"""Whether any open room tab may be mid-edit, for the deployment typing guard (frontend 7cfd8ed6, Astra 26a91642,
design 7712dc49). Tabs send booleans only: no text, drafts, ids or times. Every connected /ws socket counts, from
app.ws_clients; a socket that has not reported, or has been silent too long, counts as busy. All methods run on the
room's event loop, as /ws messages and HTTP mutations do, so ready() and the write-gate closure that follows it in
the same synchronous step cannot be interleaved by a report.

Wire, client to server, handled before the deployment gate like ping, answered only on that socket:
  {"type": "ui_composing", "busy": <bool>}                   on open, on every change and every HEARTBEAT_SECONDS
  {"type": "deployment_prepare_ack", "id": <id>, "busy": <bool>}   to the attempt this socket was sent
Server to client:
  {"type": "deployment_prepare", "id": <id>}     lock human editors and actions, keep drafts, recompute, ack
  {"type": "deployment_release", "id": <id>}     unlock and report again (sent to every connected tab)
  {"type": "deployment_state", "id": <id>|null}  on connect: the attempt still outstanding, if any. A tab that
                                                 reconnects holding a lock for any other id (or null) unlocks and
                                                 reports; one holding this id stays locked until its release.
"""
import asyncio
import json
import re
import time

HEARTBEAT_SECONDS = 5  # how often a tab reports; the frontend reporter's interval
STALE_SECONDS = 15  # a connected tab silent for longer counts as busy (hidden and suspended tabs included)
PREPARE_ACK_SECONDS = 10  # the default bound on waiting for one attempt's acknowledgements
ACK_POLL_SECONDS = 0.1
MAX_ATTEMPTS = 8  # attempts kept for late acknowledgements; older ones are dropped
ATTEMPT_ID = re.compile(r'[A-Za-z0-9_-]{1,64}')
REPORT, PREPARE, ACK, RELEASE = 'ui_composing', 'deployment_prepare', 'deployment_prepare_ack', 'deployment_release'
STATE = 'deployment_state'


class Composing:
    def __init__(self, clients, send):
        self.clients = clients  # app.ws_clients: the authority on which tabs are connected
        self.send = send  # async (socket, text), with the socket's own send lock and timeout
        self.reports = {}  # socket -> (busy, monotonic receipt time)
        self.attempts = {}  # attempt id -> {'sockets': frozenset, 'acks': {socket: busy}}

    def connected(self, socket):
        self.reports.pop(socket, None)  # unknown until it reports

    async def greet(self, socket):
        """Tell a (re)connected tab which attempt is still outstanding, so a lock it kept across a disconnect for
        an attempt that has since ended can be released (frontend 1d505512)."""
        active = next(reversed(self.attempts), None)
        await self.send(socket, json.dumps({'type': STATE, 'id': active}))

    def gone(self, socket):
        self.reports.pop(socket, None)

    def receive(self, socket, event):
        """True when the event is one of these messages (valid or not): nothing else may handle it."""
        kind = event.get('type') if isinstance(event, dict) else None
        if kind == REPORT:
            if set(event) == {'type', 'busy'} and type(event['busy']) is bool:
                self.reports[socket] = (event['busy'], time.monotonic())
            return True
        if kind == ACK:
            attempt = self.attempts.get(event.get('id')) if isinstance(event.get('id'), str) else None
            if (set(event) == {'type', 'id', 'busy'} and type(event['busy']) is bool and attempt is not None
                    and socket in attempt['sockets']):
                attempt['acks'][socket] = event['busy']
                self.reports[socket] = (event['busy'], time.monotonic())
            return True
        return False

    def busy(self, socket, now):
        report = self.reports.get(socket)
        return report is None or report[0] or now - report[1] > STALE_SECONDS

    def state(self):
        now = time.monotonic()
        sockets = list(self.clients)
        unknown = sum(socket not in self.reports for socket in sockets)
        stale = sum(socket in self.reports and now - self.reports[socket][1] > STALE_SECONDS for socket in sockets)
        return {'clients': len(sockets), 'busy': sum(self.busy(s, now) for s in sockets),
                'unknown': unknown, 'stale': stale}

    async def prepare(self, attempt_id):
        """Ask every tab connected now to lock and acknowledge this attempt."""
        if not isinstance(attempt_id, str) or not ATTEMPT_ID.fullmatch(attempt_id) or attempt_id in self.attempts:
            raise ValueError('A deployment attempt needs a new id of 1-64 letters, digits, _ or -')
        while len(self.attempts) >= MAX_ATTEMPTS:
            self.attempts.pop(next(iter(self.attempts)))
        sockets = frozenset(self.clients)
        self.attempts[attempt_id] = {'sockets': sockets, 'acks': {}}
        text = json.dumps({'type': PREPARE, 'id': attempt_id})
        await asyncio.gather(*(self.send(socket, text) for socket in sockets))

    def ready(self, attempt_id):
        """Now: the tabs connected are exactly those asked, each acknowledged this attempt as idle and its latest
        report is idle and fresh. A connect, a disconnect, a busy report or silence makes it False. No tabs is
        idle. The caller closes the write gate in the same synchronous step."""
        attempt = self.attempts.get(attempt_id)
        if attempt is None:
            return False
        sockets = frozenset(self.clients)
        if sockets != attempt['sockets']:
            return False
        now = time.monotonic()
        return all(attempt['acks'].get(s) is False and not self.busy(s, now) for s in sockets)

    async def wait_acks(self, attempt_id, timeout=PREPARE_ACK_SECONDS):
        """Bounded: True as soon as ready(attempt_id), False at the timeout or once the tab set has changed."""
        deadline = time.monotonic() + timeout
        while True:
            attempt = self.attempts.get(attempt_id)
            if attempt is None or frozenset(self.clients) != attempt['sockets']:
                return False
            if self.ready(attempt_id):
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(ACK_POLL_SECONDS)

    async def release(self, attempt_id):
        """After an abort or after the gate reopens: every connected tab is told, including one that reconnected
        while still holding this attempt's lock; a tab without that lock ignores it."""
        if self.attempts.pop(attempt_id, None) is None:
            return
        text = json.dumps({'type': RELEASE, 'id': attempt_id})
        await asyncio.gather(*(self.send(socket, text) for socket in list(self.clients)))
