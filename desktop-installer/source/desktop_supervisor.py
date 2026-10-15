#!/usr/bin/env python3
"""Room supervisor: owns the room server process and executes approved deployments.

  python supervisor.py start     Launch the supervisor detached and wait until the room is ready
                                 (Start-Room.ps1 calls this).
  python supervisor.py run       The supervisor itself (normally started by `start`).
  python supervisor.py stop      Ask a running supervisor to stop the room (Stop-Room.ps1).
  python supervisor.py recover   Roll back an interrupted deployment while the room is down.

The supervisor is not part of any release (release_pack.py refuses its files), so a release can
never replace the code that is deploying it or recovering from it. It talks to the app only
through the spool LIVE/data/deploy/ (contract: room follow-up a30afc6a):

  releases/<id>/        packaged by release_pack.py
  requests/<id>.json    written by the app when the human approves a release; taken as <id>.taken
  maintenance.json      supervisor gates; app atomically closes agent writes after tab acknowledgements
  current.json          RELEASE.json of the deployed release; the app serves it as /api/version
  status.json           supervisor state, for display
  receipts/<id>.json    terminal result: deployed, aborted, rejected, rolled_back,
                        rollback_failed, conflict or recovery_required
  journal.json          in-flight deployment, for crash recovery
  backups/<id>/         replaced files, previous current.json and the stopped room's data records

A deployment drains agent turns with admission closed (aborting at the deadline unless the
request allows interrupting), blocks writes, stops the server and confirms it exited, snapshots
the data records, applies the release, starts it with writes still blocked and verifies the
running release before reopening. Any failure after the stop restores files (compare-and-swap),
the previous current.json and the data snapshot, then restarts and verifies the previous release.
"""
import argparse
import hashlib
import json
import msvcrt
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

import httpx

import release_pack

LIVE = Path(__file__).resolve().parent
DATA = LIVE / 'data'
SPOOL = DATA / 'deploy'
HANDBACK = SPOOL / 'handback.json'  # {kind}: the other app used the room's data last; the next room opens Stopped
# Read as text by the desktop app with run.py's: both must declare it before desktop opens this room's data.
ROOM_WRITER_PROTOCOL = 1
AGENT_RELEASE_PROTOCOL = 1  # host capability; installation requires a separate user handover
AGENT_INSTALL_INTERRUPT = 1 # capability: agent requests may carry interrupt=True (9f3eca50), advertised in status.json
AGENT_ACTION_TIMEOUT = 20  # seconds for the app's announce, status and release replies
AGENT_BLOCK_TIMEOUT = 75    # block may wait up to 60 s in the app for a brief busy state (desktop 895447e3)
APP_REASON_CHARS = 300      # an app refusal's reason kept in the abort receipt
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
REQUEST_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
# Data records snapshotted for an exact restore; everything else in data/ is left alone.
SNAPSHOT_SKIP_DIRS = {'worktrees', 'integration-backups', 'controls-verification', 'deploy'}
SNAPSHOT_SKIP_FILES = {'server-process.json', 'deploy-helper.lock'}
TERMINAL = ('deployed', 'aborted', 'rejected', 'rolled_back', 'rollback_failed', 'conflict', 'recovery_required')


class Abort(Exception):
    """Deployment stopped before the server was touched; the room keeps running."""


def now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return default


write_json = release_pack.write_json


def place(source, target):
    """Replace target with a copy of source atomically: a crash leaves the old or the new file, never a torn one."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f'{target.name}.{os.getpid()}.deploy-tmp')
    try:
        shutil.copyfile(source, temp)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def room_port():
    for name in ('config.local.toml', 'config.toml'):
        try:
            port = tomllib.loads((LIVE / name).read_text(encoding='utf-8')).get('server', {}).get('port')
        except (OSError, ValueError):
            continue
        if port:
            return int(port)
    return 8300


def room_data():
    """The data folder run.py writes, by the same rule as config_loader.room_paths: config.toml's
    server.data_dir (or ZEROCOLLAB_DATA_DIR), relative to LIVE, resolved. Inlined so this file never depends
    on a release-shipped helper's version; run.py checks the lease it adopts names the same folder."""
    value = os.environ.get('ZEROCOLLAB_DATA_DIR') or ''
    if not value:
        try:
            value = tomllib.loads((LIVE / 'config.toml').read_text(encoding='utf-8')).get('server', {}).get('data_dir', '')
        except (OSError, ValueError):
            value = ''
    path = Path(value or './data')
    return (path if path.is_absolute() else LIVE / path).resolve()


def lease_holder(exc):
    """Plain text naming who holds the room's data folder, from room_writer.RoomInUse."""
    owner = getattr(exc, 'owner', None) or {}
    who = {'desktop': 'the ZeroCollab desktop app', 'web': 'another ZeroCollab web room'}.get(owner.get('kind'), 'another program')
    pid = f' (PID {owner["pid"]})' if isinstance(owner.get('pid'), int) else ''
    return f'{room_data()} is in use by {who}{pid}. Quit it, then start the room again.'


def processes():
    """All processes as {pid: (parent_pid, name, command_line)}."""
    script = ('[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-CimInstance Win32_Process | '
              'Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress')
    run = subprocess.run(['powershell.exe', '-NoProfile', '-Command', script], capture_output=True,
                         encoding='utf-8', errors='replace', timeout=60, creationflags=NO_WINDOW)
    if run.returncode:
        raise RuntimeError('Process listing failed: ' + run.stderr[-800:])
    return {r['ProcessId']: (r['ParentProcessId'], r['Name'] or '', r['CommandLine'] or '') for r in json.loads(run.stdout)}


def supervisor_alive(pid):
    """True when pid is a live supervisor.py run for this tree."""
    if not pid:
        return False
    row = processes().get(pid)
    return bool(row) and 'supervisor.py' in row[2].lower() and ' run' in row[2].lower() and str(LIVE).lower() in row[2].lower()


