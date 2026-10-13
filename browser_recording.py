"""Owned browser takes: timestamped JPEG spool, fixed WebM and content-free input.

No upload publication, caller paths, encoder selection or motion-layer authoring.
"""
import asyncio
import bisect
import contextlib
import json
import math
import os
import subprocess
import time
import uuid
import logging
from pathlib import Path

from browser_recording_transport import concat_file, encode_args, encoded_frames, probe_transport

MAX_BYTES = 512 * 1024 * 1024
MAX_PENDING_BYTES = 32 * 1024 * 1024
MAX_PENDING_FRAMES = 120
MAX_RECORD_SECONDS = 120
DEFAULT_RECORD_SECONDS = 60
DEFAULT_RECORD_FPS = 60
SUPPORTED_RECORD_FPS = (30, 60)
MAX_RETAINED_RECORDINGS = 8
MAX_SOURCE_SAMPLES = 30000
MAX_INPUT_EVENTS = 20000
CURSOR_SAMPLE_HZ = 30
MS_PER_SECOND = 1000
MAX_TIMESTAMP_SKEW_MS = 2000
MIN_TIMESTAMP_STEP_MS = .001
MAX_ENCODE_SECONDS = 300
ENCODE_SECONDS_PER_CAPTURE_SECOND = 10
ENCODE_TIMEOUT_HEADROOM_SECONDS = 30
MIN_CONTAINER_BYTES = 32
MAX_ERROR_CHARS = 500
MAX_SCROLL_DELTA = 100000
LOGGER = logging.getLogger(__name__)


async def joined(awaitable):
    """Do not abandon owned work when the caller is cancelled, even repeatedly."""
    task = asyncio.ensure_future(awaitable)
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    result = task.result()
    if interrupted:
        raise asyncio.CancelledError
    return result


