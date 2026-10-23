"""Persistent desktop owner; the frozen Supervisor still executes deployments."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time
import traceback


def main():
    parser = argparse.ArgumentParser()
    for name in ('profile', 'expected-profile', 'packages', 'room-root'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--owner', type=int, required=True)
    parser.add_argument('--development', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root))
    import backend_host as host
    commands = host.isolate_commands_input()
    parent_job = host.attach_parent_job(args.owner)
    sys.path.insert(0, str(root / 'backend' / 'src'))
    import site
    dev = json.loads((root / 'development.json').read_text('utf-8')) if args.development else None
    if Path(args.packages).resolve() != (Path(dev['packages']).resolve() if dev else root / 'python/Lib/site-packages'):
        raise ValueError('Invalid packages path')
    site.addsitedir(args.packages)
    from desktop_profile import open_profile
    from zerocollab.runtime.desktop_policy import local_folder
    from zerocollab.runtime.config_loader import load_config, room_paths
    import desktop_release_pack
    import zerocollab.releases as _release_package
    _release_package.release_pack = desktop_release_pack
    sys.modules['zerocollab.releases.release_pack'] = desktop_release_pack
    import desktop_supervisor as engine
    import desktop_room_writer as writer
    room_root = local_folder(args.room_root)
    with contextlib.redirect_stdout(io.StringIO()):
        original = load_config(room_root)
    paths = room_paths(original, room_root)
    data = local_folder(paths['data'])
    host.emit({'type': 'selection', 'room_root': str(room_root), 'data': str(data)})
    # Only the deployment engine's location inputs change. Its request, drain,
    # CAS, snapshot, verify, rollback and recovery algorithms remain frozen.
    engine.LIVE, engine.DATA, engine.SPOOL = room_root, data, data / 'deploy'
    engine.HANDBACK = engine.SPOOL / 'handback.json'
    with host.room_listener(data) as probe:
        port = probe.getsockname()[1]
    quit_event = threading.Event()

    class DesktopSupervisor(engine.Supervisor):
        def __init__(self):
            super().__init__(secrets.token_hex(16))
            self.client.close()
            self.url = f'http://127.0.0.1:{port}'
            self.room_url = f'http://zerocollab.localhost:{port}'
            self.client = engine.httpx.Client(base_url=self.url, timeout=20, trust_env=False,
                                             headers={'X-Session-Token': self.ui_token})
            self.status.update(url=self.url, host='desktop')
            self.generation = 0
            self.pending_ready = None
            self.published = False
            self.backend_job = None
            self.close_ack = False
            self.transaction = False
            self.view_updating = False

        def acquire(self):
            # One owner takes all three locks once, before any backend exists.
            for path in (engine.SPOOL / 'supervisor.lock', data / 'deploy-helper.lock'):
                handle = engine.lock(path)
                if not handle:
                    raise RuntimeError('Room supervisor is already in use')
                self.locks.append(handle)
            self.lease = writer.open_room_writer(data, kind='desktop', supervisor=True)
            self.status['data_lease'] = 'held'
            self.start_stopped = None

        def session(self, wait_seconds=0):
            deadline = time.monotonic() + wait_seconds
            while True:
                try:
                    self.client.get('/api/runtime', timeout=5).raise_for_status()
                    return
                except engine.httpx.HTTPError:
                    if self.child and self.child.poll() is not None:
                        raise RuntimeError('Owned desktop backend exited')
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.2)

        def set_status(self, state=None, **values):
            super().set_status(state, **values)
            if state == 'running' and self.pending_ready and not quit_event.is_set():
                value, self.pending_ready = self.pending_ready, None
                value.update(job=parent_job, generation=self.generation, supervisor_pid=os.getpid())
                host.emit(value)
                self.published = True
                self.view_updating = False

        def start_child(self):
            if not self.server_down(wait_seconds=0):
                raise RuntimeError('Previous backend tree is not confirmed stopped')
            self.generation += 1
            self.close_ack = False
            self.pending_ready = None
            self.backend_job = None
            argv = [sys.executable, '-I', '-B', '-u', str(root / 'backend_host.py'),
                    '--owner', str(os.getpid()), '--profile', args.profile,
                    '--expected-profile', args.expected_profile, '--packages', args.packages,
                    '--room-root', str(room_root), '--supervised', *(['--development'] if args.development else [])]
            self.child = subprocess.Popen(argv, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=sys.stderr, creationflags=engine.NO_WINDOW)
            messages = queue.Queue()
            self.messages = messages
            child = self.child
            def read_protocol():
                try:
                    for raw in child.stdout:
                        if len(raw) > 16384:
                            raise ValueError('Backend protocol overflow')
                        value = json.loads(raw)
                        if value.get('type') in ('progress', 'startup-diagnostic'):
                            # Provider refresh completes after readiness. Keep
                            # forwarding that progress for the lifetime of the child.
                            host.emit(value)
                            continue
                        if value.get('type') == 'closed':
                            self.close_ack = value.get('runtime_closed') is True
                        messages.put(value)
                except Exception:
                    messages.put({'type': 'failed', 'reason': 'Invalid backend protocol'})
            self.reader = threading.Thread(target=read_protocol, daemon=True)
            self.reader.start()
            handoff = {'room_writer': self.lease.duplicate_for_child(child), 'ui_token': self.ui_token,
                       'session_hashes': self.session_hashes, 'port': port,
                       'start_stopped': bool(self.start_stopped), 'generation': self.generation}
            child.stdin.write((json.dumps(handoff) + '\n').encode())
            child.stdin.flush()
            engine.write_json(data / 'server-process.json', {'pid': child.pid,
                'startUtc': engine.creation_utc(child._handle), 'root': str(room_root),
                'supervisor_pid': os.getpid(), 'host': 'desktop'})
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    value = messages.get(timeout=.2)
                except queue.Empty:
                    if child.poll() is not None:
                        raise RuntimeError('Backend exited before readiness')
                    continue
                if value.get('type') == 'failed':
                    if not self.published:
                        host.emit(value)
                    raise RuntimeError('Desktop backend refused startup')
                if value.get('type') == 'ownership':
                    if type(value.get('job')) is not int or value['job'] <= 0:
                        raise ValueError('Invalid backend job')
                    self.backend_job = value['job']
                    continue
                if value.get('type') == 'ready':
                    self.backend_job = value['job']
                    self.pending_ready = value
                    self.start_stopped = None
                    engine.HANDBACK.unlink(missing_ok=True)
                    return
                if value.get('type') in ('progress', 'startup-diagnostic'):
                    host.emit(value)
            raise RuntimeError('Desktop backend readiness timed out')

        def stop_child(self, graceful=True):
            if self.child and self.child.poll() is None:
                if not graceful and self.published and not self.view_updating:
                    host.emit({'type': 'restarting', 'generation': self.generation})
                    self.transaction = True
                    self.view_updating = True
                try:
                    result = self.client.get('/api/runtime/ui-sessions', timeout=5)
                    result.raise_for_status()
                    self.session_hashes = result.json()['sessions']
                except (engine.httpx.HTTPError, ValueError, KeyError):
                    pass
                # Quit closes the app; it is not the user's Stop command.
                try:
                    self.child.stdin.write(b'quit\n')
                    self.child.stdin.flush()
                except (OSError, ValueError):
                    pass
                try:
                    self.child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    if self.backend_job is None:
                        return False
                    terminate_job(self.backend_job)
                    try:
                        self.child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        return False
                self.reader.join(timeout=2)
                try:
                    self.child.stdin.close()
                except OSError:
                    pass
                self.child.stdout.close()
            down = self.server_down()
            if down and self.backend_job is not None:
                close_job(self.backend_job)
                self.backend_job = None
            return down

        def server_down(self, wait_seconds=20):
            deadline = time.monotonic() + wait_seconds
            while True:
                exited = self.child is None or self.child.poll() is not None
                tree_down = self.backend_job is None or job_empty(self.backend_job)
                if exited and tree_down and self.port_free():
                    return True
                # Surviving descendants stay owned; end that exact job, never PID-discovered processes.
                if exited and not tree_down:
                    terminate_job(self.backend_job)
                if time.monotonic() >= deadline:
                    return False
                time.sleep(.1)

        def stop_requested(self, consume=False):
            return quit_event.is_set() or super().stop_requested(consume)

        def open_browser(self):
            host.emit({'type': 'show-window'})

    supervisor = DesktopSupervisor()
    def control():
        while True:
            raw = commands.readline(4097)
            if not raw:
                break
            if raw.strip() == b'quit':
                break
            if len(raw) > 4096:
                host.emit({'type': 'failed', 'reason': 'Desktop command exceeds bound'})
                break
            host.emit({'type': 'failed', 'reason': 'Unknown desktop command'})
            break
        quit_event.set()
        host.emit({'type': 'quit-pending'})
    threading.Thread(target=control, daemon=True).start()
    profile_lock = open_profile(Path(args.profile), Path(args.expected_profile))
    clean = False
    try:
        supervisor.run()
        clean = (quit_event.is_set() and supervisor.status.get('state') == 'stopped'
                 and supervisor.close_ack and supervisor.child.returncode == 0)
    finally:
        down = supervisor.stop_child(graceful=False)
        if down and supervisor.lease:
            supervisor.lease.close()
        for handle in supervisor.locks:
            handle.close()
        supervisor.client.close()
        profile_lock.close()
    if clean and down:
        host.emit({'type': 'closed', 'instance': '', 'runtime_closed': True, 'supervisor_closed': True})
    else:
        raise RuntimeError('Desktop host stopped without a clean owned shutdown')


def job_api():
    import ctypes
    from ctypes import wintypes as W
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.QueryInformationJobObject.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD, ctypes.c_void_p]
    kernel.TerminateJobObject.argtypes = [W.HANDLE, W.UINT]
    kernel.CloseHandle.argtypes = [W.HANDLE]
    return ctypes, kernel


def job_empty(handle):
    ctypes, kernel = job_api()
    class Accounting(ctypes.Structure):
        _fields_ = [('user', ctypes.c_int64), ('kernel', ctypes.c_int64), ('period_user', ctypes.c_int64),
                    ('period_kernel', ctypes.c_int64), ('faults', ctypes.c_ulong),
                    ('total', ctypes.c_ulong), ('active', ctypes.c_ulong), ('terminated', ctypes.c_ulong)]
    value = Accounting()
    if not kernel.QueryInformationJobObject(handle, 1, ctypes.byref(value), ctypes.sizeof(value), None):
        raise ctypes.WinError(ctypes.get_last_error())
    return value.active == 0


def terminate_job(handle):
    ctypes, kernel = job_api()
    if not kernel.TerminateJobObject(handle, 1):
        raise ctypes.WinError(ctypes.get_last_error())


def close_job(handle):
    ctypes, kernel = job_api()
    if not kernel.CloseHandle(handle):
        raise ctypes.WinError(ctypes.get_last_error())


if __name__ == '__main__':
    try:
        main()
    except BaseException as exc:
        traceback.print_exc(file=sys.stderr)
        import backend_host
        backend_host.emit({'type': 'failed', 'reason': type(exc).__name__, 'handover_code': getattr(exc, 'code', None)})
        sys.exit(1)
