"""One host-owned FIFO admission gate, used by existing media lifecycle owners.

No worker is launched here. submit() registers metadata; the owner creates and
binds its task, awaits wait(), and calls finish() only AFTER owned teardown.
Pass ticket.stop into inspect's thread-owned cancellation hook. Audio/capture/
encode stages share the admitted lease; they must not call wait() again.

Recording: submit(..., reserve_encode=True), wait(), capture(lease) after probes,
then handoff(capture_lease) only after detaching capture and joining the spool
writer. Await wait(ticket) for encode, then finish(encode_lease) after cleanup.
The same id/generation survives that atomic FIFO-tail handoff.

All methods run on the host event loop. Actor/is_user are trusted runtime facts,
never scene or MCP arguments. The host must close() before its loop disappears.
"""
import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass, field
import json
import re
import threading
import time
import uuid

MAX_WAITING = 32
MAX_OWNER_WAITING = 4
SYNC_ADMISSION_WAIT_SECONDS = 1
SYNC_REPLY_WAIT_SECONDS = 10
RESULT_TTL_SECONDS = 1800
MAX_TERMINAL_JOBS = 64
MAX_RESULT_BYTES = 262144
ID = re.compile(r'^[0-9a-f]{12}$')
KINDS = frozenset(('export', 'frame', 'inspect', 'tts', 'capability', 'recording'))
TERMINAL = frozenset(('done', 'failed', 'cancelled'))
PUBLIC = ('id', 'generation', 'project', 'kind', 'owner', 'phase', 'stage',
          'created', 'finished', 'frame', 'frames', 'error')


class Cancelled(Exception):
    """The exact registered job was cancelled or invalidated."""


class QueueFull(ValueError):
    def __init__(self, limit, positions):
        self.limit, self.positions = limit, positions
        super().__init__(f'Media queue limit {limit} reached; owner positions: {positions}')


def validate_result(result):
    """Call BEFORE publishing files; returns a bounded independent JSON answer."""
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False,
                         separators=(',', ':')).encode('utf-8')
    if len(encoded) > MAX_RESULT_BYTES:
        raise ValueError(f'Media result exceeds {MAX_RESULT_BYTES} bytes')
    return json.loads(encoded)


@dataclass(eq=False)
class Ticket:
    job: dict
    gate: object = field(repr=False)
    key: tuple = field(repr=False)
    ready: asyncio.Future = field(repr=False)
    done: asyncio.Future = field(repr=False)
    stop: threading.Event = field(default_factory=threading.Event, repr=False)
    submitted: float = field(default_factory=time.monotonic, repr=False)
    lease: object = field(default=None, repr=False)
    task: object = field(default=None, repr=False)
    waited: bool = field(default=False, repr=False)
    admitted: bool = field(default=False, repr=False)
    encode_reserved: bool = field(default=False, repr=False)
    result: object = field(default=None, repr=False)
    terminal_at: float | None = field(default=None, repr=False)

    def check_cancel(self):
        if (self.stop.is_set() or self.job.get('cancel') or
                self.gate.entries.get(self.key[0]) is not self or
                (self.job.get('id'), self.job.get('generation'),
                 self.job.get('project'), self.job.get('owner')) != self.key):
            raise Cancelled('Media job was cancelled or changed')


@dataclass(frozen=True, eq=False)
class Lease:
    ticket: Ticket
    mode: str = 'heavy'


