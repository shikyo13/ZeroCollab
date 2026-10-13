"""One pinned app-owned host handover; never calls user Stop or Resume.

Runtime startup/beat/close and the server-owned exit callback are paired by the
backend. Records contain code identities and queue hashes, no session material.
"""
import asyncio
import ctypes
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import time

from durable import atomic_write
from release_maintenance import active_runs

OLD = 'db785089451b0e8d35db879f9006e208dc13f2c8f73333e3d1a35f150346579d'
NEW = '475254cae22d85c812f2d4d62e4254d59bcc252870bfcbf2b5130a1a3cec77fb'
REQUEST = 'host-handover.request.json'
ACK = 'host-handover.ack.json'
REOPEN = 'host-handover.reopen.json'
SECONDS = 600
MAX_RECORD = 8192
SETTLED = {'deployed', 'aborted', 'rejected', 'rolled_back'}
HEX = re.compile(r'[0-9a-f]{64}')
NONCE = re.compile(r'[0-9a-f]{32}')
LOGGER = logging.getLogger(__name__)


def read_record(path, limit=MAX_RECORD):
    try:
        with path.open('rb') as stream:
            raw = stream.read(limit + 1)
    except FileNotFoundError:
        return None
    if len(raw) > limit:
        raise ValueError('record_too_large')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('record_schema')
    return value


def write_record(path, value):
    data = json.dumps(value, sort_keys=True, allow_nan=False)
    if len(data.encode('utf-8')) > MAX_RECORD:
        raise ValueError('record_too_large')
    atomic_write(path, lambda stream: stream.write(data))


def file_sha(path, limit=64 * 1024 * 1024):
    digest, size = hashlib.sha256(), 0
    with path.open('rb') as stream:
        while data := stream.read(65536):
            size += len(data)
            if size > limit:
                raise ValueError('file_too_large')
            digest.update(data)
    return digest.hexdigest()


def request_schema(value):
    fields = {'version', 'nonce', 'created', 'expected_manifest_sha',
              'from_sha256', 'to_sha256', 'helper_pid', 'helper_started'}
    if (set(value) != fields or type(value['version']) is not int or value['version'] != 1
            or not isinstance(value['nonce'], str) or not NONCE.fullmatch(value['nonce'])
            or type(value['created']) not in (int, float) or not math.isfinite(value['created'])
            or any(not isinstance(value[k], str) or not HEX.fullmatch(value[k])
                   for k in ('expected_manifest_sha', 'from_sha256', 'to_sha256'))
            or any(type(value[k]) is not int or value[k] <= 0
                   for k in ('helper_pid', 'helper_started'))
            or (value['from_sha256'], value['to_sha256']) != (OLD, NEW)):
        raise ValueError('request_schema')
    return value


def helper_alive(pid, started):
    """Exact process handle and creation FILETIME; no process/environment scan."""
    if os.name != 'nt':
        return False
    from ctypes import wintypes as wt
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    api.OpenProcess.restype = wt.HANDLE
    api.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
    api.GetProcessTimes.restype = wt.BOOL
    api.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    api.WaitForSingleObject.restype = wt.DWORD
    api.CloseHandle.argtypes = [wt.HANDLE]
    api.CloseHandle.restype = wt.BOOL
    handle = api.OpenProcess(0x00100000 | 0x1000, False, pid)
    if not handle:
        return False
    try:
        times = [wt.FILETIME() for _ in range(4)]
        if not api.GetProcessTimes(handle, *map(ctypes.byref, times)):
            return False
        actual = times[0].dwLowDateTime | times[0].dwHighDateTime << 32
        return actual == started and api.WaitForSingleObject(handle, 0) == 258
    finally:
        api.CloseHandle(handle)


