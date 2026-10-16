"""Private stdio launcher for the independently owned desktop room. No HTTP control route."""
import argparse
import asyncio
import ctypes
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import contextlib
import io
import re
import traceback
import faulthandler
import importlib.util

CONNECTION_SHUTDOWN_SECONDS = 5


def attach_parent_job(owner):
    if os.name != 'nt' or owner != os.getppid():
        raise RuntimeError('owner_mismatch')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    class Basic(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                    ('flags', W.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                    ('active', W.DWORD), ('affinity', ctypes.c_size_t), ('priority', W.DWORD), ('scheduling', W.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ('read', 'write', 'other', 'read_bytes', 'write_bytes', 'other_bytes')]
    class Extended(ctypes.Structure):
        _fields_ = [('basic', Basic), ('io', IO), ('process_mem', ctypes.c_size_t), ('job_mem', ctypes.c_size_t),
                    ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, W.LPCWSTR]
    kernel.CreateJobObjectW.restype = W.HANDLE
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    kernel.GetCurrentProcess.restype = W.HANDLE
    kernel.SetInformationJobObject.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
    kernel.DuplicateHandle.argtypes = [W.HANDLE, W.HANDLE, W.HANDLE, ctypes.POINTER(W.HANDLE), W.DWORD, W.BOOL, W.DWORD]
    kernel.CloseHandle.argtypes = [W.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    info = Extended()
    info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    parent = kernel.OpenProcess(0x0040, False, owner)  # PROCESS_DUP_HANDLE only
    if not parent:
        kernel.CloseHandle(job)
        raise ctypes.WinError(ctypes.get_last_error())
    current = kernel.GetCurrentProcess()
    remote = W.HANDLE()
    # Transfer first so that failure does not close a kill-on-close job containing us.
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.DuplicateHandle(current, job, parent, ctypes.byref(remote), 0, False, 2):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.AssignProcessToJobObject(job, current):
        raise ctypes.WinError(ctypes.get_last_error())
    kernel.CloseHandle(job)
    kernel.CloseHandle(parent)
    return int(remote.value)


_emit_lock = threading.Lock()


def emit(value):
    with _emit_lock:
        print(json.dumps(value), flush=True)  # private pipe, main never logs handshake



def startup_locations(items):
    # Locations only: no locals, source lines, paths or exception messages.
    frames = []
    for item in items[-12:]:
        file = Path(item.filename).name
        if re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', file) and re.fullmatch(r'[A-Za-z0-9_<>]{1,80}', item.name):
            frames.append({'file': file, 'line': item.lineno, 'function': item.name})
    return frames


def startup_frames(frame):
    return startup_locations(traceback.extract_stack(frame) if frame else [])


def startup_snapshot(task, thread_id):
    waiting = []
    current = task.get_coro()
    for _ in range(64):
        frame = getattr(current, 'cr_frame', None) or getattr(current, 'gi_frame', None)
        if frame:
            waiting.extend(startup_frames(frame)[-1:])
        current = getattr(current, 'cr_await', None) or getattr(current, 'gi_yieldfrom', None)
        if current is None:
            break
    emit({'type': 'startup-diagnostic', 'kind': 'watchdog',
          'stack': startup_frames(sys._current_frames().get(thread_id)), 'awaiting': waiting[-12:]})



def isolate_commands_input():
    # Node's control pipe must never be the standard input inherited by child processes.
    # Keep an unbuffered, non-inheritable duplicate for our sole control reader.
    commands_in = os.fdopen(os.dup(0), 'rb', buffering=0)
    null_in = os.open(os.devnull, os.O_RDONLY)
    try:
        os.dup2(null_in, 0)
    finally:
        os.close(null_in)
    return commands_in


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--supervised', action='store_true')
    parser.add_argument('--owner', required=True, type=int)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--expected-profile', required=True)
    parser.add_argument('--packages', required=True)
    parser.add_argument('--room-root', required=True)
    args = parser.parse_args()
    commands_in = isolate_commands_input()
    faulthandler.enable(file=sys.stderr, all_threads=True)
    parent_job = attach_parent_job(args.owner)
    if args.supervised:
        emit({'type': 'ownership', 'job': parent_job})
    root = Path(__file__).resolve().parent
    source = root / 'backend'
    profile = Path(args.profile).resolve()
    # Electron derives the Windows path once; the backend never infers it from APPDATA.
    sys.path.insert(0, str(root))
    from desktop_profile import open_profile
    lock = contextlib.nullcontext() if args.supervised else open_profile(Path(args.profile), Path(args.expected_profile))
    try:
        run_owned(args, root, source, profile, parent_job, commands_in)
    finally:
        if not args.supervised:
            lock.close()
        commands_in.close()


def run_owned(args, root, source, profile, parent_job, commands_in):
    os.environ.pop('ZC_REHEARSAL', None)
    for key in list(os.environ):
        if key.upper().startswith('ZEROCOLLAB_'):
            os.environ.pop(key, None)
    sys.path.insert(0, str(source))
    # Bundled pywin32 needs its .pth paths and DLL bootstrap. No user site.
    import site
    packages = Path(args.packages).resolve()
    if packages != root / 'python/Lib/site-packages':
        raise RuntimeError('Invalid bundled packages path')
    site.addsitedir(str(packages))
    os.chdir(source)
    from desktop_policy import local_folder, HandoverRefused
    # Bootstrap only these host-pinned helpers; selected release modules import their own copies.
    def bootstrap(name):
        spec = importlib.util.spec_from_file_location('_desktop_' + name, source / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    config = bootstrap('config_loader')
    writer = bootstrap('room_writer')
    load_config, room_paths = config.load_config, config.room_paths
    open_room_writer, RoomInUse = writer.open_room_writer, writer.RoomInUse
    try:
        room_root = local_folder(args.room_root)
        if not (room_root / 'config.toml').is_file():
            raise HandoverRefused('invalid-room')
        # Loader warnings contain selected config labels; never mix them into the private protocol.
        with contextlib.redirect_stdout(io.StringIO()):
            original = load_config(room_root)
        paths = room_paths(original, room_root)
        data, uploads = local_folder(paths['data']), local_folder(paths['uploads'])
        emit({'type': 'selection', 'room_root': str(room_root), 'data': str(data)})
    except HandoverRefused:
        raise
    except (OSError, ValueError, TypeError):
        raise HandoverRefused('invalid-room') from None
    try:
        handoff = None
        if args.supervised:
            line = commands_in.readline(65537)
            if len(line) > 65536:
                raise ValueError('Supervisor bootstrap too large')
            handoff = json.loads(line)
            if (not isinstance(handoff, dict) or type(handoff.get('port')) is not int
                    or not 1024 <= handoff['port'] <= 65535
                    or not re.fullmatch(r'[a-f0-9]{64}', handoff.get('ui_token', ''))
                    or type(handoff.get('start_stopped')) is not bool
                    or type(handoff.get('generation')) is not int or handoff['generation'] < 1):
                raise ValueError('Invalid supervisor bootstrap')
            lease = writer.adopt_room_writer(data, handoff['room_writer'], kind='desktop')
        else:
            lease = open_room_writer(data, kind='desktop')
    except RoomInUse:
        raise HandoverRefused('room-in-use') from None
    except ValueError:
        raise HandoverRefused('owner-state-invalid') from None
    try:
        # The installer owns room code. The selected runtime folder owns only its saved data/config.
        identity = {'source': 'bundled', 'version': (source / 'VERSION').read_text(encoding='utf-8').strip()}
        from desktop_policy import configure as desktop_configure
        serve_owned(args, root, source, profile, parent_job, room_root, original, data, uploads,
                    lease, desktop_configure, commands_in, identity, handoff)
    finally:
        lease.close()


def serve_owned(args, root, source, profile, parent_job, room_root, original, data, uploads, lease, desktop_configure, commands_in, backend_identity, handoff):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', handoff['port'] if handoff else 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    if port in (8300, 8200, 8201):
        raise RuntimeError('Live/control port collision')
    reserved = [socket.socket(socket.AF_INET, socket.SOCK_STREAM) for _ in range(2)]
    for reserve in reserved:
        reserve.bind(('127.0.0.1', 0))
    cfg = desktop_configure(original, room_root, data, uploads, port=port,
        mcp_ports=[reserve.getsockname()[1] for reserve in reserved], encoder=root / 'encoder' / 'bin' / 'ffmpeg.exe')
    emit({'type': 'progress', 'stage': 'import-app'})
    token = handoff['ui_token'] if handoff else secrets.token_hex(32)
    instance = secrets.token_hex(16)
    import app as room
    emit({'type': 'progress', 'stage': 'configure-room'})
    from ui_sessions import BrowserSessions
    room.configure(cfg, session_token=token, sessions=BrowserSessions(port, handoff['session_hashes'] if handoff else []))
    class DesktopNavigation:
        """Keep the public Electron document on its constrained loopback origin.

        Browser open_room still uses the cookie host. This only suppresses the
        convenience redirect on public GET /, never API authentication.
        """
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope['type'] == 'http' and scope.get('method') == 'GET' and scope.get('path') == '/':
                headers = scope.get('headers', [])
                host = next((value for name, value in headers if name == b'host'), b'')
                if host == f'127.0.0.1:{port}'.encode():
                    scope = {**scope, 'headers': [(name, value) for name, value in headers if name != b'sec-fetch-mode']}
            await self.app(scope, receive, send)
    room.app.add_middleware(DesktopNavigation)
    import desktop_release_pack
    sys.modules['release_pack'] = desktop_release_pack
    from room_runtime import install
    emit({'type': 'progress', 'stage': 'install-runtime'})
    runtime = install(room)
    runtime.release_manager.live_root = room_root
    from run import mount_web
    emit({'type': 'progress', 'stage': 'mount-workspace'})
    build_id = mount_web(room.app, source / 'static', session_token=None)
    version = (source / 'VERSION').read_text().strip()

    @room.app.get('/api/version')
    async def current_version():
        return {'version': version, 'id': build_id, 'desktop': True}


    @room.app.get('/api/desktop-instance')
    async def identity():
        return {'instance': instance, 'mode': 'desktop', 'agents': len(runtime.states)}

    connection_checks = None
    server_loop = None

    @room.app.on_event('startup')
    async def startup():
        room.set_event_loop(asyncio.get_running_loop())
        nonlocal connection_checks, server_loop
        server_loop = asyncio.get_running_loop()
        runtime.desktop_refresh_pending = True
        emit({'type': 'progress', 'stage': 'start-room'})
        watchdog = threading.Timer(10, startup_snapshot, args=(asyncio.current_task(), threading.get_ident()))
        watchdog.daemon = True
        watchdog.start()
        faulthandler.dump_traceback_later(10, repeat=True, file=sys.stderr)
        try:
            await runtime.start(refresh_providers=False)
        except BaseException as exc:
            # Cancellation after parent EOF must be distinguishable from an earlier startup exception.
            emit({'type': 'startup-diagnostic', 'kind': 'exception', 'error': type(exc).__name__,
                  'stack': startup_locations(traceback.extract_tb(exc.__traceback__)), 'awaiting': []})
            if isinstance(exc, Exception):
                emit({'type': 'failed', 'reason': type(exc).__name__})
            raise
        finally:
            faulthandler.cancel_dump_traceback_later()
            watchdog.cancel()

        async def check_connections():
            emit({'type': 'progress', 'stage': 'check-providers'})
            try:
                async with runtime.profile_lock:
                    await runtime.refresh()
            except Exception:
                for state in runtime.states.values():
                    if not state.get('desktop_unavailable'):
                        state.update(state='blocked', detail='Provider checks failed. Click Refresh connections to retry.')
            finally:
                runtime.desktop_refresh_pending = False
            emit({'type': 'progress', 'stage': 'providers-checked'})

        connection_checks = asyncio.create_task(check_connections())
        emit({'type': 'ready', 'port': port, 'instance': instance, 'token': token, 'job': parent_job,
              'mode': 'desktop', 'agents': len(runtime.states), 'version': version, 'backend': backend_identity,
              'generation': handoff['generation'] if handoff else 1, 'backend_pid': os.getpid(),
              'cli_logins': 'shared', 'managed_updates': runtime.cfg['runtime']['managed_updates'],
              'automatic_tool_approvals': runtime.cfg['runtime']['automatic_tool_approvals'],
              'native_providers': runtime.cfg['runtime']['native_providers'],
              'manager_disabled': runtime.cfg['runtime']['desktop_manager_disabled'],
              'stopped': runtime.paused, 'writer_owned': True, 'room_root': str(room_root), 'data': str(runtime.data)})

    @room.app.on_event('shutdown')
    async def shutdown():
        if connection_checks is not None:
            connection_checks.cancel()
            await asyncio.gather(connection_checks, return_exceptions=True)
        await runtime.close(preserve_state=True)
        emit({'type': 'closed', 'instance': instance, 'runtime_closed': True})

    import uvicorn
    # A Windows transport can retain wait_closed after its HTTP task has ended.
    # Bound connection drain so lifespan still runs runtime.close and emits its
    # acknowledgement before the parent's owned-process deadline.
    server = uvicorn.Server(uvicorn.Config(room.app, host='127.0.0.1', port=port, log_level='warning',
                                         access_log=False, timeout_graceful_shutdown=CONNECTION_SHUTDOWN_SECONDS))
    def shutdown_snapshot():
        # Bounded locations only, using the existing startup diagnostic protocol.
        # A hung close must show what it is awaiting before the owner times out.
        for task in list(asyncio.all_tasks())[:8]:
            startup_snapshot(task, threading.get_ident())
    def commands():
        for line in commands_in:
            if line.strip() == b'quit':
                server.should_exit = True
                if server_loop is not None:
                    server_loop.call_soon_threadsafe(server_loop.call_later, 5, shutdown_snapshot)
                return
        server.should_exit = True  # parent pipe lost; never orphan or restart
    threading.Thread(target=commands, daemon=True).start()
    server.run(sockets=[listener])
    for reserve in reserved:
        reserve.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        # The private protocol carries only fixed exception metadata; stderr keeps the actual error.
        emit({'type': 'failed', 'reason': type(exc).__name__,
              'handover_code': getattr(exc, 'code', None),
              'code': getattr(exc, 'winerror', None), 'parent_pid': os.getppid(),
              'module': exc.name if isinstance(exc, ModuleNotFoundError) else None,
              'owner_mismatch': str(exc) == 'owner_mismatch'})
        sys.exit(1)
