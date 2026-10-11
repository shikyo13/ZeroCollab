"""Loopback controller shared by the tray and Server controls. No room credentials."""
import argparse
import hashlib
import json
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CONTROL_FILES = ('ZeroCollab-Control.ps1', 'control_server.py', 'refresh_controller.py', 'ZeroCollab.ico')
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def controller_identity(root, folder):
    def fingerprint(path):
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    source = root / 'windows' / 'control'
    in_sync = all(fingerprint(source / name) is not None and
                  fingerprint(source / name) == fingerprint(folder / name) for name in CONTROL_FILES)
    signature = hashlib.sha256(''.join(fingerprint(source / name) or 'missing' for name in CONTROL_FILES).encode()).hexdigest()
    try:
        failed = json.loads((folder / 'controller-refresh-error.json').read_text('utf-8-sig'))
        refresh_error = failed.get('error', '') if failed.get('source_sha') == signature else ''
    except (OSError, ValueError, AttributeError):
        refresh_error = ''
    try:
        version = json.loads((folder / 'controller-version.json').read_text('utf-8-sig')).get('version', '')
    except (OSError, ValueError, AttributeError):
        version = ''
    if folder.resolve() == source.resolve():
        version = (root / 'VERSION').read_text('utf-8').strip()
    return {'connected': True, 'version': version or 'Unknown', 'in_sync': in_sync, 'refresh_error': refresh_error}


