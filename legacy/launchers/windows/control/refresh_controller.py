"""Refresh only the controller copy after its owning tray has exited."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

FILES = ('ZeroCollab-Control.ps1', 'control_server.py', 'refresh_controller.py', 'ZeroCollab.ico')


def refresh(source, destination, version):
    """Stage the complete set first; restore the previous set if replacement fails."""
    destination.mkdir(parents=True, exist_ok=True)
    previous = {name: (destination / name).read_bytes() if (destination / name).is_file() else None for name in FILES}
    expected = {name: (source / name).read_bytes() for name in FILES}
    staged = []
    try:
        for name in FILES:
            path = destination / (name + '.refresh-tmp')
            staged.append(path)
            shutil.copyfile(source / name, path)
            if path.read_bytes() != expected[name]:
                raise ValueError('Controller copy verification failed: ' + name)
        if any((source / name).read_bytes() != expected[name] for name in FILES):
            raise ValueError('Controller source changed during refresh')
        for name, path in zip(FILES, staged):
            os.replace(path, destination / name)
        version_temp = destination / 'controller-version.json.refresh-tmp'
        version_temp.write_text(json.dumps({'version': version}), encoding='utf-8')
        os.replace(version_temp, destination / 'controller-version.json')
    except Exception:
        for name, data in previous.items():
            path = destination / name
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(data)
        raise
    finally:
        for path in staged:
            path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument('--parent-pid', required=True, type=int)
    parser.add_argument('--tray-only', action='store_true')
    args = parser.parse_args()
    root, destination = args.root.resolve(), args.destination.resolve()
    expected = (Path(os.environ['LOCALAPPDATA']) / 'ZeroCollabControl').resolve()
    if destination != expected:
        raise ValueError('Only the installed ZeroCollabControl copy can be refreshed')
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                    f'Wait-Process -Id {args.parent_pid} -Timeout 30 -ErrorAction SilentlyContinue; '
                    f'if (Get-Process -Id {args.parent_pid} -ErrorAction SilentlyContinue) {{ exit 1 }}'],
                   check=True, timeout=35, creationflags=subprocess.CREATE_NO_WINDOW)
    source = root / 'windows/control'
    signature = hashlib.sha256(''.join(hashlib.sha256((source / name).read_bytes()).hexdigest()
                             if (source / name).is_file() else 'missing' for name in FILES).encode()).hexdigest()
    try:
        refresh(source, destination, (root / 'VERSION').read_text('utf-8').strip())
        (destination / 'controller-refresh-error.json').unlink(missing_ok=True)
    except Exception as exc:
        (destination / 'controller-refresh-error.json').write_text(json.dumps({'source_sha': signature, 'error': str(exc)}), encoding='utf-8')
        raise
    finally:
        # On copy failure the restored previous controller is relaunched too.
        argv = ['powershell.exe', '-NoProfile', '-STA', '-WindowStyle', 'Hidden', '-File',
                str(destination / 'ZeroCollab-Control.ps1'), '-RoomRoot', str(root)]
        if args.tray_only:
            argv.append('-TrayOnly')
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)


if __name__ == '__main__':
    main()