def lock(path):
    """OS byte-range lock, released by the OS if this process dies. Returns the handle or None."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, 'a+b')
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def data_files():
    """Data records in snapshot scope, as relative paths (worktrees and backups are never walked)."""
    found = []
    for folder, dirs, files in os.walk(DATA):
        base = Path(folder).relative_to(DATA)
        if base == Path('.'):
            dirs[:] = [d for d in dirs if d not in SNAPSHOT_SKIP_DIRS]
            files = [f for f in files if f not in SNAPSHOT_SKIP_FILES and not f.endswith(('.log', '.lock'))]
        found.extend(base / name for name in files)
    return found


def creation_utc(handle):
    """Process creation time in .NET round-trip format, exact to the tick, as Stop-Room compares it."""
    import ctypes
    from ctypes import wintypes
    times = [wintypes.FILETIME() for _ in range(4)]
    if not ctypes.windll.kernel32.GetProcessTimes(wintypes.HANDLE(int(handle)), *map(ctypes.byref, times)):
        return None
    filetime = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime  # 100 ns ticks since 1601 UTC
    seconds, fraction = divmod(filetime, 10_000_000)
    stamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(seconds - 11_644_473_600))
    return f'{stamp}.{fraction:07d}Z'


class Supervisor:
    def __init__(self, nonce=''):
        self.url = f'http://127.0.0.1:{room_port()}'
        # Where the user's browser opens the room; its sign-in cookie lives on this host name only.
        self.room_url = f'http://zerocollab.localhost:{room_port()}'
        self.client = httpx.Client(base_url=self.url, timeout=20, trust_env=False)
        # The UI token is made here and reaches the room only on its stdin: never a file, a log or an
        # environment variable an agent could read. Browser sessions survive room restarts as hashes only.
        self.ui_token = secrets.token_hex(32)
        self.session_hashes = []
        self.child = None
        # nonce identifies this launch: the venv python.exe is a redirector, so the PID that
        # launched us is not os.getpid() and cannot be used to recognize our status file.
        self.status = {'supervisor_pid': os.getpid(), 'url': self.url, 'nonce': nonce}
        self.status_lock = threading.Lock()
        self.locks = []
        # The data-folder lease (room_writer.py), held for this process's life so the desktop app can never
        # open the room's data between child restarts or during a deployment. Each child gets a duplicate.
        self.lease = None
        self.start_stopped = None  # the other app that used the data last; only the first child opens Stopped

    # ----- records -----
    def set_status(self, state=None, **values):
        # updated is epoch seconds, refreshed every few seconds by heartbeat() so the UI can tell it is alive.
        with self.status_lock:
            self.status.update(values, state=state or self.status.get('state'), updated=time.time(),
                               agent_release_protocol=AGENT_RELEASE_PROTOCOL,
                               # The app forces an agent install's interrupt only when the host says it takes it
                               # (security 4b29e072 B1), so room and host can be updated in either order.
                               agent_install_interrupt=AGENT_INSTALL_INTERRUPT,
                               child_pid=self.child.pid if self.child and self.child.poll() is None else None,
                               release_id=(read_json(SPOOL / 'current.json') or {}).get('id'))
            write_json(SPOOL / 'status.json', self.status)

    def heartbeat(self):
        while True:
            time.sleep(5)
            self.set_status()

    def gate(self, admission, writes, deployment_id='', interrupt=False, reason='', version=''):
        seq = (read_json(SPOOL / 'maintenance.json') or {}).get('seq', 0) + 1
        write_json(SPOOL / 'maintenance.json', {'seq': seq, 'deployment_id': deployment_id, 'admission': admission,
                                                'writes': writes, 'interrupt': interrupt, 'reason': reason,
                                                'version': version, 'updated': now()})
        return seq

    # ----- room HTTP -----
    def session(self, wait_seconds=0):
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                # A room started by this supervisor took the token from stdin.
                self.client.headers['X-Session-Token'] = self.ui_token
                if self.client.get('/api/runtime', timeout=5).status_code == 200:
                    return
                # An older room ignores stdin and still embeds its own token in the page.
                page = self.client.get('/', timeout=5)
                page.raise_for_status()
                match = re.search(r'window\.__SESSION_TOKEN__="([^"]+)"', page.text)
                if not match:
                    raise RuntimeError('Room session marker missing')
                self.client.headers['X-Session-Token'] = match.group(1)
                return
            except (httpx.HTTPError, RuntimeError):
                if self.child and self.child.poll() is not None:
                    raise RuntimeError(f'Room server exited with code {self.child.returncode}; see data/server-error.log')
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)

    def get(self, path, allow=()):
        response = self.client.get(path)
        if response.status_code in allow:
            return None
        response.raise_for_status()
        return response.json()

    def acknowledged(self, seq, idle=False, wait=0):
        """The running app applied maintenance seq (and, with idle, has no turns or writes in flight)."""
        deadline = time.monotonic() + wait
        while True:
            state = self.get('/api/runtime/maintenance', allow=(404,)) or {}
            if state.get('seq_applied') == seq and (not idle or not (state.get('active_runs') or state.get('active_mutations'))):
                return state
            if time.monotonic() >= deadline:
                return None
            time.sleep(1)

    def verified_start(self, expect, deployment_id, reason):
        """Start the server behind a closed gate and prove which release is running. Returns the gate seq."""
        seq = self.gate('closed', 'blocked', deployment_id, reason=reason)
        self.start_child()
        version = self.get('/api/version', allow=(404,)) or {}
        if expect and version.get('id') != expect:
            raise RuntimeError(f'Running release is {version.get("id")}, expected {expect}')
        state = self.acknowledged(seq, wait=10)
        if not state or state.get('writes') != 'blocked' or state.get('admission') != 'closed':
            raise RuntimeError(f'Release did not start behind the write gate: {state}')
        for path in ('/api/settings', '/api/runtime', '/api/runtime/projects'):
            self.get(path)
        return seq

    def open_gate(self, deployment_id, resume, request=None):
        seq = self.gate('open', 'open', deployment_id, reason='Deployment finished')
        if not self.acknowledged(seq, wait=30):
            raise RuntimeError('App did not acknowledge reopening')
        if (request or {}).get('approved_by') == 'release-manager:astra':
            # Never call user Resume for an agent deployment: it clears Stop.
            response = self.client.post('/api/runtime/deployments/control/' + deployment_id,
                                        json={'action': 'release'}, timeout=20)
            response.raise_for_status()
        elif resume:
            self.client.post('/api/runtime/resume', json={}).raise_for_status()

    # ----- server process -----
    def start_child(self):
        DATA.mkdir(parents=True, exist_ok=True)
        python, env = sys.executable, None
        if self.lease:
            # A venv's python.exe is a launcher that runs the real interpreter as its own child, so a handle
            # duplicated into the launcher never reaches run.py. Start the real interpreter in the same venv
            # the way the launcher does (__PYVENV_LAUNCHER__), so the Popen process is the room itself.
            base = Path(getattr(sys, '_base_executable', '') or sys.executable)
            if sys.prefix != sys.base_prefix and base.is_file() and base.resolve() != Path(sys.executable).resolve():
                python, env = str(base), {**os.environ, '__PYVENV_LAUNCHER__': sys.executable}
        with open(DATA / 'server.log', 'ab') as out, open(DATA / 'server-error.log', 'ab') as err:
            self.child = subprocess.Popen([python, '-X', 'utf8', '-u', str(LIVE / 'run.py')], cwd=LIVE, env=env,
                                          stdin=subprocess.PIPE, stdout=out, stderr=err, creationflags=NO_WINDOW)
        handoff = {'ui_token': self.ui_token, 'session_hashes': self.session_hashes}
        if self.lease:
            # The child waits on stdin before writing anything, so it adopts this duplicate first.
            try:
                handoff['room_writer'] = self.lease.duplicate_for_child(self.child)
            except Exception:
                self.child.kill()
                raise
            handoff['start_stopped'] = self.start_stopped
        try:  # One line, then EOF. An older run.py never reads it; the line fits the pipe buffer.
            self.child.stdin.write((json.dumps(handoff) + '\n').encode())
            self.child.stdin.close()
        except OSError:
            pass  # The child already exited; session() below reports it.
        # Same fields Stop-Room has always checked, so its fallback still works if the supervisor dies.
        write_json(DATA / 'server-process.json', {'pid': self.child.pid, 'startUtc': creation_utc(self.child._handle),
                                                  'root': str(LIVE), 'supervisor_pid': os.getpid()})
        self.session(wait_seconds=90)
        if handoff.get('start_stopped'):
            # The room answers only after its startup ran, so it has opened Stopped and saved that state.
            self.start_stopped = None
            HANDBACK.unlink(missing_ok=True)

    def stop_child(self, graceful=True):
        """Stop the server by its handle and confirm it is gone. Returns True only when confirmed."""
        if self.child and self.child.poll() is None:
            # Keep browsers signed in across the restart: hashes only, handed to the next room on stdin.
            try:
                sessions = self.client.get('/api/runtime/ui-sessions', timeout=5)
                if sessions.status_code == 200:
                    self.session_hashes = sessions.json().get('sessions', [])
            except (httpx.HTTPError, ValueError):
                pass  # An older room has no browser sessions to keep.
            if graceful:
                try:
                    self.client.post('/api/runtime/stop', json={}, timeout=20)
                except httpx.HTTPError:
                    pass  # Stopping agents is best effort; the process stops regardless.
            subprocess.run(['taskkill.exe', '/PID', str(self.child.pid), '/T', '/F'], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
            try:
                self.child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                return False
        return self.server_down()

    def port_free(self):
        """The room port can be bound on loopback and wildcard, so nothing is listening on it.

        A bind answers at once; an HTTP probe cannot, because Windows retries a refused loopback
        connect for about 2 s and a short timeout then reads as 'still up' (room #726).
        """
        port = int(self.url.rsplit(':', 1)[1])
        for host in ('127.0.0.1', '0.0.0.0'):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                probe.bind((host, port))
            except OSError:
                return False
            finally:
                probe.close()
        return True

    def server_down(self, wait_seconds=20):
        """No run.py from this tree is alive and the port is free. Any doubt is 'not down'."""
        run_py = str(LIVE / 'run.py').lower()
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                running = [pid for pid, (_, name, cmd) in processes().items()
                           if name.lower().startswith('python') and run_py in cmd.lower()]
            except Exception:
                running = ['unknown']
            if not running and self.port_free():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(1)

    # ----- main loop -----
    def acquire(self):
        """Single supervisor, and never alongside the legacy deploy_when_idle helper (same lock file)."""
        for path in (SPOOL / 'supervisor.lock', DATA / 'deploy-helper.lock'):
            handle = lock(path)
            if not handle:
                sys.exit(f'{path} is held: another supervisor or deployment helper is running.')
            self.locks.append(handle)
        try:
            import room_writer
        except ImportError:  # Installed before the release that ships room_writer.py: run as before, unleased.
            self.status['data_lease'] = 'unavailable: room_writer.py is not installed yet'
            return
        try:
            self.lease = room_writer.open_room_writer(room_data(), kind='web', supervisor=True)
        except room_writer.RoomInUse as exc:
            text = lease_holder(exc)
            self.set_status('data_in_use', error=text)
            sys.exit(text)
        except (OSError, ValueError) as exc:  # e.g. a damaged owner record: refuse rather than run unleased
            text = f'Could not take ownership of {room_data()}: {exc}'
            self.set_status('data_lease_failed', error=text[:1000])
            sys.exit(text)
        self.status['data_lease'] = 'held'
        # Opening the lease records this supervisor as the owner, so keep "the other app used it last" in the
        # spool until a room has started Stopped with it (bootstrap and recover take the lease too).
        previous = (self.lease.previous or {}).get('kind')
        kept = (read_json(HANDBACK) or {}).get('kind')
        self.start_stopped = next((k for k in (previous, kept) if k not in (None, 'web')), None)
        if self.start_stopped:
            write_json(HANDBACK, {'kind': self.start_stopped, 'time': now()})

    def run(self):
        self.acquire()
        (SPOOL / 'stop.request').unlink(missing_ok=True)
        (SPOOL / 'open.request').unlink(missing_ok=True)
        journal = read_json(SPOOL / 'journal.json')
        if journal and journal.get('phase') in ('taken', 'composing', 'draining', 'gated'):
            # Crashed before the server was touched: reopen the room and record the abort.
            Deployment.finish_receipt(journal, 'aborted', 'Supervisor restarted during the drain; room was not changed.')
            (SPOOL / 'journal.json').unlink()
            journal = None
        elif journal and journal.get('phase') != 'restart_verify':
            Deployment.finish_receipt(journal, 'recovery_required',
                                      f'Supervisor restarted during phase {journal.get("phase")}. '
                                      'Run "supervisor.py recover" before starting the room.')
            self.set_status('recovery_required', deployment_id=journal.get('id'))
            sys.exit('Interrupted deployment found; run "supervisor.py recover".')
        try:
            if not self.server_down(wait_seconds=0):
                raise RuntimeError(f'A room server is already running on {self.url}; stop it first.')
            self.set_status('starting')
            if journal:
                self.finish_recovery(journal)
            else:
                self.gate('open', 'open', reason='supervisor start')
                self.start_child()
            self.set_status('running', step='')
            threading.Thread(target=self.heartbeat, daemon=True).start()
            while True:
                if self.stop_requested(consume=True):
                    self.set_status('stopping')
                    down = self.stop_child()
                    self.set_status('stopped' if down else 'stop_failed')
                    return
                if self.child.poll() is not None:
                    self.set_status('exited', exit_code=self.child.returncode)
                    return
                if (SPOOL / 'open.request').exists():
                    (SPOOL / 'open.request').unlink(missing_ok=True)
                    self.open_browser()
                for path in sorted((SPOOL / 'requests').glob('*.json')):
                    Deployment(self, path).run()
                    if self.child is None or self.child.poll() is not None:
                        return  # Deployment left the room stopped; its receipt explains why.
                time.sleep(1)
        except Exception as exc:
            self.set_status('failed', error=str(exc)[:1000])
            raise

    def finish_recovery(self, journal):
        """First start after "supervisor.py recover" or "bootstrap": verify, then reopen.

        Verification failures before the gate opens may still undo a bootstrap. Once the
        no-restore boundary is persisted, nothing is restored again, whatever fails next.
        """
        expect = journal.get('expect')
        try:
            self.verified_start(expect, journal['id'], 'Verifying the room before reopening')
        except Exception as exc:
            if journal.get('bootstrap'):
                return self.undo_bootstrap(journal, exc)
            self.stop_child(graceful=False)
            Deployment.finish_receipt(journal, 'recovery_required', f'Restart after recovery failed verification: {exc}'[:1500])
            raise RuntimeError(f'Restart after recovery failed verification: {exc}')
        outcome = journal.get('outcome', 'rolled_back')
        # Boundary: after this, the room may accept writes, so a crash leads to restart_verify of
        # this same release (via recover) and never to a restore or a bootstrap undo.
        journal.update(phase='reopening', reopen_expect=expect, reopen_outcome=outcome, bootstrap=False)
        journal.setdefault('steps', []).append({'time': now(), 'step': 'Verified; reopening the room'})
        write_json(SPOOL / 'journal.json', journal)
        detail = f'Verified release {expect or "(unversioned)"} on restart.'
        try:
            self.open_gate(journal['id'], resume=not journal.get('was_paused'), request=journal.get('request'))
        except Exception as exc:
            detail += f' Reopening the room reported: {exc}'[:800]
        Deployment.finish_receipt(journal, outcome, detail)
        (SPOOL / 'journal.json').unlink(missing_ok=True)

    def undo_bootstrap(self, journal, error):
        """The first supervised release failed verification: restore the unsupervised release and run it.

        That release has no gate, so it is started without one and checked only for a working page.
        """
        if not self.stop_child(graceful=False):
            Deployment.finish_receipt(journal, 'recovery_required', f'Bootstrap failed ({error}); server not confirmed stopped.')
            raise RuntimeError(f'Bootstrap failed and the server did not stop: {error}')
        deployment = Deployment.__new__(Deployment)
        deployment.sup, deployment.journal = self, journal
        conflicts = deployment.restore()  # Compare-and-swap: repeating it after a crash is safe.
        if conflicts:
            Deployment.finish_receipt(journal, 'conflict', f'Bootstrap failed ({error}); restore conflicts, room left stopped: {conflicts[:20]}')
            raise RuntimeError(f'Bootstrap restore conflicts: {conflicts}')
        # Boundary: the legacy release may accept writes as soon as it starts, so the journal goes
        # first. A crash from here on leads to an ordinary start, never to another restore.
        Deployment.finish_receipt(journal, 'rolled_back', f'Bootstrap failed verification ({error}). The previous unsupervised '
                                  'release is restored; it has no gate, so deploying needs a new bootstrap.'[:1500])
        (SPOOL / 'journal.json').unlink(missing_ok=True)
        self.gate('open', 'open', journal['id'], reason='Bootstrap rolled back')
        try:
            self.start_child()
        except Exception as exc:
            self.stop_child(graceful=False)
            Deployment.finish_receipt(journal, 'rollback_failed', f'Bootstrap failed ({error}); files and data were restored, '
                                      f'but the previous release did not start ({exc}).')
            raise RuntimeError(f'Bootstrap rollback failed: {exc}')

    def open_browser(self):
        """Open the user's default browser on a single-use sign-in link. The code goes from the room to the
        browser and nowhere else: whoever asked for the open never sees it, and it is never logged."""
        url = self.url  # An older room still embeds its token in the page and has no launch codes.
        try:
            response = self.client.post('/api/runtime/launch-codes', json={}, timeout=10)
            if response.status_code == 200:
                url = f'{self.room_url}/launch#code={response.json()["code"]}'
        except (httpx.HTTPError, ValueError, KeyError):
            pass
        try:
            os.startfile(url)
        except OSError:
            pass  # No default browser; the room keeps running.

    def stop_requested(self, consume=False):
        """A stop request addressed to this supervisor. Only the main loop consumes it."""
        request = read_json(SPOOL / 'stop.request')
        if request is None:
            return False
        if request.get('pid') != os.getpid():
            (SPOOL / 'stop.request').unlink(missing_ok=True)  # Stale, for an earlier supervisor.
            return False
        if consume:
            (SPOOL / 'stop.request').unlink(missing_ok=True)
        return True


class Deployment:
    def __init__(self, supervisor, request_path):
        self.sup = supervisor
        self.request_path = request_path
        self.journal = {'id': request_path.stem, 'phase': 'taken', 'steps': [], 'started': now()}
        self.release_dir = None
        self.gate_supported = False
        self.seq = 0

    # ----- records -----
    def step(self, text, phase=None):
        if phase:
            self.journal['phase'] = phase
        self.journal['steps'].append({'time': now(), 'step': text})
        write_json(SPOOL / 'journal.json', self.journal)
        self.sup.set_status('deploying', deployment_id=self.journal['id'], step=text)

    @staticmethod
    def finish_receipt(journal, status, detail):
        receipt = {key: journal.get(key) for key in ('id', 'release_id', 'previous_release_id',
                   'previous_manifest_sha', 'backup', 'request', 'started')}
        receipt.update(status=status, detail=detail, steps=journal.get('steps', []), finished=now())
        write_json(SPOOL / 'receipts' / f'{journal["id"]}.json', receipt)
        return receipt

    def finish(self, status, detail):
        self.journal['phase'] = status
        self.finish_receipt(self.journal, status, detail)
        if status in ('deployed', 'aborted', 'rejected', 'rolled_back'):
            (SPOOL / 'journal.json').unlink(missing_ok=True)
        self.sup.set_status('running' if self.sup.child and self.sup.child.poll() is None else status,
                            deployment_id=self.journal['id'], step=f'{status}: {detail[:300]}')

    # ----- phases -----
    def take(self):
        taken = self.request_path.with_suffix('.taken')
        try:
            os.replace(self.request_path, taken)
        except OSError as exc:
            raise ValueError(f'Could not take the request: {exc}')
        request = read_json(taken)
        if not isinstance(request, dict) or not REQUEST_ID.match(self.journal['id']) or request.get('id') != self.journal['id']:
            raise ValueError('Request is not valid JSON or its id does not match the file name')
        if (SPOOL / 'receipts' / f'{self.journal["id"]}.json').exists():
            self.journal['id'] += '-dup' + str(int(time.time()))  # Keep the original receipt intact.
            raise ValueError('Request id was already used')
        if not request.get('approved_by') or not request.get('approved_at'):
            raise ValueError('Request carries no approval')
        release_id = str(request.get('release_id', ''))
        if not re.fullmatch(r'[0-9a-f]{12}', release_id):
            raise ValueError('Request names no valid release')
        self.release_dir = SPOOL / 'releases' / release_id
        try:
            self.manifest, self.release = release_pack.check_release(self.release_dir, str(request.get('manifest_sha', '')))
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            raise ValueError(f'Release {release_id} failed its integrity check: {exc}')
        rehearsal = read_json(self.release_dir / 'rehearsal.json') or {}
        if (rehearsal.get('release_id'), rehearsal.get('manifest_sha'), rehearsal.get('status')) != \
                (release_id, self.release['manifest_sha'], 'passed'):
            raise ValueError(f'Release {release_id} has no passed rehearsal (releases/{release_id}/rehearsal.json)')
        previous = read_json(SPOOL / 'current.json')
        if previous and previous.get('id') == release_id:
            raise ValueError(f'Release {release_id} is already deployed')
        self.journal.update(release_id=release_id, request=request, previous_release_id=(previous or {}).get('id'),
                            previous_manifest_sha=(previous or {}).get('manifest_sha'),
                            backup=str(SPOOL / 'backups' / self.journal['id']))
        self.request = request
        self.previous = previous
        self.backup = Path(self.journal['backup'])
        self.step(f'Took request for release {release_id} ({self.release.get("subject", "")[:120]}), approved by {request["approved_by"]}')
        if self.agent_request():
            # An agent install interrupts running turns after its short drain (opus 7cdd51bd); the app sets that
            # policy. A literal bool only: the drain loop must never treat another value as yes.
            if request.get('protocol') != AGENT_RELEASE_PROTOCOL or not isinstance(request.get('interrupt'), bool):
                raise ValueError('Agent request protocol is unsupported')
            self.agent_status()  # server-side authority again after atomic take

    def agent_request(self):
        return self.journal.get('request', {}).get('approved_by') == 'release-manager:astra'

    @staticmethod
    def app_refusal(response, action):
        """A refusal from the app keeps its reason in the abort receipt (abort 9b46ea04 kept only the status)."""
        if response.status_code < 400:
            return
        try:
            reason = str((response.json() or {}).get('error') or '')
        except ValueError:
            reason = response.text
        raise Abort(f'The app refused {action} ({response.status_code}): {reason[:APP_REASON_CHARS] or "no reason given"}')

    def agent_status(self):
        response = self.sup.client.get('/api/runtime/deployments/control/' + self.journal['id'])
        self.app_refusal(response, 'status')
        result = response.json()
        if result.get('protocol') != AGENT_RELEASE_PROTOCOL:
            raise Abort('App does not support this agent deployment protocol')
        return result

    def agent_action(self, action):
        # block may wait in the app for a brief busy state to clear (desktop 269535ef), so it gets longer.
        response = self.sup.client.post('/api/runtime/deployments/control/' + self.journal['id'], json={'action': action},
                                        timeout=AGENT_BLOCK_TIMEOUT if action == 'block' else AGENT_ACTION_TIMEOUT)
        self.app_refusal(response, action)
        return response.json()

    def plan(self):
        """Files to write and delete, refusing live edits made outside a deployment."""
        expected = {f['path']: f['sha256'] for f in self.previous['files']} if self.previous else {}
        drift, items = [], []
        for item in self.manifest['files']:
            current = digest(LIVE / item['path'])
            if current != item['sha256']:
                if self.previous and current != expected.get(item['path']):
                    drift.append(item['path'])
                items.append({'path': item['path'], 'before': current, 'after': item['sha256']})
        deletions = set(self.manifest['deletions'])
        if self.agent_request() and self.request.get('rollback'):
            # A retained package's original deletion list describes its old
            # predecessor. Returning to it must also remove files added since.
            retained = {item['path'] for item in self.manifest['files']}
            deletions = set(expected) - retained
        for path in sorted(deletions):
            current = digest(LIVE / path)
            if current is not None:
                if self.previous and current != expected.get(path):
                    drift.append(path)
                items.append({'path': path, 'before': current, 'after': None})
        if drift:
            raise ValueError('Live files changed outside a deployment (refusing to overwrite): ' + ', '.join(drift[:20]))
        self.journal['plan'] = items
        self.step(f'Planned {sum(i["after"] is not None for i in items)} writes and {sum(i["after"] is None for i in items)} deletions')

    def check_plan(self):
        for item in self.journal['plan']:
            if digest(LIVE / item['path']) != item['before']:
                raise RuntimeError('Live source changed during the deployment: ' + item['path'])

    def drain(self):
        sup, request = self.sup, self.request
        sup.session()
        self.journal['was_paused'] = bool(sup.get('/api/runtime').get('paused'))
        if sup.get('/api/runtime/maintenance', allow=(404,)) is None:
            # Without the gate a turn or write could start after the last idle check and be cut off.
            raise ValueError('The running app has no maintenance gate; it must be bootstrapped externally first')
        self.gate_supported = True
        if self.agent_request():
            until = time.monotonic() + 120
            self.step('Waiting for all room tabs to be idle', phase='composing')
            while self.agent_status()['composing']['busy']:
                if sup.stop_requested() or time.monotonic() >= until:
                    raise Abort('Room tabs remained busy or Stop was requested')
                time.sleep(.5)
            self.agent_action('announce')
        deadline = time.monotonic() + max(30, min(int(request.get('drain_seconds') or 600), 3600))
        self.seq = sup.gate('closed', 'open', self.journal['id'], reason='Deployment: finishing current agent turns',
                            version=self.release.get('version', ''))
        self.step('Admission closed; waiting for current turns to finish', phase='draining')
        interrupted = False
        while not sup.acknowledged(self.seq, idle=True):
            if self.agent_request():
                self.agent_status()  # revocation, project/Stop/evidence changes abort the drain
            if sup.stop_requested():
                raise Abort('Room stop requested during the drain')
            if time.monotonic() >= deadline:
                if request.get('interrupt') is not True or interrupted:
                    raise Abort('Agent turns did not finish before the drain deadline')
                interrupted = True
                self.seq = sup.gate('closed', 'open', self.journal['id'], interrupt=True, reason='Deployment: interrupting turns')
                self.step('Drain deadline reached; interrupting turns as the request allows')
                deadline = time.monotonic() + 60
            time.sleep(1)
        if self.agent_request():
            # App event loop checks idle acknowledgements and closes writes in
            # one synchronous step; a client report cannot race an HTTP reply.
            self.seq = self.agent_action('block')['seq']
        else:
            self.seq = sup.gate('closed', 'blocked', self.journal['id'], reason='Deployment in progress')
        if not sup.acknowledged(self.seq, idle=True, wait=30):
            raise Abort('App did not acknowledge the write gate')
        self.step('Agents idle and writes blocked', phase='gated')

    def snapshot(self):
        """Copy the stopped room's data records; exact restore is valid until writes reopen."""
        target = self.backup / 'data'
        listing = {}
        for rel in data_files():
            out = target / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(DATA / rel, out)
            listing[rel.as_posix()] = digest(out)
        self.journal['data_snapshot'] = listing
        self.step(f'Snapshot of {len(listing)} data records taken with the room stopped')

    def apply(self):
        files = self.backup / 'files'
        for item in self.journal['plan']:
            if item['before'] is not None:
                saved = files / item['path']
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(LIVE / item['path'], saved)
                if digest(saved) != item['before']:
                    raise RuntimeError('Backup does not match the live file: ' + item['path'])
        if self.previous:
            write_json(self.backup / 'current.json', self.previous)
        self.step('Backed up replaced files', phase='applying')
        for item in self.journal['plan']:
            target = LIVE / item['path']
            if item['after'] is None:
                target.unlink(missing_ok=True)
            else:
                place(self.release_dir / 'payload' / item['path'], target)
            if digest(target) != item['after']:
                raise RuntimeError('Written file does not match the release: ' + item['path'])
        write_json(SPOOL / 'current.json', self.release)
        self.step(f'Applied release {self.release["id"]}', phase='starting')

    def verify(self):
        sup = self.sup
        for item in self.manifest['files']:
            if item['path'].startswith('static/'):
                response = sup.client.get('/' + item['path'])
                if response.status_code != 200 or hashlib.sha256(response.content).hexdigest() != item['sha256']:
                    raise RuntimeError(f'Served {item["path"]} does not match the release ({response.status_code})')
        for path in self.manifest['deletions']:
            if path.startswith('static/') and sup.client.get('/' + path).status_code != 404:
                raise RuntimeError(f'Deleted {path} is still served')
        for item in self.manifest['files']:
            if digest(LIVE / item['path']) != item['sha256']:
                raise RuntimeError('On-disk file does not match the release: ' + item['path'])
        for path in self.manifest['deletions']:
            if (LIVE / path).exists():
                raise RuntimeError('Deleted file is still on disk: ' + path)
        self.step('Verified running release, endpoints, served static files and on-disk hashes', phase='verified')

    def reopen(self, expect, outcome):
        # Persisted before writes open: from here the data snapshot is no longer exact, and
        # recovery must never restore it over records the reopened room accepted. It also records
        # which release is running (the new one, or the previous one after a rollback).
        self.journal.update(reopen_expect=expect, reopen_outcome=outcome)
        self.step('Reopening the room', phase='reopening')
        self.sup.open_gate(self.journal['id'], resume=not self.journal.get('was_paused'),
                           request=self.journal.get('request'))

    # ----- rollback -----
    def restore(self):
        """Compare-and-swap file restore plus exact data restore. Server must be down. Returns conflicts."""
        conflicts = []
        files = Path(self.journal['backup']) / 'files'
        for item in reversed(self.journal.get('plan', [])):
            target = LIVE / item['path']
            current = digest(target)
            if current == item['before']:
                continue
            if current != item['after']:
                conflicts.append(f'{item["path"]} (sha {current})')
                continue
            if item['before'] is None:
                target.unlink()
            else:
                place(files / item['path'], target)
            if digest(target) != item['before']:
                conflicts.append(f'{item["path"]} (restore did not match)')
        previous = Path(self.journal['backup']) / 'current.json'
        if previous.is_file():
            write_json(SPOOL / 'current.json', read_json(previous))
        else:
            (SPOOL / 'current.json').unlink(missing_ok=True)
        snapshot = self.journal.get('data_snapshot')
        if snapshot is not None:
            saved = Path(self.journal['backup']) / 'data'
            for rel in data_files():
                if rel.as_posix() not in snapshot:
                    (DATA / rel).unlink()  # Created by the new release (for example its migration marker).
            for rel, sha in snapshot.items():
                if digest(DATA / rel) != sha:
                    place(saved / rel, DATA / rel)
                if digest(DATA / rel) != sha:
                    conflicts.append(f'data/{rel} (restore did not match)')
        return conflicts

    def rollback(self, error):
        sup = self.sup
        self.step(f'Rolling back: {error}'[:1500], phase='rolling_back')
        if not sup.stop_child(graceful=False):
            return self.finish('recovery_required', f'{error}. Server not confirmed stopped; nothing restored. '
                                                    'Stop it, then run "supervisor.py recover".')
        conflicts = self.restore()
        if conflicts:
            return self.finish('conflict', f'{error}. Rollback conflicts, room left stopped: {conflicts[:20]}')
        self.step('Restored previous files, release record and data records')
        try:
            previous_id = (self.previous or {}).get('id')
            sup.verified_start(previous_id, self.journal['id'], 'Rollback: verifying previous release')
            self.reopen(previous_id, 'rolled_back')
        except Exception as exc:
            sup.stop_child(graceful=False)
            return self.finish('rollback_failed', f'{error}. Previous release did not come back ({exc}); room left stopped.')
        return self.finish('rolled_back', f'{error}. Previous release restored and verified.')

    def run(self):
        stopped = False
        try:
            self.take()
            self.plan()
            self.drain()
            sup = self.sup
            if self.agent_request():
                self.agent_status()  # last policy check before stopping; never interrupt turns
            stopped = True  # From here any failure may have touched the server.
            self.step('Stopping the room server', phase='stopping')
            if not sup.stop_child(graceful=False):
                raise RuntimeError('Room server not confirmed stopped')
            self.check_plan()
            self.snapshot()
            self.apply()
            # The new release must boot behind the gate: admission closed, writes blocked.
            sup.verified_start(self.release['id'], self.journal['id'], 'Deployment: verifying new release')
            self.verify()
        except Exception as exc:
            if not stopped:
                if self.gate_supported:
                    self.sup.gate('open', 'open', self.journal['id'], reason='Deployment aborted')
                    if self.agent_request():
                        try:
                            self.agent_action('release')
                        except Exception:
                            pass  # receipt retains the abort; reconnect also clears old UI locks
                status = 'rejected' if self.journal['phase'] == 'taken' else 'aborted'
                return self.finish(status, f'{exc}'[:1500])
            return self.rollback(str(exc)[:1000])
        detail = f'Release {self.release["id"]} ({self.release["commit"][:12]}) is running and verified.'
        try:
            self.reopen(self.release['id'], 'deployed')
        except Exception as exc:
            # Writes may already be open, so the data snapshot no longer restores exactly: no rollback.
            detail += f' Reopening the room reported: {exc}'[:800]
        self.finish('deployed', detail)


def start(args):
    status = read_json(SPOOL / 'status.json') or {}
    if supervisor_alive(status.get('supervisor_pid')):
        sys.exit(f'Supervisor already running (PID {status["supervisor_pid"]}, state {status.get("state")}).')
    nonce = os.urandom(8).hex()
    argv = [sys.executable, '-X', 'utf8', str(LIVE / 'supervisor.py'), 'run', '--nonce', nonce]
    # A hidden console, not DETACHED_PROCESS: the venv python.exe relays to the real interpreter, which
    # would open a visible console window if its parent had none. Children inherit the hidden one.
    flags = NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    SPOOL.mkdir(parents=True, exist_ok=True)
    with open(DATA / 'supervisor.log', 'ab') as log:
        common = dict(cwd=LIVE, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True)
        try:  # Outlive the launching shell or agent, even inside a job object.
            child = subprocess.Popen(argv, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **common)
        except OSError:
            child = subprocess.Popen(argv, creationflags=flags, **common)
    deadline, mine = time.monotonic() + args.wait, False
    while time.monotonic() < deadline:
        time.sleep(0.5)
        status = read_json(SPOOL / 'status.json') or {}
        mine = status.get('nonce') == nonce
        if mine and status.get('state') in ('running', 'failed', 'exited', 'recovery_required'):
            break
        if child.poll() is not None:
            break
    if mine and status.get('state') == 'running':
        print(f'Room ready: {status["url"]} (supervisor PID {status["supervisor_pid"]}, server PID {status.get("child_pid")})')
        return
    if not mine:
        status = {'state': 'no status from this launch'}
    sys.exit(f'Room did not start: {status.get("state", "no status")} {status.get("error", "")}. '
             f'See {DATA / "supervisor.log"} and {DATA / "server-error.log"}.')


def open_room(args):
    """Ask the running supervisor to open the room in the default browser, signed in. Anyone may ask;
    the sign-in link goes only to the browser the supervisor opens."""
    status = read_json(SPOOL / 'status.json') or {}
    pid = status.get('supervisor_pid')
    if not supervisor_alive(pid):
        # A normal launcher cannot read an elevated supervisor's command line.
        # For Open only, a fresh heartbeat and its live child relationship suffice:
        # the request merely asks that supervisor to open its own sign-in page.
        # Stop/start keep their stricter process identity checks.
        updated = status.get('updated')
        rows = processes()
        parent = rows.get(pid)
        child = rows.get(status.get('child_pid'))
        responsive = (isinstance(updated, (int, float)) and 0 <= time.time() - updated < 15
                      and status.get('state') == 'running' and parent and not parent[2]
                      and parent[1].lower() in ('python.exe', 'pythonw.exe')
                      and child and child[0] == pid)
        if not responsive:
            print('No supervisor is running; start the room first.')
            sys.exit(3)
    write_json(SPOOL / 'open.request', {'pid': pid, 'time': now()})
    deadline = time.monotonic() + args.wait
    while time.monotonic() < deadline:
        if not (SPOOL / 'open.request').exists():
            return
        time.sleep(0.25)
    sys.exit(f'Supervisor {pid} did not open the room within {args.wait}s; see {SPOOL / "status.json"}.')


def stop(args):
    status = read_json(SPOOL / 'status.json') or {}
    pid = status.get('supervisor_pid')
    if not supervisor_alive(pid):
        print('No supervisor is running.')
        sys.exit(3)
    write_json(SPOOL / 'stop.request', {'pid': pid, 'time': now()})
    deadline = time.monotonic() + args.wait
    while time.monotonic() < deadline:
        time.sleep(1)
        if not supervisor_alive(pid):
            print(f'Room closed ({(read_json(SPOOL / "status.json") or {}).get("state")}). Transcript and worktrees are saved.')
            return
    sys.exit(f'Supervisor {pid} did not stop within {args.wait}s (a deployment may be finishing); see {SPOOL / "status.json"}.')


def bootstrap(args):
    """External, one time: install a gate-capable release into a stopped, unsupervised room, then start it.

    Same backup, atomic apply and snapshot as a deployment. The supervisor's first start verifies the
    release behind the gate; if that fails it restores the previous files and data and restarts them.
    """
    sup = Supervisor()
    sup.acquire()
    if read_json(SPOOL / 'journal.json'):
        sys.exit('A deployment journal exists; resolve it with "supervisor.py recover" first.')
    if read_json(SPOOL / 'current.json'):
        sys.exit('This room already runs a supervised release; deploy through the room instead.')
    if not sup.server_down(wait_seconds=5):
        sys.exit('Stop the room first (Stop-Room.ps1), after its agents are idle.')
    deployment = Deployment.__new__(Deployment)
    deployment.sup = sup
    deployment.journal = {'id': f'bootstrap-{args.release_id}-{int(time.time())}', 'phase': 'taken', 'steps': [],
                          'started': now(), 'bootstrap': True, 'was_paused': True}
    deployment.release_dir = SPOOL / 'releases' / args.release_id
    deployment.manifest, deployment.release = release_pack.check_release(deployment.release_dir)
    rehearsal = read_json(deployment.release_dir / 'rehearsal.json') or {}
    if (rehearsal.get('release_id'), rehearsal.get('manifest_sha'), rehearsal.get('status')) != \
            (args.release_id, deployment.release['manifest_sha'], 'passed'):
        sys.exit(f'Release {args.release_id} has no passed rehearsal.')
    deployment.previous = None
    deployment.backup = SPOOL / 'backups' / deployment.journal['id']
    deployment.journal.update(release_id=args.release_id, previous_release_id=None, backup=str(deployment.backup),
                              request={'approved_by': args.approved_by, 'bootstrap': True})
    sup.set_status = lambda *a, **k: None  # No supervisor is running yet; status belongs to the next start.
    deployment.plan()
    deployment.snapshot()
    deployment.apply()
    deployment.journal.update(phase='restart_verify', expect=args.release_id, outcome='deployed')
    write_json(SPOOL / 'journal.json', deployment.journal)
    sup.gate('closed', 'blocked', deployment.journal['id'], reason='Bootstrap: verifying the first supervised release')
    print(f'Installed release {args.release_id} ({len(deployment.journal["plan"])} file changes); starting it behind the gate.')
    for handle in sup.locks:  # The supervisor started next takes these locks itself.
        handle.close()
    if sup.lease:  # No child holds a duplicate yet, so it closes at once.
        sup.lease.close()
    start(args)


def recover(args):
    sup = Supervisor()
    sup.acquire()
    journal = read_json(SPOOL / 'journal.json')
    if not journal:
        sys.exit('No interrupted deployment.')
    if not sup.server_down(wait_seconds=5):
        sys.exit('The room server is still running; stop it before recovering.')
    if journal.get('phase') == 'restart_verify':
        sys.exit('Recovery already done; start the room with Start-Room to verify it.')
    if journal.get('phase') in ('reopening', 'reopened'):
        # A verified release was reopening and may have accepted writes: keep it and its new records.
        journal.update(phase='restart_verify', expect=journal.get('reopen_expect'), outcome=journal.get('reopen_outcome', 'deployed'))
        note = f'Kept release {journal.get("reopen_expect")}; nothing restored because the room may have accepted new records.'
    else:
        deployment = Deployment.__new__(Deployment)
        deployment.sup, deployment.journal = sup, journal
        conflicts = deployment.restore()
        if conflicts:
            Deployment.finish_receipt(journal, 'conflict', f'Recovery conflicts, nothing started: {conflicts[:20]}')
            sys.exit(f'Recovery conflicts: {conflicts}')
        journal.update(phase='restart_verify', expect=journal.get('previous_release_id'), outcome='rolled_back')
        note = f'Restored files and data from before deployment {journal["id"]}.'
    journal.setdefault('steps', []).append({'time': now(), 'step': note + ' Next start verifies before reopening.'})
    write_json(SPOOL / 'journal.json', journal)
    sup.gate('closed', 'blocked', journal['id'], reason='Recovered; verifying on next start')
    print(note + ' Start the room with Start-Room; the supervisor verifies it before reopening.')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('start').add_argument('--wait', type=int, default=120)
    sub.add_parser('run').add_argument('--nonce', default='')
    sub.add_parser('stop').add_argument('--wait', type=int, default=120)
    sub.add_parser('open').add_argument('--wait', type=int, default=15)
    sub.add_parser('recover')
    b = sub.add_parser('bootstrap')
    b.add_argument('release_id')
    b.add_argument('--approved-by', required=True, help='Who approved this release in the room')
    b.add_argument('--wait', type=int, default=180)
    args = parser.parse_args()
    {'start': start, 'run': lambda a: Supervisor(a.nonce).run(), 'stop': stop, 'open': open_room, 'recover': recover,
     'bootstrap': bootstrap}[args.command](args)


if __name__ == '__main__':
    main()