class HostHandover:
    def __init__(self, runtime, host_root, request_exit):
        self.runtime, self.state = runtime, runtime.deployment
        self.host_root, self.root = Path(host_root).resolve(), self.state.root
        self.request_exit = request_exit
        self.held = self.exit_ready = False
        self.request = self.ack = self.grant = self.latch = None
        self.prepared = False
        self.hold_since = None
        self.busy = asyncio.Lock()

    def _gate(self, writes):
        gate = self.state.reload()
        nonce = (self.request or self.ack)['nonce']
        gate.update(seq=gate['seq'] + 1, deployment_id='host-handover:' + nonce,
                    admission='closed', writes=writes, interrupt=False,
                    reason='Held for host handover ' + nonce + ', since ' +
                    str((self.request or self.ack)['created']))
        write_record(self.root / 'maintenance.json', gate)
        self.state.reload()
        self.held = True
        if self.hold_since is None:
            self.hold_since = time.time()
        return gate['seq']

    def startup_hold(self):
        """Backend calls before project setup, queue restoration or admission."""
        with self.state.lock:
            self.ack = read_record(self.root / ACK)
            # Do not replay a settled nonce on ordinary restart, including rollback.
            if self.ack and self.ack.get('phase') in ('completed', 'refused', 'user_released'):
                self.ack = None
            if self.ack:
                if (type(self.ack.get('version')) is not int or self.ack.get('version') != 1
                        or not NONCE.fullmatch(str(self.ack.get('nonce', '')))
                        or type(self.ack.get('created')) not in (int, float)
                        or not math.isfinite(self.ack['created'])):
                    raise ValueError('ack_schema')
                self._gate('blocked')
                return True
            value = read_record(self.root / REQUEST)
            terminal = read_record(self.root / ACK)
            if value and (not terminal or terminal.get('nonce') != value.get('nonce')):
                self.request = request_schema(value)
                self._gate('open')
                return True
        return False

    def _no_deploy(self):
        if (self.root / 'journal.json').exists() or any((self.root / 'requests').glob('*.json')):
            raise ValueError('deployment_busy')
        for path in (self.root / 'requests').glob('*.taken'):
            request, receipt = read_record(path), read_record(self.root / 'receipts' / (path.stem + '.json'), 1024 * 1024)
            if (not request or not receipt or receipt.get('id') != request.get('id')
                    or receipt['id'] != path.stem or receipt.get('request') != request
                    or receipt.get('status') not in SETTLED):
                raise ValueError('deployment_unsettled')

    def _validate(self):
        rt, request = self.runtime, self.request
        request_schema(request)
        now = time.time()
        if not 0 <= now - request['created'] < SECONDS:
            raise ValueError('request_expired')
        if self.state.rehearsal:
            raise ValueError('rehearsal_disabled')
        self._no_deploy()
        current = read_record(self.root / 'current.json', 1024 * 1024)
        if not current or current.get('manifest_sha') != request['expected_manifest_sha']:
            raise ValueError('manifest_changed')
        if file_sha(self.host_root / 'supervisor.py', 1024 * 1024) != OLD:
            raise ValueError('host_changed')
        if not callable(self.request_exit):
            raise ValueError('exit_unavailable')
        grant = self.state.release_grant.require(
            {'profile': 'astra', 'project': rt.identity, 'run': 'host-handover:' + request['nonce']},
            rt.profiles.items.get('astra'))
        latch = rt.manual_stop.snapshot() if rt.manual_stop is not None else None
        if (not latch or latch['stopped'] or rt.paused or rt.manual_stop_pending
                or getattr(rt, 'repository_transition', None)):
            raise ValueError('room_stopped')
        if self.grant is not None and (grant != self.grant or latch != self.latch):
            raise ValueError('authority_changed')
        if not helper_alive(request['helper_pid'], request['helper_started']):
            raise ValueError('helper_unavailable')
        if self.grant is None:
            self.grant, self.latch = dict(grant), dict(latch)

    def counters(self):
        rt = self.runtime
        gate = rt.host.media_jobs
        tasks = {t for t in gate.tasks if not t.done()}
        tickets = [t for t in gate.entries.values() if not t.done.done()]
        media = len(tasks) + len(tickets) + int(gate.active is not None)
        recorders = 0
        for browser in getattr(getattr(rt, 'browser', None), 'instances', {}).values():
            for take in browser.recordings.values():
                task = getattr(take, 'task', None)
                if task is not None and not task.done():
                    recorders += 1
        return {'active_runs': active_runs(rt), 'active_mutations': self.state.mutations,
                'media_owners': media, 'recorders': recorders}

    def _ready(self):
        self._validate()
        gate = self.state.reload()
        if (gate['deployment_id'] != 'host-handover:' + self.request['nonce']
                or gate['admission'] != 'closed' or gate['writes'] != 'open'):
            raise ValueError('gate_changed')
        counters = self.counters()
        if any(counters.values()) or not self.runtime.host.composing.ready(self.request['nonce']):
            return False
        seq = self._gate('blocked')
        self.runtime.save_deploy_queue()
        checkpoint = file_sha(self.root / 'pending-wakes.json')
        self.ack = {'version': 1, 'nonce': self.request['nonce'], 'phase': 'ready',
                    'from_sha256': OLD, 'to_sha256': NEW,
                    'expected_manifest_sha': self.request['expected_manifest_sha'],
                    'project_id': self.runtime.identity, 'maintenance_seq': seq,
                    'latch': dict(self.latch), 'checkpoint_sha256': checkpoint,
                    'counters': counters, 'created': time.time()}
        write_record(self.root / ACK, self.ack)
        self.exit_ready = True
        self.request_exit()  # synchronous server callback; no user/control transition
        return True

    async def _refuse(self, reason):
        nonce = self.request['nonce']
        with self.state.lock:
            gate = self.state.reload()
            if gate['writes'] == 'blocked':
                # Partial persistence cannot prove restoration; remain held.
                self.ack = {'version': 1, 'nonce': nonce, 'phase': 'failed',
                            'reason': 'checkpoint_failed', 'created': time.time()}
                write_record(self.root / ACK, self.ack)
                return
            write_record(self.root / ACK, {'version': 1, 'nonce': nonce, 'phase': 'refused',
                         'reason': reason, 'created': time.time()})
            if gate.get('deployment_id') == 'host-handover:' + nonce:
                gate.update(seq=gate['seq'] + 1, admission='open', writes='open',
                            reason='Host handover refused', deployment_id='')
                write_record(self.root / 'maintenance.json', gate)
                self.state.reload()
            self.held = False
        if self.prepared:
            await self.runtime.host.composing.release(nonce)
        self.request = None
        self.grant = self.latch = None
        self.prepared = False

    async def _reopen(self):
        value = read_record(self.root / REOPEN)
        if not value:
            return
        fields = {'version', 'nonce', 'expected_manifest_sha', 'host_sha256',
                  'outcome', 'checkpoint_sha256', 'created'}
        ack = self.ack
        if (set(value) != fields or type(value['version']) is not int or value['version'] != 1
                or value['nonce'] != ack['nonce'] or value['outcome'] not in ('applied', 'rolled_back')
                or value['expected_manifest_sha'] != ack.get('expected_manifest_sha')
                or value['checkpoint_sha256'] != ack.get('checkpoint_sha256')
                or type(value['created']) not in (int, float) or not math.isfinite(value['created'])
                or not ack['created'] <= value['created'] <= time.time()
                or value['host_sha256'] != (NEW if value['outcome'] == 'applied' else OLD)
                or file_sha(self.host_root / 'supervisor.py', 1024 * 1024) != value['host_sha256']
                or file_sha(self.root / 'pending-wakes.json') != ack['checkpoint_sha256']
                or self.runtime.identity != ack['project_id']):
            raise ValueError('reopen_mismatch')
        with self.state.lock:
            current = read_record(self.root / 'current.json', 1024 * 1024)
            if current.get('manifest_sha') != ack['expected_manifest_sha']:
                raise ValueError('manifest_changed')
            gate = self.state.reload()
            if gate.get('deployment_id') != 'host-handover:' + ack['nonce']:
                raise ValueError('gate_changed')
            # Commit authorization before opening writes: after this boundary
            # a crash restarts the verified pair, never reapplies the old hold.
            write_record(self.root / ACK, {**ack, 'phase': 'completed', 'outcome': value['outcome']})
            gate.update(seq=gate['seq'] + 1, admission='open', writes='open',
                        reason='Host handover verified', deployment_id='')
            write_record(self.root / 'maintenance.json', gate)
            self.state.reload()
            self.held = False
        await self.runtime.host.composing.release(ack['nonce'])
        await self.runtime.reconcile_manual_stop()
        await self.runtime.reopen_if(ack['latch']['generation'])
        self.ack = self.request = None

    async def release_for_user(self):
        """Only Backend's authenticated user Resume may invoke this rescue."""
        if not self.held:
            return False
        record = self.ack or self.request
        # A corrupt future timestamp must not make the user's rescue impossible.
        since = min(record['created'], self.hold_since or time.time())
        if time.time() - since < SECONDS:
            return False
        with self.state.lock:
            gate = self.state.reload()
            if gate.get('deployment_id') != 'host-handover:' + record['nonce']:
                return False
            write_record(self.root / ACK, {**record, 'phase': 'user_released'})
            gate.update(seq=gate['seq'] + 1, admission='open', writes='open',
                        reason='User released expired host handover', deployment_id='')
            write_record(self.root / 'maintenance.json', gate)
            self.state.reload()
            self.held = False
        await self.runtime.host.composing.release(record['nonce'])
        try:
            self.runtime.append_event(json.dumps({'type': 'host_handover_user_release',
                'time': time.time(), 'data': {'nonce': record['nonce']}}) + '\n')
        except OSError:
            LOGGER.exception('Host handover user release audit append failed; durable ack records the action')
        self.request = self.ack = None
        return True

    async def tick(self):
        if self.busy.locked() or self.exit_ready:
            return
        async with self.busy:
            if self.ack:
                if self.ack.get('phase') == 'ready':
                    await self._reopen()
                return
            if self.request is None:
                value = read_record(self.root / REQUEST)
                prior = read_record(self.root / ACK)
                if not value or prior and prior.get('nonce') == value.get('nonce'):
                    return
                self.request = request_schema(value)
            try:
                with self.state.lock:
                    self._validate()
                    if not self.held:
                        if not self.state.admission_open():
                            raise ValueError('gate_busy')
                        self._gate('open')
                    if any(self.counters().values()) or self.runtime.host.composing.state()['busy']:
                        return
                if not self.prepared:
                    await self.runtime.host.composing.prepare(self.request['nonce'])
                    self.prepared = True
                    if not await self.runtime.host.composing.wait_acks(self.request['nonce'], timeout=10):
                        raise ValueError('editors_busy')
                with self.state.lock:
                    self._ready()
            except (OSError, ValueError, TypeError, TimeoutError) as exc:
                known = {'request_expired', 'rehearsal_disabled', 'deployment_busy', 'deployment_unsettled',
                         'manifest_changed', 'host_changed', 'exit_unavailable', 'room_stopped',
                         'authority_changed', 'helper_unavailable', 'gate_busy', 'gate_changed', 'editors_busy'}
                await self._refuse(str(exc) if str(exc) in known else 'handover_failed')
