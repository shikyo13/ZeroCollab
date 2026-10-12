"""Bounded ordered frame capture and content-addressed receipts for Motion S3.

The caller supplies owned browser contexts and the job-id/generation cancellation
check. This module never launches a service or publishes an export. A worker's
capture(frame) returns PNG bytes; sink(frame, png, receipt) accepts frames in order.
"""
import asyncio
import base64
import hashlib
import json
import os
import tempfile
from collections import deque
from contextlib import AsyncExitStack
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

PNG = b'\x89PNG\r\n\x1a\n'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


@dataclass(frozen=True)
class FrameClock:
    num: int
    den: int = 1

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in (self.num, self.den)):
            raise ValueError('fps requires positive integer num and den')
        rate = Fraction(self.num, self.den)
        if not 1 <= rate <= 240:
            raise ValueError('fps must be between 1 and 240')
        object.__setattr__(self, 'num', rate.numerator)
        object.__setattr__(self, 'den', rate.denominator)

    def ms(self, frame):
        if type(frame) is not int or frame < 0:
            raise ValueError('frame must be a nonnegative integer')
        return Fraction(frame * 1000 * self.den, self.num)


@dataclass(frozen=True)
class RenderIdentity:
    """Hashes must describe resolved inputs, including actual asset bytes.

    renderer includes the renderer code, browser version and rendering options.
    revision is motion_scene.revision(); assets hashes the resolved asset manifest.
    Job ids deliberately do not affect reuse. Props/seed are part of revision.
    """
    composition: str
    revision: str
    assets: str
    renderer: str
    width: int
    height: int
    clock: FrameClock

    def __post_init__(self):
        for name in ('revision', 'assets', 'renderer'):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError(f'{name} must be a sha256')
        if not isinstance(self.composition, str) or not self.composition:
            raise ValueError('composition id is required')
        if any(type(v) is not int or not 16 <= v <= 7680 for v in (self.width, self.height)):
            raise ValueError('frame size must be between 16 and 7680')

    @property
    def key(self):
        value = {'composition': self.composition, 'revision': self.revision,
                 'assets': self.assets, 'renderer': self.renderer,
                 'width': self.width, 'height': self.height,
                 'fps': {'num': self.clock.num, 'den': self.clock.den}}
        return hashlib.sha256(canonical(value)).hexdigest()


def receipt(identity, frame, png):
    return {'version': 1, 'render': identity.key, 'frame': frame,
            'sha256': hashlib.sha256(png).hexdigest(), 'bytes': len(png)}


def atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class FrameCache:
    def __init__(self, root, identity):
        self.identity = identity
        self.root = Path(root) / identity.key

    def paths(self, frame):
        self.identity.clock.ms(frame)  # validate before deriving paths
        return self.root / f'{frame}.png', self.root / f'{frame}.json'

    def read(self, frame):
        image, metadata = self.paths(frame)
        try:
            saved = json.loads(metadata.read_bytes())
            png = image.read_bytes()
            if png.startswith(PNG) and saved == receipt(self.identity, frame, png):
                return png, saved
        except (OSError, ValueError, TypeError):
            pass
        return None

    def write(self, frame, png):
        if not isinstance(png, bytes) or not png.startswith(PNG):
            raise ValueError('capture did not return PNG bytes')
        image, metadata = self.paths(frame)
        saved = receipt(self.identity, frame, png)
        # Publish the receipt last. A crash between replacements leaves a miss,
        # or the old identical frame, never an unchecked reusable image.
        atomic_bytes(image, png)
        atomic_bytes(metadata, canonical(saved))
        return saved


