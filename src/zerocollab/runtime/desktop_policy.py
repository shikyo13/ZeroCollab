"""Desktop policy for opening an existing room; never imports that room's code."""
import copy
import ctypes
import os
from pathlib import Path
import re
import ast
import json
import socket


class HandoverRefused(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def prepared_room(root):
    """Read declarations as data, never import the selected room's Python code."""
    for name in ('run.py', 'supervisor.py'):
        try:
            file = root / name
            if file.stat().st_size > 2_000_000:
                raise ValueError()
            tree = ast.parse(file.read_text('utf-8-sig'))
            version = next((node.value.value for node in tree.body
                            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                            and any(isinstance(target, ast.Name) and target.id == 'ROOM_WRITER_PROTOCOL'
                                    for target in node.targets)), None)
            if type(version) is not int or version < 1:
                raise ValueError()
        except (OSError, ValueError, SyntaxError, StopIteration):
            raise HandoverRefused('upgrade-required') from None
    if not (root / 'room_writer.py').is_file() and not (root / 'src/zerocollab/runtime/room_writer.py').is_file():
        raise HandoverRefused('upgrade-required')


def legacy_in_use(cfg, data):
    """Positive evidence only. Never kill a process or claim PID ownership."""
    ports = [cfg.get('server', {}).get('port', 8300), cfg.get('mcp', {}).get('http_port', 8200),
             cfg.get('mcp', {}).get('sse_port', 8201)]
    if any(type(port) is not int or not 1 <= port <= 65535 for port in ports):
        raise HandoverRefused('invalid-room')
    for port in ports:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                raise HandoverRefused('room-in-use')
        except OSError:
            pass
    # The sole supervisor-spool read is its bounded legacy PID evidence.
    status = data / 'deploy' / 'status.json'
    if not status.exists():
        return
    try:
        if status.stat().st_size > 65536:
            raise ValueError()
        pid = json.loads(status.read_text('utf-8')).get('supervisor_pid')
        if pid is None:
            return
        if type(pid) is not int or pid <= 0:
            raise ValueError()
    except (OSError, ValueError, AttributeError):
        raise HandoverRefused('owner-state-invalid') from None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
    if not handle:
        if ctypes.get_last_error() == 5:
            raise HandoverRefused('room-in-use')
        return
    try:
        if kernel.WaitForSingleObject(handle, 0) != 0:
            raise HandoverRefused('room-in-use')
    finally:
        kernel.CloseHandle(handle)


UNAVAILABLE = 'Unavailable in desktop. Saved settings are kept for the web room.'


def local_folder(value):
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError('Choose an existing absolute folder on a local fixed drive.')
    # Check both the selected spelling and resolved target (junctions/mapped drives).
    resolved = path.resolve(strict=True)
    if os.name != 'nt':
        raise ValueError('Existing-room desktop handover is available on Windows only.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel.GetDriveTypeW.restype = ctypes.c_uint
    for candidate in (path, resolved):
        if not re.match(r'^[A-Za-z]:\\', str(candidate)) or kernel.GetDriveTypeW(candidate.anchor) != 3:
            raise ValueError('Choose a local fixed drive. Network and mapped drives cannot own this room.')
    return resolved


def unavailable_reason(cfg, name=''):
    provider = cfg.get('provider', name)
    if provider not in ('codex', 'claude', 'antigravity', 'api'):
        return UNAVAILABLE
    if provider == 'api':
        return ''
    # Desktop uses its own native adapter, not a saved wrapper, script or argv.
    executable = 'agy' if provider == 'antigravity' else provider
    command = cfg.get('command', executable)
    if command not in (executable, executable + '.exe') or cfg.get('type', 'native') not in ('native', provider):
        return UNAVAILABLE
    if any(cfg.get(key) for key in ('args', 'cli_args', 'flags', 'yolo', 'dangerously_skip_permissions')):
        return UNAVAILABLE
    return ''


def template(cfg, name=''):
    result = copy.deepcopy(cfg)
    reason = unavailable_reason(result, name)
    if reason:
        result['_desktop_unavailable'] = reason
    else:
        result.pop('_desktop_unavailable', None)
        result.pop('automatic_tool_approvals', None)
    return result


MAX_VERSION_BYTES = 64


def require_matching_version(room_root, source):
    """Never open a newer or different room schema with this packaged backend."""
    def version(root):
        with (root / 'VERSION').open('rb') as stream:
            raw = stream.read(MAX_VERSION_BYTES + 1)
        if len(raw) > MAX_VERSION_BYTES:
            raise ValueError('Invalid room version')
        value = raw.decode('ascii').strip()
        if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', value):
            raise ValueError('Invalid room version')
        return value
    try:
        if version(room_root) != version(source):
            raise ValueError('Different room version')
    except (OSError, ValueError):
        raise HandoverRefused('version-mismatch') from None


def require_shared_homes(data):
    """Compare only the web room's paths-only marker before importing app/providers."""
    from zerocollab.providers.cli_homes import check_marker, effective_homes
    try:
        status = check_marker(data, effective_homes())
    except (OSError, ValueError):
        raise HandoverRefused('cli-home-mismatch') from None
    if status != 'ok':
        raise HandoverRefused('cli-home-' + status)


def configure(original, room_root, data, uploads, *, port, mcp_ports):
    """Use normalized shared config paths, then apply immutable desktop guards."""
    room_root, data, uploads = (local_folder(p) for p in (room_root, data, uploads))
    if not (room_root / 'config.toml').is_file():
        raise ValueError('Choose the runtime folder containing config.toml.')
    cfg = copy.deepcopy(original)
    server, mcp = cfg.setdefault('server', {}), cfg.setdefault('mcp', {})
    configured = [server.get('port', 8300), mcp.get('http_port', 8200), mcp.get('sse_port', 8201)]
    blocked = [8300, 8200, 8201, 8301, *configured, port, *mcp_ports]
    if any(type(p) is not int or not 1 <= p <= 65535 for p in blocked):
        raise ValueError('The room configuration contains an invalid listener port.')
    # Do not carry LAN/allowed-host overrides from an existing room into desktop.
    cfg['server'] = {'host': '127.0.0.1', 'port': port, 'data_dir': str(data)}
    cfg['mcp'] = {'http_port': mcp_ports[0], 'sse_port': mcp_ports[1]}
    cfg.setdefault('images', {})['upload_dir'] = str(uploads)
    cfg.setdefault('browser', {})['blocked_ports'] = sorted(set(blocked))
    cfg.setdefault('runtime', {}).update(automatic_tool_approvals=bool(original.get('runtime', {}).get('automatic_tool_approvals', True)), managed_updates=True,
        native_providers=['codex', 'claude', 'antigravity'], desktop_existing_room=True, desktop_isolated_cli=False,
        desktop_manager_disabled=False)
    cfg['agents'] = {name: {**template(value, name), 'managed': True}
                     for name, value in cfg.get('agents', {}).items()}
    cfg.pop('motion', None)  # Media dependencies belong to the separately installed tool.
    return cfg