def execute(root, command, body):
    """Runs as a hidden worker, using the supervisor and the shared approval path."""
    sys.path.insert(0, str(root))
    from room_deploy import DeploymentState, approve_release
    from supervisor import supervisor_alive
    python = root / '.venv' / 'Scripts' / 'python.exe'
    supervisor = root / 'supervisor.py'
    script = Path(__file__).with_name('ZeroCollab-Control.ps1')
    if command in ('Start', 'Stop', 'Restart', 'Open'):
        if command == 'Open':
            subprocess.run([str(python), '-X', 'utf8', str(supervisor), 'open'], check=True, creationflags=NO_WINDOW)
        else:
            subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(script),
                            '-RoomRoot', str(root), '-Action', command], check=True, creationflags=NO_WINDOW)
        return
    state = DeploymentState(root / 'data')
    status = state.snapshot()['supervisor']
    approval = approve_release(state, body, allow_offline=True,
                               supervisor_online=supervisor_alive(status.get('supervisor_pid')))
    transport = approval.get('transport')
    if transport == 'bootstrap':
        subprocess.run([str(python), '-X', 'utf8', str(supervisor), 'bootstrap', approval['release_id'],
                        '--approved-by', 'user'], check=True, creationflags=NO_WINDOW)
    elif transport == 'start':
        subprocess.run([str(python), '-X', 'utf8', str(supervisor), 'start'], check=True, creationflags=NO_WINDOW)
        # Repeat fingerprint/rehearsal/current/pending validation after startup.
        approve_release(DeploymentState(root / 'data'), body)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8301)
    parser.add_argument('--execute', choices=('Start', 'Stop', 'Restart', 'Open', 'Install'))
    parser.add_argument('--release-id', default='')
    parser.add_argument('--manifest-sha', default='')
    args = parser.parse_args()
    root = args.root.resolve()
    if args.execute:
        execute(root, args.execute, {'release_id': args.release_id, 'manifest_sha': args.manifest_sha})
        return
    sys.path.insert(0, str(root))
    from room_deploy import DeploymentState
    from supervisor import room_port, supervisor_alive
    origins = {f'http://{host}:{room_port()}' for host in ('127.0.0.1', 'localhost', 'zerocollab.localhost')}
    token = secrets.token_urlsafe(32)
    worker, action, message = None, '', ''
    lock = threading.Lock()
    last_check, last_pid, last_alive = 0, None, False

    def status():
        nonlocal worker, message, last_check, last_pid, last_alive
        if worker is not None and worker.poll() is not None:
            message = f'{action} submitted.' if worker.returncode == 0 else f'{action} failed. See controller-web-error.log and supervisor.log.'
            worker = None
        snapshot = DeploymentState(root / 'data').snapshot()
        state = snapshot['supervisor']
        pid = state.get('supervisor_pid')
        if pid != last_pid or time.monotonic() - last_check > 2:
            last_alive = supervisor_alive(pid)
            last_pid, last_check = pid, time.monotonic()
        gate = snapshot['maintenance']
        deploying = bool(snapshot['pending']) or gate['admission'] == 'closed' or gate['writes'] == 'blocked' or (root / 'data/deploy/journal.json').exists()
        candidate = next((r for r in snapshot['releases'] if r['relation'] == 'newer' and r['rehearsal_passed']), None)
        receipt = snapshot['receipts'][0] if snapshot['receipts'] else None
        return dict(state=state.get('state', 'unknown') if last_alive else 'stopped', alive=last_alive,
                    busy=worker is not None or deploying, action=action, message=message,
                    url=f'http://zerocollab.localhost:{room_port()}', token=token,
                    controller=controller_identity(root, Path(__file__).parent),
                    current=snapshot['current'], update=candidate, maintenance=gate,
                    progress=(state.get('step') or gate.get('reason', '')) if deploying else '', receipt=receipt)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, data):
            body = json.dumps(data).encode()
            self.send_response(code)
            for name, value in [('Content-Type', 'application/json'), ('Content-Length', str(len(body))),
                                ('Cache-Control', 'no-store'), ('X-Frame-Options', 'DENY'), ('X-Content-Type-Options', 'nosniff')]:
                self.send_header(name, value)
            if self.headers.get('Origin') in origins:
                self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
                self.send_header('Vary', 'Origin')
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Control-Token')
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            return self.headers.get('Host') == f'127.0.0.1:{args.port}' and self.headers.get('Origin') in origins

        def do_OPTIONS(self):
            self.reply(200 if self.allowed() else 403, {})

        def do_GET(self):
            if not self.allowed():
                return self.reply(403, {'error': 'Use Server controls inside ZeroCollab.'})
            if self.path != '/status':
                return self.reply(404, {'error': 'Not found'})
            try:
                with lock:
                    self.reply(200, status())
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                self.reply(503, {'error': f'Controller status unavailable: {exc}'})

        def do_POST(self):
            nonlocal worker, action, message
            if not self.allowed() or not secrets.compare_digest(self.headers.get('X-Control-Token', ''), token):
                return self.reply(403, {'error': 'Reconnect Server controls inside ZeroCollab.'})
            if self.path != '/action':
                return self.reply(404, {'error': 'Not found'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 1024:
                    raise ValueError('Invalid action')
                body = json.loads(self.rfile.read(length))
                command = body.get('action')
                if command not in ('Start', 'Stop', 'Restart', 'Open', 'Install'):
                    raise ValueError('Invalid action')
                with lock:
                    current = status()
                    if current['busy']:
                        raise ValueError('An action or deployment is already in progress.')
                    if command == 'Install':
                        from room_deploy import approve_release
                        approval = approve_release(DeploymentState(root / 'data'), body,
                                                   allow_offline=True, supervisor_online=current['alive'])
                        if not approval.get('transport'):
                            action, message = 'Install', 'Install queued; finishing current turns.'
                            return self.reply(202, {'accepted': True, 'request_id': approval['id']})
                    argv = [sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--root', str(root), '--execute', command]
                    if command == 'Install':
                        argv += ['--release-id', approval['release_id'], '--manifest-sha', approval['manifest_sha']]
                    with (root / 'data/controller-web-output.log').open('wb') as out, (root / 'data/controller-web-error.log').open('wb') as err:
                        worker = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err, creationflags=NO_WINDOW)
                    action, message = command, ''
                self.reply(202, {'accepted': True})
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                self.reply(409, {'error': str(exc)})

    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
