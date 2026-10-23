"""Own live capture; encoding belongs to the separately installed motion CLI."""
import asyncio
import contextlib
import uuid
from zerocollab.browser.browser_recording import CANCEL_POLL_SECONDS, MAX_ERROR_CHARS, Recording, joined
from zerocollab.integrations import motion_client

CLOSING_REASONS = frozenset(('instance_closed', 'page_closed', 'viewport_changed'))


class RecordingCoordinator:
    def __init__(self, browser, page, max_seconds, fps):
        self.browser, self.page = browser, page
        self.project, self.agent = browser.project, browser.agent
        self.max_seconds, self.fps = max_seconds, fps
        self.id, self.generation = uuid.uuid4().hex[:12], uuid.uuid4().hex
        self.take = None
        self.error, self.reason = '', ''
        self.finished = self.closing = self.cancelled = False
        self.started = False
        self.stopped = asyncio.Event()
        self.job = {'phase': 'preparing', 'stage': 'capture', 'generation': self.generation}
        self.task = asyncio.create_task(self.run())

    @property
    def phase(self):
        if self.finished:
            return 'done' if self.take and self.take.phase == 'done' and not self.error else 'failed'
        return self.job['phase']

    def status(self, paths=False):
        value = self.take.status(paths=paths and self.finished and self.phase == 'done') if self.take else {
            'recording_id': self.id, 'project': self.project, 'browser': self.browser.id,
            'page': self.page.id, 'frames': 0, 'fps': {'num': self.fps, 'den': 1},
            'width': self.page.width, 'height': self.page.height}
        return {**value, 'phase': self.phase, 'stage': self.job['stage'], 'generation': self.generation,
                'error': self.error or value.get('error', '')}

    def push(self, data, metadata=None):
        if self.take:
            self.take.push(data, metadata)

    def input(self, method, params):
        if self.take:
            self.take.input(method, params)

    def check(self):
        if self.cancelled or self.browser.project != self.project or self.browser.runtime.identity != self.project:
            raise ValueError('Recording cancelled or project changed')
        if self.browser.agent != self.agent or not self.browser.status()['running']:
            raise ValueError('Browser closed during recording')

    def begin_stop(self, reason='requested'):
        self.reason = self.reason or reason
        self.closing = self.closing or reason in CLOSING_REASONS
        if self.closing:
            self.signal_cancel()
        if self.take:
            self.take.begin_stop(self.reason)
        self.stopped.set()
        return self.status()

    def signal_cancel(self):
        self.cancelled = True
        self.stopped.set()
        if self.take:
            self.take.cancelled = True
            if self.take.process and self.take.process.returncode is None:
                self.take.process.kill()
        if self.started and not self.finished and self.task is not asyncio.current_task():
            self.task.cancel()

    async def stop(self, reason='requested'):
        self.begin_stop(reason)
        await asyncio.shield(self.task)
        if self.phase != 'done':
            raise ValueError(self.error or 'Recording failed')
        return self.status(paths=True)

    async def cancel(self, reason):
        self.reason = reason
        self.signal_cancel()
        await joined(asyncio.shield(self.task))

    async def run(self):
        self.started = True
        try:
            self.check()
            argv = await motion_client.probe(self.browser.runtime.cfg)
            self.check()
            first = await self.browser.screenshot(self.page)
            self.check()
            folder = self.browser.runtime.data / 'projects' / self.project / 'recordings'
            self.take = Recording(self.browser, self.page, argv, self.max_seconds, self.fps, folder,
                                  recording_id=self.id, coordinator=self)
            self.page.recording = self
            self.job['phase'] = 'recording'
            self.take.push(first)
            await self.browser.start_stream(self.page)
            while not self.stopped.is_set():
                self.check()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.stopped.wait(), CANCEL_POLL_SECONDS)
            self.check()
            await joined(self.browser.recording_detached(self))
            await joined(self.take.join_writer())
            self.check()
            self.job.update(phase='encoding', stage='external-encode')
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
                await joined(self.browser.recording_detached(self))
                if self.take and (self.error or self.cancelled):
                    await joined(asyncio.to_thread(self.take.cleanup, False))
            except (Exception, asyncio.CancelledError) as exc:
                self.error = (str(exc) or type(exc).__name__)[:MAX_ERROR_CHARS]
            self.finished = True
