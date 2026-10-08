"""One disposable, project-scoped Chromium page shared by the user and room agents."""
import asyncio
import base64
import contextlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit, unquote

import httpx
import websockets
from fastapi import Request
from fastapi.responses import JSONResponse, Response

WIDTH, HEIGHT = 1280, 800
LEASE_SECONDS = 60


def preview_url(value):
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError('Enter a local preview URL')
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or parsed.hostname not in ('localhost', '127.0.0.1', '::1')
            or parsed.username or parsed.password):
        raise ValueError('This browser opens local previews: http(s)://localhost, 127.0.0.1 or [::1]')
    return value


def executable():
    candidates = [shutil.which(name) for name in ('msedge', 'google-chrome', 'chromium', 'chromium-browser')]
    if os.name == 'nt':
        candidates += [str(Path(root) / suffix) for root in
                       (os.environ.get('PROGRAMFILES(X86)', ''), os.environ.get('PROGRAMFILES', ''))
                       for suffix in ('Microsoft/Edge/Application/msedge.exe', 'Google/Chrome/Application/chrome.exe')]
    elif os.uname().sysname == 'Darwin':
        candidates += ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                       '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge']
    return next((p for p in candidates if p and Path(p).is_file()), None)


class Browser:
    def __init__(self, runtime):
        self.runtime = runtime
        self.lock = asyncio.Lock()
        self.proc = self.ws = self.reader = self.profile = None
        self.pending, self.serial = {}, 0
        self.project = self.owner = self.owner_label = ''
        self.expires = 0
        self.url, self.title = '', ''
        self.events = deque(maxlen=50)
        self.dialog = self.file_node = None
        self.dialog_event = asyncio.Event()
        self.generation = 0
        self.ref_serial = 0
        self.background = set()
        self.frame, self.frame_at = b'', 0
        self.width, self.height = WIDTH, HEIGHT

    def note(self, kind, text):
        self.events.append({'time': time.time(), 'kind': kind, 'text': str(text)[:500]})

    async def receive(self):
        try:
            async for raw in self.ws:
                message = json.loads(raw)
                if 'id' in message:
                    waiter = self.pending.pop(message['id'], None)
                    if waiter and not waiter.done():
                        if message.get('error'):
                            waiter.set_exception(ValueError(message['error'].get('message', 'Browser command failed')))
                        else:
                            waiter.set_result(message.get('result', {}))
                    continue
                method, p = message.get('method'), message.get('params', {})
                if method == 'Page.frameNavigated' and not p.get('frame', {}).get('parentId'):
                    self.url = p['frame']['url']
                    self.generation += 1
                elif method == 'Runtime.consoleAPICalled' and p.get('type') in ('error', 'warning'):
                    self.note('console', ' '.join(str(a.get('value', a.get('description', ''))) for a in p.get('args', [])))
                elif method == 'Runtime.exceptionThrown':
                    self.note('error', p.get('exceptionDetails', {}).get('text', 'Page exception'))
                elif method == 'Network.loadingFailed' and not p.get('canceled'):
                    self.note('network', p.get('errorText', 'Request failed'))
                elif method == 'Page.javascriptDialogOpening':
                    self.dialog = {'type': p.get('type'), 'message': p.get('message', '')[:1000]}
                    self.dialog_event.set()
                elif method == 'Page.javascriptDialogClosed':
                    self.dialog = None
                    self.dialog_event.clear()
                elif method == 'Page.fileChooserOpened':
                    self.file_node = p.get('backendNodeId')
                elif method == 'Fetch.requestPaused':
                    task = asyncio.create_task(self.route_document(p))
                    self.background.add(task)
                    task.add_done_callback(self.background.discard)
        except Exception as exc:
            self.note('browser', 'Browser connection closed')
            for waiter in self.pending.values():
                if not waiter.done():
                    waiter.set_exception(ValueError('Browser disconnected; stop and reopen it'))
            self.pending.clear()

    async def route_document(self, params):
        try:
            url = params['request']['url']
            try:
                if url != 'about:blank':
                    preview_url(url)
            except ValueError:
                await self.command('Fetch.failRequest', {'requestId': params['requestId'], 'errorReason': 'BlockedByClient'})
                self.note('navigation', 'Blocked navigation outside local previews')
            else:
                await self.command('Fetch.continueRequest', {'requestId': params['requestId']})
        except (ValueError, websockets.ConnectionClosed):
            pass

    async def command(self, method, params=None):
        self.serial += 1
        identifier = self.serial
        waiter = asyncio.get_running_loop().create_future()
        self.pending[identifier] = waiter
        dialog_waiter = None
        try:
            await self.ws.send(json.dumps({'id': identifier, 'method': method, 'params': params or {}}))
            # Chromium leaves a click pending while its JS alert is open. Return control
            # to the user/agent so it can answer the dialog instead of holding our lock.
            if method.startswith('Input.'):
                dialog_waiter = asyncio.create_task(self.dialog_event.wait())
                done, _ = await asyncio.wait({waiter, dialog_waiter}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
                if waiter in done:
                    return waiter.result()
                if dialog_waiter in done:
                    return {}
                raise asyncio.TimeoutError()
            return await asyncio.wait_for(waiter, 5)
        except (asyncio.TimeoutError, websockets.ConnectionClosed) as exc:
            raise ValueError('Browser did not respond; retry or stop and reopen it') from exc
        finally:
            self.pending.pop(identifier, None)
            if dialog_waiter:
                dialog_waiter.cancel()
            if not waiter.done():
                waiter.cancel()

    async def evaluate(self, expression):
        data = await self.command('Runtime.evaluate', {'expression': expression, 'returnByValue': True})
        if data.get('exceptionDetails'):
            raise ValueError('Page changed; inspect it again')
        return data.get('result', {}).get('value')

    async def start(self):
        binary = executable()
        if not binary:
            raise ValueError('Install Microsoft Edge, Google Chrome or Chromium to use the shared browser')
        self.profile = tempfile.TemporaryDirectory(prefix='zerocollab-browser-')
        opts = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        try:
            self.proc = await asyncio.create_subprocess_exec(binary, '--headless=new', '--disable-gpu',
                '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=0',
                '--remote-debugging-address=127.0.0.1', '--disable-background-networking',
                '--user-data-dir=' + self.profile.name, 'about:blank',
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, **opts)
            marker = Path(self.profile.name) / 'DevToolsActivePort'
            for _ in range(80):
                if marker.exists():
                    break
                if self.proc.returncode is not None:
                    raise ValueError('Chromium exited during startup')
                await asyncio.sleep(.1)
            if not marker.exists():
                raise ValueError('Chromium did not start within 8 seconds')
            port = int(marker.read_text().splitlines()[0])
            async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
                pages = (await client.get(f'http://127.0.0.1:{port}/json/list')).json()
            page = next(p for p in pages if p['type'] == 'page')
            self.ws = await websockets.connect(page['webSocketDebuggerUrl'], max_size=8 * 1024 * 1024)
            self.reader = asyncio.create_task(self.receive())
            self.width, self.height = WIDTH, HEIGHT
            for domain in ('Page', 'Runtime', 'Network'):
                await self.command(domain + '.enable')
            await self.command('Fetch.enable', {'patterns': [{'resourceType': 'Document', 'requestStage': 'Request'}]})
            await self.command('Emulation.setDeviceMetricsOverride', {'width': WIDTH, 'height': HEIGHT, 'deviceScaleFactor': 1, 'mobile': False})
            await self.command('Page.setInterceptFileChooserDialog', {'enabled': True})
            self.project = self.runtime.identity
            self.note('browser', 'Shared browser started with a temporary profile')
        except Exception:
            await self.close()
            raise

    async def close(self):
        if self.ws:
            with contextlib.suppress(Exception):
                await self.ws.close()
        if self.reader:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        for task in list(self.background):
            task.cancel()
        await asyncio.gather(*self.background, return_exceptions=True)
        self.background.clear()
        if self.proc and self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        if self.profile:
            with contextlib.suppress(OSError):
                self.profile.cleanup()
        self.proc = self.ws = self.reader = self.profile = None
        self.owner = self.owner_label = self.project = self.url = self.title = ''
        self.frame, self.frame_at = b'', 0
        self.dialog = self.file_node = None
        self.dialog_event.clear()
        self.generation += 1

    def expire(self):
        if self.owner and self.owner != 'user' and time.monotonic() >= self.expires:
            self.owner = self.owner_label = ''

    def status(self):
        self.expire()
        return {'running': bool(self.proc and self.proc.returncode is None and self.ws and self.reader and not self.reader.done()),
                'project': self.runtime.identity, 'url': self.url, 'title': self.title,
                'owner': self.owner, 'owner_label': self.owner_label,
                'lease_seconds': max(0, int(self.expires - time.monotonic())) if self.owner != 'user' else None,
                'width': self.width, 'height': self.height, 'generation': self.generation,
                'events': list(self.events), 'dialog': self.dialog, 'file_chooser': bool(self.file_node)}

    def control(self, actor):
        self.expire()
        if self.owner != actor:
            raise ValueError(f'{self.owner_label or "Nobody"} controls the browser. Claim control first; agents cannot take over from the user.')
        self.expires = time.monotonic() + LEASE_SECONDS

    async def screenshot(self):
        if time.monotonic() - self.frame_at > .35:
            result = await self.command('Page.captureScreenshot', {'format': 'jpeg', 'quality': 75})
            self.frame = base64.b64decode(result['data'])
            self.frame_at = time.monotonic()
        return self.frame

    async def act(self, actor, label, arguments):
        if not isinstance(arguments, dict):
            raise ValueError('Browser arguments must be an object')
        async with self.lock:
            if self.project and self.project != self.runtime.identity:
                await self.close()
                self.events.clear()
            action = arguments.get('action', 'status')
            if action == 'status':
                return self.status()
            if action == 'claim':
                self.expire()
                if self.owner and self.owner != actor and actor != 'user':
                    raise ValueError(f'{self.owner_label} controls the browser; wait for release')
                self.owner, self.owner_label = actor, label
                self.expires = time.monotonic() + LEASE_SECONDS
                self.note('control', label + ' took control')
                return self.status()
            if action == 'release':
                self.control(actor)
                self.owner = self.owner_label = ''
                return self.status()
            if action not in ('inspect', 'screenshot'):
                self.control(actor)
            if action == 'open':
                url = preview_url(arguments.get('url', ''))
                if not self.status()['running']:
                    await self.close()
                    self.owner, self.owner_label = actor, label
                    self.expires = time.monotonic() + LEASE_SECONDS
                    await self.start()
                result = await self.command('Page.navigate', {'url': url})
                if result.get('errorText'):
                    raise ValueError('Navigation failed: ' + result['errorText'])
                self.note('action', label + ' opened ' + url)
            elif action == 'stop':
                await self.close()
            elif not self.status()['running']:
                raise ValueError('Open a local preview first')
            elif action == 'screenshot':
                return {**self.status(), 'image': base64.b64encode(await self.screenshot()).decode()}
            elif action == 'resize':
                self.width = max(360, min(1600, int(arguments.get('width', WIDTH))))
                self.height = max(300, min(1000, int(arguments.get('height', HEIGHT))))
                await self.command('Emulation.setDeviceMetricsOverride', {'width': self.width, 'height': self.height, 'deviceScaleFactor': 1, 'mobile': False})
            elif action == 'inspect':
                self.ref_serial += 1000
                expression = """(() => { let n = REF_START; return {title: document.title,
                    text: document.body.innerText.slice(0, 12000), elements: Array.from(document.querySelectorAll(
                    'a,button,input,textarea,select,[role=button],[contenteditable=true]')).filter(e => e.getClientRects().length)
                    .slice(0,150).map(e => { e.setAttribute('data-room-ref', String(++n)); return {ref:n,tag:e.tagName,
                    type:e.type||'',text:(e.innerText||e.getAttribute('aria-label')||e.placeholder||'').slice(0,160)}; })}; })()"""
                elements = await self.evaluate(expression.replace('REF_START', str(self.ref_serial)))
                self.title = elements.get('title', '')
                return {**self.status(), **elements}
            elif action in ('click', 'type'):
                reference = arguments.get('ref')
                if reference is not None:
                    if not isinstance(reference, int) or reference < 1:
                        raise ValueError('Use a ref from inspect')
                    point = await self.evaluate(f"""(() => {{ const e=document.querySelector('[data-room-ref="{reference}"]');
                        if(!e) return null; e.scrollIntoView({{block:'center'}}); e.focus(); const r=e.getBoundingClientRect();
                        return {{x:r.x+r.width/2,y:r.y+r.height/2}}; }})()""")
                    if not point:
                        raise ValueError('Element no longer exists; inspect again')
                else:
                    point = arguments
                if action == 'click':
                    x, y = self.point(point)
                    for phase in ('mousePressed', 'mouseReleased'):
                        await self.command('Input.dispatchMouseEvent', {'type': phase, 'x': x, 'y': y, 'button': 'left', 'clickCount': 1})
                else:
                    value = arguments.get('text', '')
                    if not isinstance(value, str) or len(value) > 16000:
                        raise ValueError('Text must be at most 16000 characters')
                    await self.command('Input.insertText', {'text': value})
            elif action == 'pointer':
                phase = arguments.get('phase')
                if phase not in ('mousePressed', 'mouseReleased', 'mouseMoved'):
                    raise ValueError('Invalid pointer event')
                x, y = self.point(arguments)
                await self.command('Input.dispatchMouseEvent', {'type': phase, 'x': x, 'y': y, 'button': 'left',
                    'buttons': 0 if phase == 'mouseReleased' else 1, 'clickCount': 1})
            elif action == 'scroll':
                x, y = self.point(arguments, default=True)
                delta = max(-2000, min(2000, float(arguments.get('delta', 500))))
                await self.command('Input.dispatchMouseEvent', {'type': 'mouseWheel', 'x': x, 'y': y, 'deltaX': 0, 'deltaY': delta})
            elif action == 'key':
                key = arguments.get('key', '')
                keys = {'Enter':13,'Tab':9,'Backspace':8,'Delete':46,'Escape':27,'ArrowLeft':37,'ArrowUp':38,'ArrowRight':39,'ArrowDown':40,'Home':36,'End':35,'PageUp':33,'PageDown':34,'a':65}
                if key not in keys:
                    raise ValueError('Unsupported key')
                modifiers = int(arguments.get('modifiers', 0)) & 15
                for phase in ('keyDown', 'keyUp'):
                    await self.command('Input.dispatchKeyEvent', {'type': phase, 'key': key, 'windowsVirtualKeyCode': keys[key], 'modifiers': modifiers})
            elif action == 'select':
                reference = arguments.get('ref')
                if not isinstance(reference, int) or reference < 1:
                    raise ValueError('Use a select ref from inspect')
                value = json.dumps(str(arguments.get('text', ''))[:4000])
                changed = await self.evaluate(f"""(() => {{ const e=document.querySelector('select[data-room-ref="{reference}"]');
                    if(!e||!Array.from(e.options).some(o=>o.value==={value})) return false;
                    e.value={value}; e.dispatchEvent(new Event('input',{{bubbles:true}})); e.dispatchEvent(new Event('change',{{bubbles:true}})); return true; }})()""")
                if not changed:
                    raise ValueError('Select or option changed; inspect again')
            elif action == 'reload':
                await self.command('Page.reload')
            elif action == 'dialog':
                await self.command('Page.handleJavaScriptDialog', {'accept': bool(arguments.get('accept')), 'promptText': str(arguments.get('text', ''))[:2000]})
            else:
                raise ValueError('Unknown browser action')
            self.frame_at = 0
            return self.status()

    def point(self, args, default=False):
        x, y = float(args.get('x', self.width / 2 if default else -1)), float(args.get('y', self.height / 2 if default else -1))
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ValueError('Point is outside the browser viewport')
        return x, y


def install(host, runtime):
    browser = runtime.browser = Browser(runtime)

    @host.app.get('/api/runtime/browser')
    async def status():
        return await browser.act('user', 'You', {'action': 'status'})

    @host.app.post('/api/runtime/browser')
    async def action(request: Request):
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError('Browser arguments must be an object')
            if body.get('project') != runtime.identity:
                return JSONResponse({'error': 'Project changed; reload the browser view'}, 409)
            return await browser.act('user', 'You', body)
        except (ValueError, TypeError, OSError) as exc:
            return JSONResponse({'error': str(exc)}, 400)

    @host.app.get('/api/runtime/browser/frame')
    async def frame(request: Request):
        try:
            if request.query_params.get('project') != runtime.identity:
                return Response(status_code=409)
            result = await browser.act('user', 'You', {'action': 'screenshot'})
            return Response(base64.b64decode(result['image']), media_type='image/jpeg', headers={'Cache-Control': 'no-store'})
        except (ValueError, OSError):
            return Response(status_code=409)

    @host.app.post('/api/runtime/browser/file')
    async def upload(request: Request):
        async with browser.lock:
            try:
                if request.query_params.get('project') != runtime.identity or browser.project != runtime.identity:
                    raise ValueError('Project changed; reopen the browser')
                browser.control('user')
                if not browser.file_node:
                    raise ValueError('The page has no pending file picker')
                filename = Path(unquote(request.headers.get('X-File-Name', 'upload')).replace('\\', '/')).name
                if not filename or filename in ('.', '..') or ':' in filename:
                    raise ValueError('Invalid filename')
                target = Path(browser.profile.name) / 'uploads' / filename
                target.parent.mkdir(exist_ok=True)
                count = 0
                with target.open('wb') as output:
                    async for chunk in request.stream():
                        count += len(chunk)
                        if count > 16 * 1024 * 1024:
                            raise ValueError('File must be smaller than 16 MB')
                        output.write(chunk)
                await browser.command('DOM.setFileInputFiles', {'files': [str(target)], 'backendNodeId': browser.file_node})
                browser.file_node = None
                return browser.status()
            except (ValueError, OSError) as exc:
                return JSONResponse({'error': str(exc)}, 400)
