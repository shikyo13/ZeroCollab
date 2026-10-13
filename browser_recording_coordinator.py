"""One recorder owner across host admission, capture, encode and joined teardown."""
import asyncio
import contextlib
from browser_recording import CANCEL_POLL_SECONDS, MAX_ERROR_CHARS, Recording, joined, probe_transport

CLOSING_REASONS = frozenset(('instance_closed', 'page_closed', 'viewport_changed'))


class RecordingCoordinator:
    def __init__(self, browser, page, gate, max_seconds, fps):
        self.browser, self.page, self.gate = browser, page, gate
        self.project, self.agent = browser.project, browser.agent
        self.max_seconds, self.fps = max_seconds, fps
        self.take = self.lease = None
        self.error, self.reason = '', ''
        self.finished = self.closing = False
        self.stopped = asyncio.Event()
        self.job = {'project': self.project, 'owner': self.agent, 'kind': 'recording'}
        self.ticket = gate.submit(self.job, reserve_encode=True)
        self.id = self.job['id']
        # No await between submit/create/bind; shutdown always knows the full owner.
        self.task = asyncio.create_task(self.run())
        gate.bind(self.ticket, self.task)

    @property
    def phase(self):
        if self.finished:
            return 'done' if self.job['phase'] == 'done' and self.take and self.take.phase == 'done' and not self.error else 'failed'
        phase = self.job['phase']
        return 'queued' if phase == 'starting' and self.take is None else phase

    def status(self, paths=False):
        result = self.take.status(paths=paths and self.finished and self.phase == 'done') if self.take else {
            'recording_id': self.id, 'project': self.project, 'browser': self.browser.id,
            'page': self.page.id, 'started_at': None, 'width': self.page.width, 'height': self.page.height,
            'fps': {'num': self.fps, 'den': 1}, 'frames': 0, 'duration_ms': 0,
            'stop_reason': self.reason, 'error': self.error}
        status = self.gate.status(self.id, project=self.project)
        result.update(phase=self.phase, stage=self.job['stage'], generation=self.job['generation'])
        result.update({key: status[key] for key in ('position', 'waiting_reason') if key in status})
        if self.error:
            result['error'] = self.error
        return result

    def push(self, data, metadata=None):
        if self.take:
            self.take.push(data, metadata)

    def input(self, method, params):
        if self.take:
            self.take.input(method, params)

    def check(self):
        self.ticket.check_cancel()
        if (self.browser.project != self.project or self.browser.runtime.identity != self.project
                or self.browser.agent != self.agent
                or self.take is None and self.browser.pages.get(self.page.id) is not self.page
                or not self.browser.status()['running']):
            raise ValueError('Browser page or project changed during recording')

    def begin_stop(self, reason='requested'):
        if self.finished:
            return self.status(paths=True)
        self.reason = self.reason or reason
        self.closing = self.closing or reason in CLOSING_REASONS
        if self.take:
            self.take.begin_stop(self.reason)
            self.stopped.set()
            if self.closing and self.job['stage'] == 'encode' and not self.ticket.admitted:
                self.signal_cancel()
        else:
            self.signal_cancel()
        return self.status(paths=True)

    def signal_cancel(self):
        self.gate.cancel(self.id, self.job['generation'], project=self.project, actor=self.agent)
        self.stopped.set()
        if self.take:
            self.take.cancelled = True
            if self.take.process and self.take.process.returncode is None:
                self.take.process.kill()

    async def stop(self, reason='requested'):
        self.begin_stop(reason)
        # The caller's shutdown grace can expire without abandoning this bound owner.
        await asyncio.shield(self.task)
        if self.phase != 'done':
            raise ValueError(self.error or 'Recording was cancelled')
        return self.status(paths=True)

    async def cancel(self, reason):
        self.reason = reason
        if not self.finished:
            self.signal_cancel()
        await joined(asyncio.shield(self.task))

    async def run(self):
        try:
            self.lease = await self.gate.wait(self.ticket, self.check)
            self.job['phase'] = 'preparing'
            from motion_studio import require_tools, project_dir
            encoder = await joined(asyncio.to_thread(require_tools, self.browser.runtime, 'webm'))
            self.check()
            await joined(asyncio.to_thread(probe_transport, encoder['path']))
            self.check()
            first = await self.browser.screenshot(self.page)
            self.check()
            folder = project_dir(self.browser.runtime)
            if not folder:
                raise ValueError('Recording needs a project')
            self.lease = self.gate.capture(self.lease)
            self.take = Recording(self.browser, self.page, encoder['path'], self.max_seconds, self.fps,
                                  folder / 'recordings', recording_id=self.id, coordinator=self)
            self.page.recording = self
            self.take.push(first)
            await self.browser.start_stream(self.page)
            self.check()
            while not self.stopped.is_set():
                self.check()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.stopped.wait(), CANCEL_POLL_SECONDS)
            self.check()
            await joined(self.browser.recording_detached(self))
            await joined(self.take.join_writer())
            self.check()
            self.gate.handoff(self.lease)
            self.lease = None
            # Closing cannot wait behind other jobs while Browser.close holds its lock.
            if self.closing and self.ticket.lease is None:
                self.signal_cancel()
            self.lease = await self.gate.wait(self.ticket, self.check)
            self.job['phase'] = 'encoding'
            await self.take.finish()
            self.check()
            if self.take.phase != 'done':
                raise ValueError(self.take.error or 'Recording failed')
        except (Exception, asyncio.CancelledError) as exc:
            self.error = (str(exc) or type(exc).__name__)[:MAX_ERROR_CHARS]
            if self.take and self.take.phase not in ('done', 'failed'):
                self.take.cancelled = True
                self.take.begin_stop(self.reason or 'cancelled')
                await joined(self.take.finish())
        finally:
            try:
                try:
                    await joined(self.browser.recording_detached(self))
                except (Exception, asyncio.CancelledError) as exc:
                    self.error = (str(exc) or type(exc).__name__)[:MAX_ERROR_CHARS]
                if self.ticket.stop.is_set() and not self.error:
                    self.error = 'Recording was cancelled before publication'
                if self.take and (self.error or self.ticket.stop.is_set()):
                    try:
                        await joined(asyncio.to_thread(self.take.cleanup, False))
                    except (Exception, asyncio.CancelledError) as exc:
                        self.error = (str(exc) or type(exc).__name__)[:MAX_ERROR_CHARS]
            finally:
                if self.lease is not None and self.ticket.lease is self.lease:
                    self.job['frames'] = self.take.frames if self.take else 0
                    self.gate.finish(self.lease, phase='failed' if self.error else 'done', error=self.error or None)
                self.finished = True