async def file_io(function, *args):
    """Join an already-running filesystem operation before context teardown."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class ChromiumWorker:
    """Owned headless capture context using the existing renderer's lifecycle.

    page is the Exporter's generated local HTML, never an agent/browser URL.
    profile must be a fresh per-worker directory under the job's work directory.
    The current page seam is __ready/__error/__seek(ms). v2 pages must expose that
    same seek seam or supply a separate frame adapter after S1/S5 are integrated.
    legacy_ms keeps v1's four-decimal seek rounding for pixel comparisons.
    """
    def __init__(self, page, profile, identity, check_cancel, *, legacy_ms=False):
        self.page, self.profile = Path(page).resolve(), Path(profile).resolve()
        self.identity, self.check_cancel = identity, check_cancel
        self.legacy_ms = legacy_ms
        self.proc = self.ws = None
        self.pid = None
        self.browser_version = None
        self.serial = 0
        self.timeout = 30

    async def __aenter__(self):
        # Cleanup covers allocation before readiness, not just successful entry.
        try:
            await self.start()
            return self
        except BaseException:
            await self.close()
            raise

    async def __aexit__(self, *exc):
        await self.close()

    async def start(self):
        import httpx
        import websockets
        import shared_browser
        from motion_studio import RENDER_FLAGS, CALL_TIMEOUT

        self.timeout = CALL_TIMEOUT
        self.check_cancel()
        if not self.page.is_file():
            raise ValueError('render page must be a local HTML file')
        executable = shared_browser.executable()
        if not executable:
            raise RuntimeError('No capture browser is installed')
        self.profile.mkdir(parents=True, exist_ok=False)
        flags = [f for f in RENDER_FLAGS if not f.startswith('--window-size=')]
        flags.append(f'--window-size={self.identity.width},{self.identity.height}')
        args = [executable, *flags, f'--user-data-dir={self.profile}', 'about:blank']
        if os.name == 'nt':
            self.proc = shared_browser.WindowsBrowserProcess(args)
        else:
            spawning = asyncio.create_task(asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL))
            try:
                self.proc = await asyncio.shield(spawning)
            except asyncio.CancelledError:
                self.proc = await spawning
                raise
        self.pid = self.proc.pid
        marker = self.profile / 'DevToolsActivePort'
        for _ in range(150):
            self.check_cancel()
            if marker.exists() and marker.read_text().strip():
                break
            code = self.proc.returncode
            if code is not None and not (os.name == 'nt' and code == 0):
                raise RuntimeError('The browser exited during startup')
            await asyncio.sleep(.1)
        else:
            raise RuntimeError('The browser did not start within 15 seconds')
        port = int(marker.read_text().splitlines()[0])
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            pages = (await client.get(f'http://127.0.0.1:{port}/json/list')).json()
            version = (await client.get(f'http://127.0.0.1:{port}/json/version')).json()
            self.browser_version = version.get('Browser')
        self.check_cancel()
        url = next(p['webSocketDebuggerUrl'] for p in pages if p['type'] == 'page')
        self.ws = await asyncio.wait_for(websockets.connect(url, max_size=64 * 1024 * 1024), self.timeout)
        await self.call('Network.enable')
        await self.call('Network.setBlockedURLs', urls=['http://*', 'https://*', 'ws://*', 'wss://*'])
        await self.call('Emulation.setDeviceMetricsOverride', width=self.identity.width,
                        height=self.identity.height, deviceScaleFactor=1, mobile=False)
        await self.call('Page.navigate', url=self.page.as_uri())
        for _ in range(400):
            self.check_cancel()
            state = await self.evaluate('({ready:window.__ready===true,error:window.__error||null})')
            if state and state['error']:
                raise RuntimeError(state['error'])
            if state and state['ready']:
                return
            await asyncio.sleep(.025)
        raise RuntimeError('The scene did not become ready within 10 seconds')

    async def call(self, method, **params):
        self.check_cancel()
        self.serial += 1
        mid = self.serial
        await self.ws.send(json.dumps({'id': mid, 'method': method, 'params': params}))

        async def reply():
            while True:
                message = json.loads(await self.ws.recv())
                if message.get('id') == mid:
                    return message

        try:
            message = await asyncio.wait_for(reply(), self.timeout)
        except asyncio.TimeoutError:
            raise RuntimeError(f'The browser stopped responding ({method})') from None
        self.check_cancel()
        if 'error' in message:
            raise RuntimeError(f'{method}: {message["error"].get("message")}')
        return message.get('result', {})

    async def evaluate(self, expression, wait=False):
        result = await self.call('Runtime.evaluate', expression=expression,
                                 awaitPromise=wait, returnByValue=True)
        if 'exceptionDetails' in result:
            raise RuntimeError(result['exceptionDetails'].get('text') or 'scene script error')
        return result.get('result', {}).get('value')

    async def capture(self, frame):
        ms = float(self.identity.clock.ms(frame))
        if self.legacy_ms:
            ms = round(ms, 4)
        return await self.capture_ms(ms)

    async def capture_ms(self, ms):
        if isinstance(ms, bool) or not isinstance(ms, (int, float)) or not 0 <= ms < float('inf'):
            raise ValueError('seek time must be a finite nonnegative number')
        await self.evaluate(f'window.__seek({ms!r})', wait=True)
        shot = await self.call('Page.captureScreenshot', format='png', fromSurface=True,
                               clip={'x': 0, 'y': 0, 'width': self.identity.width,
                                     'height': self.identity.height, 'scale': 1})
        return base64.b64decode(shot['data'], validate=True)

    async def close(self):
        async def cleanup():
            try:
                if self.ws is not None:
                    await asyncio.wait_for(self.ws.close(), 2)
            finally:
                self.ws = None
                proc, self.proc = self.proc, None
                if proc is not None:
                    if os.name == 'nt':
                        try:
                            if proc.returncode is None:
                                proc.terminate()
                            await asyncio.wait_for(proc.wait(), 5)
                        finally:
                            proc.close_handles()  # closes the kill-on-close job, including children
                    elif proc.returncode is None:
                        proc.kill()
                        await proc.wait()

        cleaning = asyncio.create_task(cleanup())
        try:
            await asyncio.shield(cleaning)
        except asyncio.CancelledError:
            await cleaning
            raise


async def render_frames(identity, frames, worker_factory, sink, check_cancel,
                        *, concurrency=2, cache=None):
    """Capture a contiguous range with at most concurrency outstanding PNGs.

    worker_factory(slot) returns an async context manager with capture(frame).
    Workers start lazily on cache misses and close on success, cancellation or
    failure. check_cancel raises the caller's cancellation exception for a
    changed job id/generation. The returned count includes cache hits.

    A worker MUST clean up partially acquired resources if __aenter__ raises or
    is cancelled, before returning control: AsyncExitStack registers __aexit__
    only after entry succeeds. Put launch/readiness inside try/finally (including
    the yield for an asynccontextmanager). Entry runs in a capture task and exit
    in the parent; workers must not use task-bound scopes across those calls.
    """
    if not isinstance(frames, range) or frames.step != 1 or frames.start < 0:
        raise ValueError('frames must be a nonnegative ascending range')
    if type(concurrency) is not int or not 1 <= concurrency <= 8:
        raise ValueError('concurrency must be between 1 and 8')
    if cache is not None and cache.identity != identity:
        raise ValueError('cache belongs to another render identity')
    pending = deque()
    workers = [None] * concurrency
    source = iter(frames)
    count = 0

    async with AsyncExitStack() as contexts:
        async def capture(frame, slot):
            check_cancel()
            found = await file_io(cache.read, frame) if cache else None
            check_cancel()
            if found is not None:
                return found
            if workers[slot] is None:
                workers[slot] = await contexts.enter_async_context(worker_factory(slot))
            check_cancel()
            png = await workers[slot].capture(frame)
            check_cancel()
            if not isinstance(png, bytes) or not png.startswith(PNG):
                raise ValueError('capture did not return PNG bytes')
            saved = await file_io(cache.write, frame, png) if cache else receipt(identity, frame, png)
            check_cancel()
            return png, saved

        def enqueue(slot):
            frame = next(source, None)
            if frame is not None:
                pending.append((frame, slot, asyncio.create_task(capture(frame, slot))))

        async def guarded(task):
            while not task.done():
                check_cancel()
                # A later worker can fail while the first frame is still slow.
                # Surface it now rather than retaining the rest until timeout.
                for _, _, other in pending:
                    if other.done() and not other.cancelled() and other.exception() is not None:
                        other.result()
                await asyncio.wait([task], timeout=0.1)
            check_cancel()
            return task.result()

        writing = None
        try:
            check_cancel()
            for slot in range(concurrency):
                enqueue(slot)
            while pending:
                frame, slot, task = pending[0]
                png, saved = await guarded(task)
                writing = asyncio.create_task(sink(frame, png, saved))
                await guarded(writing)
                writing = None
                pending.popleft()
                count += 1
                enqueue(slot)
            check_cancel()
            return count
        finally:
            tasks = [t for _, _, t in pending]
            if writing is not None:
                tasks.append(writing)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