class MediaJobs:
    def __init__(self):
        # Room installation is synchronous. Bind lazily on the first operation
        # rather than making the existing installer start an extra event loop.
        self.loop = None
        self.entries = OrderedDict()
        self.queue = deque()
        self.active = None
        self.tasks = set()
        self._close_join = None
        self.closed = False
        self.generation = 0

    def _loop(self):
        current = asyncio.get_running_loop()
        if self.loop is None:
            self.loop = current
        elif current is not self.loop:
            raise RuntimeError('MediaJobs belongs to the host event loop')

    def _known(self, ticket):
        if not isinstance(ticket, Ticket) or ticket.gate is not self:
            raise ValueError('Ticket belongs to another media gate')
        if self.entries.get(ticket.key[0]) is not ticket:
            raise ValueError('Media ticket is no longer registered')

    def _lease(self, lease):
        self._loop()
        if not isinstance(lease, Lease) or self.active is not lease:
            raise ValueError('Lease is stale or belongs to another job')
        self._known(lease.ticket)

    def _trim(self):
        now = time.monotonic()
        terminal = sorted((t for t in self.entries.values() if t.terminal_at is not None),
                          key=lambda t: t.terminal_at)
        excess = max(0, len(terminal) - MAX_TERMINAL_JOBS)
        for i, ticket in enumerate(terminal):
            if i < excess or now - ticket.terminal_at >= RESULT_TTL_SECONDS:
                del self.entries[ticket.key[0]]

    def _positions(self, owner):
        return [i for i, t in enumerate(self.queue, 1) if t.key[3] == owner]

    def _pump(self):
        if not self.closed and self.active is None and self.queue:
            ticket = self.queue.popleft()
            lease = Lease(ticket)
            self.active = ticket.lease = lease
            ticket.job['phase'] = 'starting'
            ticket.ready.set_result(lease)

    def submit(self, job, *, reserve_encode=False):
        """Metadata only. Mutates id/generation/phase on caller's job record.

        One outstanding recording per host. Its future encode ticket counts
        against BOTH queue limits before any recording preparation can begin.
        """
        self._loop()
        self._trim()
        if self.closed:
            raise ValueError('The room is closing; media admission is stopped')
        if not isinstance(job, dict) or job.get('kind') not in KINDS:
            raise ValueError('Unknown media job kind')
        for name in ('project', 'owner'):
            if not isinstance(job.get(name), str) or not job[name]:
                raise ValueError(f'Media job needs {name}')
        if reserve_encode != (job['kind'] == 'recording'):
            raise ValueError('Only recordings must reserve a future encode entry')
        if reserve_encode and any(t.job['kind'] == 'recording' and t.terminal_at is None
                                  for t in self.entries.values()):
            raise ValueError('The room already has an outstanding recording')
        owner = job['owner']
        reserved = [t for t in self.entries.values() if t.encode_reserved]
        needed = 1 + int(reserve_encode)
        if len(self.queue) + len(reserved) + needed > MAX_WAITING:
            raise QueueFull(MAX_WAITING, self._positions(owner))
        if len(self._positions(owner)) + sum(t.key[3] == owner for t in reserved) + needed > MAX_OWNER_WAITING:
            raise QueueFull(MAX_OWNER_WAITING, self._positions(owner))
        identity = job.get('id')
        if identity is None:
            identity = uuid.uuid4().hex[:12]
            while identity in self.entries:
                identity = uuid.uuid4().hex[:12]
        if not isinstance(identity, str) or not ID.fullmatch(identity) or identity in self.entries:
            raise ValueError('Media job id is invalid or already registered')
        generation = job.get('generation')
        if generation is not None and (type(generation) is not int or generation < 1):
            raise ValueError('Media generation must be a positive integer')
        self.generation = max(self.generation + 1, generation or 0)
        generation = generation or self.generation
        job.update(id=identity, generation=generation, phase='queued', cancel=False)
        job.setdefault('created', time.time())
        job.setdefault('stage', 'prepare' if reserve_encode else job['kind'])
        key = (identity, generation, job['project'], owner)
        ticket = Ticket(job, self, key, self.loop.create_future(), self.loop.create_future(),
                        encode_reserved=reserve_encode)
        self.entries[identity] = ticket
        self.queue.append(ticket)
        self._pump()
        return ticket

    def bind(self, ticket, task):
        """Strongly retain the existing owner's task; never create a task here."""
        self._loop()
        self._known(ticket)
        if not isinstance(task, asyncio.Task) or task.get_loop() is not self.loop or ticket.task is not None:
            raise ValueError('Bind exactly one owned task on the host loop')
        ticket.task = task
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def wait(self, ticket, check_cancel=None):
        """Await one grant. Caller cancellation before return cannot strand it."""
        self._loop()
        self._known(ticket)
        if ticket.waited:
            raise ValueError('Nested or duplicate media admission is forbidden')
        ticket.waited = True
        try:
            ticket.check_cancel()
            if check_cancel:
                check_cancel()
            lease = await asyncio.shield(ticket.ready)
            ticket.check_cancel()
            if check_cancel:
                check_cancel()
            ticket.admitted = True
            return lease
        except BaseException:
            # No owned stage has begun through this wait() yet. A grant raced
            # with cancellation, or was allocated before the owner awaited it.
            self._cancel(ticket)
            if ticket.lease is not None:
                self.finish(ticket.lease, phase='cancelled')
            raise

    def capture(self, lease):
        """Convert joined recording preparation to an exclusive capture hold."""
        self._lease(lease)
        ticket = lease.ticket
        ticket.check_cancel()
        if lease.mode != 'heavy' or not ticket.encode_reserved:
            raise ValueError('Only recording preparation can become capture')
        capture = Lease(ticket, 'capture')
        self.active = ticket.lease = capture
        ticket.job.update(phase='recording', stage='capture')
        return capture

    def handoff(self, lease):
        """After detach/drain: atomically append encode, release capture, pump."""
        self._lease(lease)
        ticket = lease.ticket
        ticket.check_cancel()
        if lease.mode != 'capture' or not ticket.encode_reserved:
            raise ValueError('Only capture has a reserved encode handoff')
        ticket.encode_reserved = False
        ticket.lease = None
        ticket.waited = False
        ticket.admitted = False
        ticket.ready = self.loop.create_future()
        ticket.job.update(phase='queued', stage='encode')
        self.queue.append(ticket)
        self.active = None
        self._pump()
        return ticket

    def _terminal(self, ticket, phase, result=None, error=None):
        ticket.encode_reserved = False
        ticket.result = result
        ticket.job.update(phase=phase, finished=time.time())
        if error:
            ticket.job['error'] = str(error)[:2000]
        ticket.terminal_at = time.monotonic()
        if not ticket.done.done():
            ticket.done.set_result(None)
        if not ticket.ready.done():
            ticket.ready.set_result(None)

    def finish(self, lease, *, phase='done', result=None, error=None):
        """Terminal transition and release, only after owned work/cleanup ends.

        validate_result() must have been called before any external publication.
        This defensive second check prevents an oversized retained answer.
        """
        self._lease(lease)
        if phase not in TERMINAL:
            raise ValueError('Media job needs a terminal outcome')
        ticket = lease.ticket
        if ticket.stop.is_set() or ticket.job.get('cancel'):
            phase, result = 'cancelled', None
        elif result is not None:
            try:
                result = validate_result(result)
            except (ValueError, TypeError) as exc:
                phase, result, error = 'failed', None, exc
        self._terminal(ticket, phase, result, error)
        ticket.lease = None
        self.active = None
        self._pump()
        self._trim()

    def release(self, lease):
        """Finish a job with no retained answer; never use between stages."""
        self.finish(lease)

    def _cancel(self, ticket):
        if ticket.terminal_at is not None:
            return
        ticket.stop.set()
        ticket.job['cancel'] = True
        if ticket.lease is None:
            self.queue.remove(ticket)
            self._terminal(ticket, 'cancelled')
            self._pump()
        elif not ticket.admitted:
            # A granted future is not yet an executing owner. Cancellation can
            # otherwise strand a slot when the bound task never starts.
            self.finish(ticket.lease, phase='cancelled')

    def cancel(self, job_id, generation, *, project, actor, is_user=False):
        """Request cancellation; active owners keep the slot through teardown."""
        self._loop()
        self._trim()
        ticket = self.entries.get(job_id)
        if ticket is None or ticket.key[2] != project:
            raise ValueError('Media job is unavailable or expired in this project')
        if ticket.key[1] != generation:
            raise ValueError('Media generation changed')
        if not is_user and ticket.key[3] != actor:
            raise ValueError('Only the submitting agent or user can cancel this job')
        self._cancel(ticket)
        self._trim()
        return self.status(job_id, project=project)

    def cancel_project(self, project):
        self._loop()
        for ticket in list(self.entries.values()):
            if ticket.key[2] == project:
                self._cancel(ticket)
        self._trim()

    def invalidate_project(self, project):
        """Signal old-project owners before the runtime changes its identity."""
        self.cancel_project(project)

    def status(self, job_id, *, project):
        self._loop()
        self._trim()
        ticket = self.entries.get(job_id)
        if ticket is None or ticket.key[2] != project:
            return {'job_id': job_id, 'phase': 'unavailable', 'error': 'Media job is unavailable or expired in this project'}
        answer = {k: ticket.job[k] for k in PUBLIC if k in ticket.job}
        answer['job_id'] = ticket.key[0]
        if ticket in self.queue:
            answer.update(position=list(self.queue).index(ticket) + 1,
                          waiting_reason='Waiting for earlier media jobs')
        if ticket.stop.is_set() and ticket.terminal_at is None:
            answer['cancelling'] = True
        if ticket.result is not None:
            answer['result'] = validate_result(ticket.result)
        return answer

    def snapshot(self, project):
        self._loop()
        self._trim()
        return [self.status(t.key[0], project=project) for t in list(self.entries.values())
                if t.key[2] == project]

    async def reply(self, ticket):
        """Bounded tool reply. Neither timeout cancels the owner or its grant."""
        self._loop()
        self._known(ticket)
        async def within(future, budget):
            remaining = ticket.submitted + budget - time.monotonic()
            if remaining <= 0:
                return False
            try:
                await asyncio.wait_for(asyncio.shield(future), remaining)
                return True
            except asyncio.TimeoutError:
                return False
        if await within(ticket.ready, SYNC_ADMISSION_WAIT_SECONDS):
            await within(ticket.done, SYNC_REPLY_WAIT_SECONDS)
        return self.status(ticket.key[0], project=ticket.key[2])

    def begin_close(self):
        """First shutdown hook: stop admission and signal, without joining.

        Exporter/browser shutdown hooks can now tear down their existing owners
        without waiting for media admission. close() performs the final join.
        """
        self._loop()
        self.closed = True
        for ticket in list(self.entries.values()):
            self._cancel(ticket)
        self._trim()

    async def close(self):
        """Idempotent final shutdown join, after owner-specific close hooks."""
        self.begin_close()
        if self._close_join is None:
            tasks = [task for task in self.tasks if not task.done()]
            if asyncio.current_task() in tasks:
                raise RuntimeError('An owned media task cannot close its own host gate')
            for task in tasks:
                task.cancel()
            self._close_join = asyncio.gather(*tasks, return_exceptions=True)
        joining = self._close_join
        interrupted = False
        while not joining.done():
            try:
                await asyncio.shield(joining)
            except asyncio.CancelledError:
                interrupted = True
        joining.result()
        if self.active is not None:
            raise RuntimeError('Media owner did not finish owned teardown before gate close')
        if interrupted:
            raise asyncio.CancelledError
