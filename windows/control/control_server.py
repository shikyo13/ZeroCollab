"""Private loopback process-control API used by ZeroCollab's existing frontend.

There is no separate browser UI. Only the configured room origin may read status
or submit a command; commands additionally require a per-controller CSRF token.
"""
import argparse
import json
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8301)
    args = parser.parse_args()
    root = args.root.resolve()
    script = Path(__file__).with_name('ZeroCollab-Control.ps1')
    token = secrets.token_urlsafe(32)
    import sys
    sys.path.insert(0, str(root))
    from supervisor import room_port, supervisor_alive
    origins = {f'http://127.0.0.1:{room_port()}', f'http://localhost:{room_port()}',
               f'http://zerocollab.localhost:{room_port()}'}
    worker = None
    action = ''
    message = ''
    lock = threading.Lock()
    last_check, last_pid, last_alive = 0, None, False

    def status():
        nonlocal worker, message, last_check, last_pid, last_alive
        if worker is not None and worker.poll() is not None:
            message = f'{action} completed.' if worker.returncode == 0 else f'{action} failed. See data/controller-web-error.log and supervisor.log.'
            worker = None
        try:
            state = json.loads((root / 'data/deploy/status.json').read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            state = {}
        # Reuse the installed supervisor's identity check, but avoid a Windows
        # process enumeration for every poll or simultaneous browser tab.
        pid = state.get('supervisor_pid')
        if pid != last_pid or time.monotonic() - last_check > 2:
            last_alive = supervisor_alive(pid)
            last_pid, last_check = pid, time.monotonic()
        alive = last_alive
        url = state.get('url', 'http://127.0.0.1:8300')
        if not url.startswith('http://127.0.0.1:'):
            url = 'http://127.0.0.1:8300'
        return dict(state=state.get('state','unknown') if alive else 'stopped', alive=alive,
                    busy=worker is not None, action=action, message=message or state.get('error',''), url=url, token=token)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, data):
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Frame-Options','DENY')
            self.send_header('X-Content-Type-Options','nosniff')
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
                return self.reply(403, {'error':'Use Backend controls inside ZeroCollab.'})
            if self.path == '/status':
                with lock:
                    return self.reply(200,status())
            self.reply(404, {'error':'Not found'})

        def do_POST(self):
            nonlocal worker, action, message
            if not self.allowed() or not secrets.compare_digest(self.headers.get('X-Control-Token',''),token):
                return self.reply(403, {'error':'Reconnect Backend controls inside ZeroCollab.'})
            if self.path != '/action':
                return self.reply(404, {'error':'Not found'})
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 256:
                    raise ValueError()
                command = json.loads(self.rfile.read(length)).get('action')
                if command not in ('Start','Stop','Restart'):
                    raise ValueError()
            except (ValueError, AttributeError):
                return self.reply(400, {'error':'Invalid action'})
            with lock:
                if status()['busy']:
                    return self.reply(409, {'error':'An action is already in progress.'})
                try:
                    with (root/'data/controller-web-output.log').open('wb') as out, (root/'data/controller-web-error.log').open('wb') as err:
                        worker = subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-File',str(script),'-RoomRoot',str(root),'-Action',command],stdin=subprocess.DEVNULL,stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW)
                    action, message = command, ''
                except OSError as exc:
                    return self.reply(500, {'error':str(exc)})
            self.reply(202, {'accepted':True})

    server = ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    server.serve_forever()

if __name__ == '__main__':
    main()