class Recording:
    def __init__(self, browser, page, encoder, max_seconds, fps, folder):
        self.browser, self.page, self.encoder = browser, page, encoder
        self.id = uuid.uuid4().hex[:12]
        self.project, self.browser_id, self.page_id = browser.project, browser.id, page.id
        self.width, self.height, self.fps = page.width, page.height, fps
        self.started_at, self.started = time.time(), time.monotonic()
        self.folder = folder / self.id
        self.folder.mkdir(parents=True, exist_ok=False)
        self.max_seconds, self.phase, self.reason = max_seconds, 'recording', ''
        self.queue = asyncio.Queue(maxsize=MAX_PENDING_FRAMES)
        self.pending_bytes = self.bytes = self.source_count = self.dropped = 0
        self.samples, self.events = [], []
        self.spool_files = []
        self.writer_ending = False
        self.clock_anchor, self.timing_source = None, 'arrival'
        self.last_t = self.last_move = -1.
        self.pointer = (0., 0.)
        self.error = ''
        self.cancelled = False
        self.duration_ms = self.frames = 0
        self.source_used = self.repeated = self.resampled = 0
        self.process = self.finish_task = None
        self.writer = asyncio.create_task(self.write_frames())
        self.timer = asyncio.create_task(self.deadline())
        LOGGER.info('Browser recording %s started for page %s', self.id, self.page_id)

    def elapsed(self):
        return max(0., (time.monotonic() - self.started) * MS_PER_SECOND)

    def push(self, data, metadata=None):
        if self.phase != 'recording':
            return
        self.source_count += 1
        at = self.elapsed()
        stamp = (metadata or {}).get('timestamp')
        if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and math.isfinite(stamp):
            if self.clock_anchor is None:
                self.clock_anchor = (stamp, at)
            candidate = self.clock_anchor[1] + (stamp-self.clock_anchor[0])*MS_PER_SECOND
            if candidate >= self.last_t and abs(candidate-at) < MAX_TIMESTAMP_SKEW_MS:
                at, self.timing_source = candidate, 'cdp_timestamp'
        at = max(at, self.last_t + MIN_TIMESTAMP_STEP_MS)
        self.last_t = at
        if (self.queue.full() or len(self.samples) + self.queue.qsize() >= MAX_SOURCE_SAMPLES or self.pending_bytes + len(data) > MAX_PENDING_BYTES or
                self.bytes + self.pending_bytes + len(data) > MAX_BYTES):
            self.dropped += 1
            if self.bytes + self.pending_bytes + len(data) > MAX_BYTES:
                self.begin_stop('spool_limit')
            return
        self.pending_bytes += len(data)
        self.queue.put_nowait((at, data))

    async def write_frames(self):
        while True:
            sample = await self.queue.get()
            if sample is None:
                return
            at, data = sample
            try:
                name = f'{len(self.samples):08d}.jpg'
                self.spool_files.append(name)
                await joined(asyncio.to_thread((self.folder / name).write_bytes, data))
                self.samples.append((at, name))
                self.bytes += len(data)
            except OSError as exc:
                self.error = str(exc)[:MAX_ERROR_CHARS]
                self.begin_stop('spool_error')
            finally:
                self.pending_bytes -= len(data)

    def input(self, method, params):
        if self.phase != 'recording' or len(self.events) >= MAX_INPUT_EVENTS:
            return
        at = self.elapsed()
        event = {'t_ms': round(at, 3), 'frame': int(at*self.fps/MS_PER_SECOND)}
        if method == 'Input.dispatchMouseEvent':
            kind = {'mouseMoved': 'move', 'mousePressed': 'down',
                    'mouseReleased': 'up', 'mouseWheel': 'scroll'}.get(params.get('type'))
            if not kind:
                return
            if kind == 'move' and at - self.last_move < MS_PER_SECOND/CURSOR_SAMPLE_HZ:
                return
            if kind == 'move':
                self.last_move = at
            try:
                x, y = float(params.get('x', 0)), float(params.get('y', 0))
                dx, dy = float(params.get('deltaX', 0)), float(params.get('deltaY', 0))
            except (TypeError, ValueError):
                return
            if not all(math.isfinite(value) for value in (x, y, dx, dy)):
                return
            self.pointer = (min(self.width, max(0., x)), min(self.height, max(0., y)))
            event.update(type=kind, x=self.pointer[0], y=self.pointer[1])
            if kind in ('down', 'up'):
                event['button'] = params.get('button', 'left')
            if kind == 'scroll':
                event.update(dx=max(-MAX_SCROLL_DELTA, min(MAX_SCROLL_DELTA, dx)),
                             dy=max(-MAX_SCROLL_DELTA, min(MAX_SCROLL_DELTA, dy)))
            self.events.append(event)
            if kind == 'up' and len(self.events) < MAX_INPUT_EVENTS:
                self.events.append({**event, 'type': 'click'})
            return
        if method == 'Input.insertText' or (method == 'Input.dispatchKeyEvent' and
                                           params.get('type') in ('keyDown', 'rawKeyDown')):
            # Deliberately do not copy printable key, code, text, length or field data.
            event.update(type='key', x=self.pointer[0], y=self.pointer[1])
            key = params.get('key')
            if key in ('Enter', 'Tab', 'Escape', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown',
                       'Home', 'End', 'PageUp', 'PageDown'):
                event['key'] = key
            self.events.append(event)

    async def deadline(self):
        await asyncio.sleep(self.max_seconds)
        self.begin_stop('duration_limit')

    def status(self, paths=False):
        result = {'recording_id': self.id, 'project': self.project, 'browser': self.browser_id,
                  'page': self.page_id, 'phase': self.phase, 'started_at': self.started_at,
                  'width': self.width, 'height': self.height, 'device_scale': 1,
                  'fps': {'num': self.fps, 'den': 1}, 'frames': self.frames,
                  'duration_ms': self.duration_ms, 'source_count': self.source_count,
                  'dropped_frames': self.dropped, 'timing_source': self.timing_source,
                  'source_frames_used': self.source_used, 'repeated_frames': self.repeated,
                  'resampled_frames': self.resampled,
                  'partial': self.reason not in ('', 'requested', 'duration_limit'),
                  'stop_reason': self.reason, 'error': self.error}
        if paths and self.phase == 'done':
            result.update(path=str(self.folder / 'clip.webm'), sidecar_path=str(self.folder / 'sidecar.json'))
        return result

    def begin_stop(self, reason='requested'):
        """Atomically initiate finalization; tool callers poll without holding a lock."""
        if self.finish_task is None:
            self.reason = reason
            self.duration_ms = max(1, math.ceil(min(self.elapsed(), self.max_seconds*MS_PER_SECOND)))
            self.phase = 'encoding'
            if self.timer is not asyncio.current_task():
                self.timer.cancel()
            self.finish_task = asyncio.create_task(self.finish())
        return self.status(paths=True)

    async def stop(self, reason='requested'):
        """Internal lifecycle join, used with the browser shutdown grace period."""
        self.begin_stop(reason)
        await asyncio.shield(self.finish_task)
        if self.phase != 'done':
            raise ValueError(self.error or 'Recording failed')
        return self.status(paths=True)

    async def cancel(self, reason):
        self.cancelled, self.reason = True, reason
        if self.process and self.process.returncode is None:
            self.process.kill()
        with contextlib.suppress(ValueError):
            await self.stop(reason)

    def check_cancelled(self):
        if self.cancelled:
            raise ValueError('Recording was cancelled before publication')

    async def finish(self):
        completed = False
        try:
            await self.browser.recording_detached(self)
            await self.queue.put(None)
            self.writer_ending = True
            await asyncio.shield(self.writer)
            self.check_cancelled()
            if self.error:
                raise ValueError('Recording spool failed: ' + self.error)
            await joined(asyncio.to_thread(concat_file, self.folder, self.samples, self.duration_ms))
            self.check_cancelled()
            expected = max(1, math.ceil(self.duration_ms*self.fps/MS_PER_SECOND))
            times = [at for at, _ in self.samples if at < self.duration_ms]
            if not times:
                raise ValueError('Recording has no usable frames before its stop time')
            times[0] = 0
            used = {max(0, bisect.bisect_right(times, i*MS_PER_SECOND/self.fps)-1) for i in range(expected)}
            self.source_used = len(used)
            self.repeated = expected-self.source_used
            self.resampled = len(times)-self.source_used
            partial = self.folder / 'clip.partial.webm'
            args = encode_args(self.encoder, self.folder, self.fps, expected, partial)
            self.check_cancelled()
            if os.name == 'nt':
                from shared_browser import WindowsBrowserProcess
                self.process = WindowsBrowserProcess(args, priority_class=subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            else:
                await joined(self.spawn_posix(args))
            self.check_cancelled()
            code = await asyncio.wait_for(self.process.wait(), min(MAX_ENCODE_SECONDS, self.max_seconds*ENCODE_SECONDS_PER_CAPTURE_SECOND+ENCODE_TIMEOUT_HEADROOM_SECONDS))
            self.check_cancelled()
            self.frames = await joined(asyncio.to_thread(encoded_frames, self.folder))
            self.check_cancelled()
            if code or self.frames != expected or not partial.is_file() or partial.stat().st_size < MIN_CONTAINER_BYTES:
                raise ValueError(f'Recording encoder failed (code {code}, {self.frames}/{expected} frames)')
            self.events = [event for event in self.events if event['frame'] < self.frames]
            partial.replace(self.folder / 'clip.webm')
            sidecar = {'version': 1, **self.status(), 'phase': 'done', 'expected_frames': expected, 'events': self.events,
                       'source_timestamps_ms': [round(at, 3) for at, _ in self.samples]}
            await joined(asyncio.to_thread((self.folder / 'sidecar.json').write_text,
                                          json.dumps(sidecar, ensure_ascii=False), encoding='utf-8'))
            self.check_cancelled()
            completed = True
        except (Exception, asyncio.CancelledError) as exc:
            self.fail(exc)
        finally:
            # Each cleanup step runs even if another raises unexpectedly. No finalizer
            # exception may strand the take in encoding or escape browser shutdown.
            for step in (self.close_encoder, self.join_writer):
                try:
                    await joined(step())
                except (Exception, asyncio.CancelledError) as exc:
                    self.fail(exc)
            try:
                await joined(asyncio.to_thread(self.cleanup, completed and not self.error and not self.cancelled))
            except (Exception, asyncio.CancelledError) as exc:
                self.fail(exc)
            self.samples.clear()
            self.events.clear()
            self.spool_files.clear()
            try:
                await joined(self.browser.recording_detached(self))
            except (Exception, asyncio.CancelledError) as exc:
                self.fail(exc)
            if self.cancelled and not self.error:
                self.error = 'Recording was cancelled before publication'
                try:
                    await joined(asyncio.to_thread(self.cleanup, False))
                except (Exception, asyncio.CancelledError) as exc:
                    self.fail(exc)
            self.phase = 'done' if completed and not self.error and not self.cancelled else 'failed'
            if self.phase == 'done':
                LOGGER.info('Browser recording %s finalized: frames=%s duration_ms=%s', self.id, self.frames, self.duration_ms)

    def fail(self, exc):
        self.error = (str(exc) or type(exc).__name__)[:MAX_ERROR_CHARS]
        LOGGER.warning('Browser recording %s failed', self.id, exc_info=True)

    async def spawn_posix(self, args):
        # Assignment is part of the joined operation, so cancellation cannot lose
        # the process handle between subprocess creation and owned teardown.
        self.process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL,
                                                           stderr=asyncio.subprocess.DEVNULL)

    async def close_encoder(self):
        if self.process:
            try:
                if self.process.returncode is None:
                    self.process.kill()
                    await self.process.wait()
            finally:
                if hasattr(self.process, 'close_handles'):
                    self.process.close_handles()

    async def join_writer(self):
        if not self.writer.done() and not self.writer_ending:
            await self.queue.put(None)
            self.writer_ending = True
        await asyncio.shield(self.writer)

    def cleanup(self, keep_original):
        temporary = [self.folder / name for name in self.spool_files]
        temporary += [self.folder / name for name in ('input.ffconcat', 'progress.txt', 'clip.partial.webm')]
        if not keep_original:
            temporary += [self.folder / name for name in ('clip.webm', 'sidecar.json')]
        for path in temporary:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning('Could not remove recording temporary file %s', path, exc_info=True)
