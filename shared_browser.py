"""Disposable project-scoped Chromium instances, independently controlled by each agent and viewable
by the user. Pages are opened explicitly; viewing frames never holds the action lock."""
import asyncio
import base64
import contextlib
import contextvars
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit, unquote

import httpx
import websockets
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

WIDTH, HEIGHT = 1280, 800
LEASE_SECONDS = 60
# Explicitly opened pages per browser; each headless page costs a renderer process.
MAX_PAGES = 4
IDLE_SECONDS = 300


class WindowsBrowserProcess:
    """A suspended launch assigned to a kill-on-close job before it can create children."""
    def __init__(self, args):
        import ctypes
        from ctypes import wintypes as w
        import _winapi
        self.api = _winapi
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                        ('flags', w.DWORD), ('min_working_set', ctypes.c_size_t),
                        ('max_working_set', ctypes.c_size_t), ('active_processes', w.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]
        class Limits(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', ctypes.c_uint64 * 6),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.ResumeThread.argtypes = [w.HANDLE]
        self.kernel.ResumeThread.restype = w.DWORD
        self.job = self.kernel.CreateJobObjectW(None, None)
        self.handle = None
        if not self.job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Limits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        thread = None
        try:
            if not self.kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle, thread, self.pid, _ = _winapi.CreateProcess(
                None, subprocess.list2cmdline(args), None, None, False,
                subprocess.CREATE_NO_WINDOW | 4, None, None, subprocess.STARTUPINFO())  # CREATE_SUSPENDED
            if not self.kernel.AssignProcessToJobObject(self.job, self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            if self.kernel.ResumeThread(thread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            if self.handle:
                _winapi.TerminateProcess(self.handle, 1)
            self.close_handles()
            raise
        finally:
            if thread:
                _winapi.CloseHandle(thread)

    @property
    def returncode(self):
        code = self.api.GetExitCodeProcess(self.handle)
        return None if code == 259 else code  # STILL_ACTIVE

    def terminate(self):
        self.api.TerminateProcess(self.handle, 1)

    kill = terminate

    async def wait(self):
        await asyncio.to_thread(self.api.WaitForSingleObject, self.handle, 0xFFFFFFFF)
        return self.returncode

    def close_handles(self):
        if self.job:
            self.kernel.CloseHandle(self.job)  # Also terminates all surviving children.
            self.job = None
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


# The web room's browser host name (ui_sessions.ROOM_HOST), repeated here so the desktop app, which ships
# this file without ui_sessions.py, needs no extra module. Agents' browser never reaches it on any port.
ROOM_HOST = 'zerocollab.localhost'


def preview_url(value, blocked_ports=()):
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError('Enter a local preview URL')
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or parsed.hostname not in ('localhost', '127.0.0.1', '::1')
            or parsed.username or parsed.password):
        raise ValueError('This browser opens local previews: http(s)://localhost, 127.0.0.1 or [::1]')
    if (parsed.port or (443 if parsed.scheme == 'https' else 80)) in blocked_ports:
        raise ValueError('The room, MCP and browser-control ports cannot be opened in the shared browser')
    return value


def remove_stale_profiles():
    """A crashed room can leave a temporary profile behind, holding the default context's account
    identifiers. Remove those no browser still holds: on Windows a running Chromium keeps 'lockfile'
    open, so it cannot be deleted; on other systems a SingletonLock link marks a profile as possibly live."""
    for folder in Path(tempfile.gettempdir()).glob('zerocollab-browser-*'):
        try:
            if not folder.is_dir() or (folder / 'SingletonLock').is_symlink():
                continue
            (folder / 'lockfile').unlink(missing_ok=True)
        except OSError:
            continue
        shutil.rmtree(folder, ignore_errors=True)


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




class Page:
    """One tab of the shared browser: its own CDP session, location, viewport, dialog and file chooser.
    Its public id is ours, not Chromium's target id, and is never reused while the browser runs."""
    def __init__(self, target, session):
        self.id = uuid.uuid4().hex[:8]
        self.target, self.session = target, session
        self.url = self.title = ''
        self.generation = 0
        self.dialog = self.file_node = None
        self.dialog_event = asyncio.Event()
        self.frame, self.frame_at = b'', 0
        self.frame_seq = self.frame_drops = self.viewers = 0
        self.frame_generation = 0
        self.frame_event = asyncio.Event()
        self.stream_lock = asyncio.Lock()
        self.streaming = False
        self.width, self.height = WIDTH, HEIGHT

    def summary(self, active):
        return {'id': self.id, 'url': self.url, 'title': self.title, 'generation': self.generation,
                'dialog': bool(self.dialog), 'file_chooser': bool(self.file_node), 'active': active}


class Browser:
    def __init__(self, runtime):
        self.runtime = runtime
        self.lock = asyncio.Lock()
        self.proc = self.ws = self.reader = self.profile = None
        # Pages in creation order; the active page receives actions that name no page.
        self.pages, self.active = {}, None
        # While new_page waits for its target, unknown page attachments are held here instead of
        # being closed at once; everything except the created target is then closed as a pop-up.
        self.adopting, self.adopted = None, asyncio.Event()
        # Every shared page lives in one off-the-record browser context, which never carries the
        # profile's account: without --guest, Edge signs a new profile in with the OS account. The
        # startup tab is the only page in that default context and is closed once ours exists.
        self.context = self.anchor = None
        self.pending, self.serial = {}, 0
        self.project = self.owner = self.owner_label = ''
        self.expires = 0
        self.events = deque(maxlen=50)
        # Browser-wide: changes on any navigation, page switch, page close and teardown.
        self.generation = 0
        self.ref_serial = 0
        self.background = set()
        cfg = getattr(runtime, 'cfg', {})
        self.blocked_ports = {int(cfg.get('server', {}).get('port', 8300)),
                              int(cfg.get('mcp', {}).get('http_port', 8200)),
                              int(cfg.get('mcp', {}).get('sse_port', 8201)), 8301}
        self.protected_debug_ports = set()
        self.debug_port = None
        self.activity = None
        self.id = uuid.uuid4().hex[:12]
        self.agent = self.label = ''
        self.last_action = time.monotonic()
        self.capacity_guard = None
        self.lifecycle_guard = None

    async def publish_activity(self, actor, label, action, phase, page=None):
        # Broadcast existing observer status plus action metadata, not action arguments or screenshots.
        self.activity = {'actor': actor, 'label': label, 'action': action,
                         'phase': phase, 'time': time.time(), 'page': page or self.active}
        await self.runtime.emit('browser_activity', self.status())

    def note(self, kind, text, page=None):
        self.events.append({'time': time.time(), 'kind': kind, 'text': str(text)[:500],
                            'page': page.id if page else None})

    def page(self, arguments=None):
        """The page an action targets. A named page must still exist; it never falls back to another."""
        wanted = (arguments or {}).get('page')
        if wanted:
            if not isinstance(wanted, str) or wanted not in self.pages:
                raise ValueError('That browser page was closed; read status for the current pages')
            return self.pages[wanted]
        if self.active not in self.pages:
            raise ValueError('Open a local preview first')
        return self.pages[self.active]

    def session_page(self, session):
        return next((p for p in self.pages.values() if p.session == session), None) if session else None

    def schedule(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    def ours(self, target):
        return target == self.anchor or any(page.target == target for page in self.pages.values())

    async def sweep(self):
        """Close page targets that are not ours, e.g. a link pop-up created while new_page was adopting."""
        with contextlib.suppress(ValueError, websockets.ConnectionClosed):
            for info in (await self.command('Target.getTargets'))['targetInfos']:
                # Runs outside the action lock: a later new_page may be adopting a target that is
                # not in pages yet, so stand down for it and re-check each target just before closing.
                if self.adopting is not None:
                    return
                if info.get('type') == 'page' and not self.ours(info['targetId']):
                    await self.close_stray(info['targetId'])

    async def refresh_titles(self):
        """Chromium sends no event when a page's title changes (checked on headless Edge), but
        Target.getTargets reports the current one; read it on each status request so tabs are named
        without an inspect. The title is page-controlled text."""
        if not (self.ws and self.pages):
            return
        with contextlib.suppress(ValueError, websockets.ConnectionClosed):
            titles = {info['targetId']: info.get('title', '') for info in
                      (await self.command('Target.getTargets'))['targetInfos'] if info.get('type') == 'page'}
            for page in self.pages.values():
                if isinstance(titles.get(page.target), str):
                    page.title = titles[page.target][:300]

    def drop(self, page):
        if self.pages.pop(page.id, None) is None:
            return
        if self.active == page.id:
            self.active = next(reversed(self.pages), None)
        self.generation += 1
        page.frame_event.set()

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
                if method == 'Target.attachedToTarget' and p.get('targetInfo', {}).get('type') == 'page':
                    target, session = p['targetInfo']['targetId'], p['sessionId']
                    known = next((page for page in self.pages.values() if page.target == target), None)
                    if target == self.anchor:
                        pass  # The startup tab in the signed-in default context; never driven, closed at start.
                    elif known:
                        known.session = session
                    elif self.adopting is not None:
                        self.adopting[target] = session
                        self.adopted.set()
                    else:
                        # Auto-attach pauses new pages before their document can run or navigate.
                        self.schedule(self.close_popup(target, session))
                    continue
                if method == 'Target.targetCreated' and p.get('targetInfo', {}).get('type') == 'page':
                    # Tripwire behind auto-attach: a page target that is neither ours nor being adopted
                    # is closed. Tab targets are not touched; in the tab-target model they wrap our pages.
                    target = p['targetInfo']['targetId']
                    if self.adopting is None and not self.ours(target):
                        self.schedule(self.close_stray(target, tripwire=True))
                    continue
                if method == 'Target.detachedFromTarget':
                    gone = self.session_page(p.get('sessionId'))
                    if gone:
                        self.note('browser', 'A shared page closed', gone)
                        self.drop(gone)
                    continue
                page = self.session_page(message.get('sessionId'))
                if page is None:
                    continue
                if method == 'Page.screencastFrame':
                    page.frame = base64.b64decode(p['data'])
                    page.frame_at = time.monotonic()
                    page.frame_generation = page.generation
                    page.frame_seq += 1
                    page.frame_event.set()
                    # Never await a command from the reader that receives its reply.
                    self.schedule(self.command('Page.screencastFrameAck', {'sessionId': p['sessionId']}, page=page))
                elif method == 'Page.frameNavigated' and not p.get('frame', {}).get('parentId'):
                    page.url = p['frame']['url']
                    page.generation += 1
                    self.generation += 1
                    page.frame, page.frame_at = b'', 0
                elif method == 'Runtime.consoleAPICalled' and p.get('type') in ('error', 'warning'):
                    self.note('console', ' '.join(str(a.get('value', a.get('description', ''))) for a in p.get('args', [])), page)
                elif method == 'Runtime.exceptionThrown':
                    self.note('error', p.get('exceptionDetails', {}).get('text', 'Page exception'), page)
                elif method == 'Network.loadingFailed' and not p.get('canceled'):
                    self.note('network', p.get('errorText', 'Request failed'), page)
                elif method == 'Page.javascriptDialogOpening':
                    page.dialog = {'type': p.get('type'), 'message': p.get('message', '')[:1000]}
                    page.dialog_event.set()
                elif method == 'Page.javascriptDialogClosed':
                    page.dialog = None
                    page.dialog_event.clear()
                elif method == 'Page.fileChooserOpened':
                    page.file_node = p.get('backendNodeId')
                elif method == 'Fetch.requestPaused':
                    self.schedule(self.route_document(p, page.session))
        except Exception as exc:
            self.note('browser', 'Browser connection closed')
            for waiter in self.pending.values():
                if not waiter.done():
                    waiter.set_exception(ValueError('Browser disconnected; stop and reopen it'))
            self.pending.clear()

    async def close_popup(self, target, session):
        # Block its network before anything else, then always try to close it, even if blocking failed.
        # The pop-up itself is never resumed. Our own pages are released afterwards either way: an opener
        # stays blocked in window.open until then, and resuming our own page cannot release the pop-up.
        with contextlib.suppress(ValueError, websockets.ConnectionClosed):
            await self.command('Network.enable', session=session)
            await self.command('Network.setBlockedURLs', {'urls': ['*']}, session=session)
        closed = await self.close_stray(target)
        for page in list(self.pages.values()):
            with contextlib.suppress(ValueError, websockets.ConnectionClosed):
                await self.command('Runtime.runIfWaitingForDebugger', page=page)
        if closed:
            self.note('navigation', 'Blocked a new browser window; use new_page for another shared page')

    async def close_stray(self, target, tripwire=False):
        """Close a page target we did not create. True once it is gone, including when it already was."""
        if tripwire:
            # Normally close_popup has already closed it; the tripwire acts only on a page that survived.
            await asyncio.sleep(.5)
            if self.adopting is not None or self.ours(target):
                return False
        try:
            await self.command('Target.closeTarget', {'targetId': target})
        except (ValueError, websockets.ConnectionClosed) as exc:
            if 'No target with given id' in str(exc):
                return not tripwire
            self.note('navigation', 'Could not close a blocked browser window; it stays paused')
            return False
        if tripwire:
            self.note('navigation', 'Closed an unexpected browser page')
        return True

    async def route_document(self, params, session):
        try:
            url = params['request']['url']
            try:
                parsed = urlsplit(url)
                ports = self.blocked_ports | self.protected_debug_ports | {self.debug_port}
                # Block admin/control origins for every resource, not only top-level pages.
                if parsed.hostname in ('localhost', '127.0.0.1', '::1') and (parsed.port or (443 if parsed.scheme == 'https' else 80)) in ports:
                    raise ValueError('Protected room endpoint')
                # The web room's own host name, on every port: the browser room signs in there.
                if parsed.hostname and (parsed.hostname == ROOM_HOST or parsed.hostname.endswith('.' + ROOM_HOST)):
                    raise ValueError('Protected room endpoint')
                if params.get('resourceType') == 'Document' and url != 'about:blank':
                    preview_url(url, ports)
            except ValueError:
                await self.command('Fetch.failRequest', {'requestId': params['requestId'], 'errorReason': 'BlockedByClient'}, session=session)
                self.note('navigation', 'Blocked protected endpoint or navigation outside local previews', self.session_page(session))
            else:
                await self.command('Fetch.continueRequest', {'requestId': params['requestId']}, session=session)
        except (ValueError, websockets.ConnectionClosed):
            pass

    async def command(self, method, params=None, page=None, session=None):
        session = session or (page.session if page else None)
        if not method.startswith(('Target.', 'Browser.')) and not session:
            raise ValueError('Open a local preview first')
        self.serial += 1
        identifier = self.serial
        waiter = asyncio.get_running_loop().create_future()
        self.pending[identifier] = waiter
        dialog_waiter = None
        try:
            message = {'id': identifier, 'method': method, 'params': params or {}}
            if not method.startswith(('Target.', 'Browser.')):
                message['sessionId'] = session
            await self.ws.send(json.dumps(message))
            # Chromium leaves a click pending while its JS alert is open. Return control
            # to the user/agent so it can answer the dialog instead of holding our lock.
            target = page or self.session_page(session)
            if method.startswith('Input.') and target:
                dialog_waiter = asyncio.create_task(target.dialog_event.wait())
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

    async def evaluate(self, expression, page):
        data = await self.command('Runtime.evaluate', {'expression': expression, 'returnByValue': True}, page=page)
        if data.get('exceptionDetails'):
            raise ValueError('Page changed; inspect it again')
        return data.get('result', {}).get('value')

    async def setup(self, page):
        """Per-session protections: every page gets the same blocklist, routing fence, viewport and file-chooser intercept."""
        for domain in ('Page', 'Runtime', 'Network'):
            await self.command(domain + '.enable', page=page)
        await self.command('Fetch.enable', {'patterns': [{'urlPattern': '*', 'requestStage': 'Request'}]}, page=page)
        await self.command('Network.setBlockedURLs', {'urls': [f'*://{host}:{p}/*'
            for host in ('localhost', '127.0.0.1', '[::1]') for p in self.blocked_ports | self.protected_debug_ports | {self.debug_port}]
            + [f'*://{ROOM_HOST}*', f'*://*.{ROOM_HOST}*']}, page=page)
        await self.command('Emulation.setDeviceMetricsOverride', {'width': page.width, 'height': page.height, 'deviceScaleFactor': 1, 'mobile': False}, page=page)
        await self.command('Page.setInterceptFileChooserDialog', {'enabled': True}, page=page)

    async def start(self):
        binary = executable()
        if not binary:
            raise ValueError('Install Microsoft Edge, Google Chrome or Chromium to use the shared browser')
        if not self.lifecycle_guard:
            await asyncio.to_thread(remove_stale_profiles)
        self.profile = tempfile.TemporaryDirectory(prefix='zerocollab-browser-')
        try:
            # An elevated Edge or Chrome otherwise relaunches itself de-elevated through the shell.
            # That child escapes the kill-on-close job and, with Administrators deny-only, cannot
            # write the mkdtemp profile (its 0o700 ACL is owned by Administrators), so it never starts.
            # No --guest: guest mode refuses Target.createTarget, so new_page could not work. The profile is
            # still a fresh temporary directory per browser, deleted on close.
            args = [binary, '--headless=new', '--disable-gpu', '--do-not-de-elevate',
                '--no-first-run', '--no-default-browser-check', '--disable-sync', '--remote-debugging-port=0',
                '--remote-debugging-address=127.0.0.1', '--disable-background-networking',
                # Refuses window.open and target=_blank at the process level; explicit CDP pages still open.
                '--block-new-web-contents',
                '--user-data-dir=' + self.profile.name, 'about:blank']
            self.proc = (WindowsBrowserProcess(args) if os.name == 'nt' else
                         await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL,
                                                              stderr=asyncio.subprocess.DEVNULL))
            marker = Path(self.profile.name) / 'DevToolsActivePort'
            for _ in range(80):
                if marker.exists():
                    break
                exit_code = self.proc.returncode
                # Edge's Windows launcher can exit successfully before its child
                # writes the readiness marker. Keep the job alive for that handoff.
                if exit_code is not None and not (isinstance(self.proc, WindowsBrowserProcess) and exit_code == 0):
                    raise ValueError('Chromium exited during startup')
                await asyncio.sleep(.1)
            if not marker.exists():
                raise ValueError('Chromium did not start within 8 seconds')
            port = int(marker.read_text().splitlines()[0])
            self.debug_port = port
            self.protected_debug_ports.add(port)
            async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
                pages = (await client.get(f'http://127.0.0.1:{port}/json/list')).json()
                version = (await client.get(f'http://127.0.0.1:{port}/json/version')).json()
            self.anchor = next(p for p in pages if p['type'] == 'page' and p['url'] == 'about:blank')['id']
            self.ws = await websockets.connect(version['webSocketDebuggerUrl'], max_size=8 * 1024 * 1024)
            self.reader = asyncio.create_task(self.receive())
            await self.command('Target.setAutoAttach', {'autoAttach': True, 'waitForDebuggerOnStart': True,
                'flatten': True, 'filter': [{'type': 'page', 'exclude': False}, {'exclude': True}]})
            await self.command('Target.setDiscoverTargets', {'discover': True})
            self.context = (await self.command('Target.createBrowserContext', {'disposeOnDetach': True}))['browserContextId']
            page = await self.new_page()
            # The browser keeps running on the context page alone (checked live on headless Edge).
            await self.command('Target.closeTarget', {'targetId': self.anchor})
            self.project = self.runtime.identity
            self.note('browser', 'Shared browser started with a temporary profile', page)
        except Exception:
            await self.close()
            raise

    async def new_page(self):
        """Create a page only on an explicit request; the page's own pop-ups stay blocked."""
        if len(self.pages) >= MAX_PAGES:
            raise ValueError(f'The shared browser keeps at most {MAX_PAGES} pages; close one first')
        self.adopting, target, session, paused = {}, None, None, False
        try:
            target = (await self.command('Target.createTarget', {'url': 'about:blank', 'browserContextId': self.context}))['targetId']
            deadline = asyncio.get_running_loop().time() + 5
            while target not in self.adopting:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                self.adopted.clear()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.adopted.wait(), remaining)
            session = self.adopting.pop(target, None)
            paused = session is not None
            if session is None:
                session = (await self.command('Target.attachToTarget', {'targetId': target, 'flatten': True}))['sessionId']
            page = Page(target, session)
            self.pages[page.id] = page
            await self.setup(page)
            if paused:
                await self.command('Runtime.runIfWaitingForDebugger', page=page)
        except Exception:
            if target:
                stale = next((p for p in self.pages.values() if p.target == target), None)
                if stale:
                    self.pages.pop(stale.id)
                with contextlib.suppress(ValueError, websockets.ConnectionClosed):
                    await self.command('Target.closeTarget', {'targetId': target})
            raise
        finally:
            strays, self.adopting = self.adopting or {}, None
            for stray, stray_session in strays.items():
                self.schedule(self.close_popup(stray, stray_session))
            # The tripwire stands down while adopting, so check once more for unattached strays.
            self.schedule(self.sweep())
        self.active = page.id
        self.generation += 1
        if len(self.pages) > 1:
            self.note('browser', 'Opened another shared page', page)
        return page

    async def close_page(self, page):
        if len(self.pages) == 1:
            raise ValueError('This is the only page; use stop to close the shared browser')
        await self.command('Target.closeTarget', {'targetId': page.target})
        self.drop(page)

    async def close(self):
        for page in self.pages.values():
            page.frame_event.set()
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
        if isinstance(self.proc, WindowsBrowserProcess):
            self.proc.close_handles()
        if self.profile:
            with contextlib.suppress(OSError):
                self.profile.cleanup()
        self.protected_debug_ports.discard(self.debug_port)
        self.debug_port = None
        self.proc = self.ws = self.reader = self.profile = None
        self.pages, self.active, self.adopting = {}, None, None
        self.context = self.anchor = None
        self.owner = self.owner_label = self.project = ''
        self.activity = None
        self.generation += 1

    def expire(self):
        if self.owner and self.owner != 'user' and time.monotonic() >= self.expires:
            self.owner = self.owner_label = ''

    def status(self, page=None):
        """Top-level page fields describe the targeted page (the active page by default), as before;
        pages[] and active_page describe every open page."""
        self.expire()
        page = page or self.pages.get(self.active)
        # The connected reader tracks the browser child after a successful Windows launcher exit.
        process_ready = self.proc and (self.proc.returncode is None or
                                      isinstance(self.proc, WindowsBrowserProcess) and self.proc.returncode == 0)
        return {'running': bool(process_ready and self.ws and self.reader and not self.reader.done()),
                'browser': self.id, 'agent': self.agent, 'browser_label': self.label,
                'project': self.runtime.identity, 'url': page.url if page else '', 'title': page.title if page else '',
                'owner': self.owner, 'owner_label': self.owner_label,
                'lease_seconds': max(0, int(self.expires - time.monotonic())) if self.owner != 'user' else None,
                'width': page.width if page else WIDTH, 'height': page.height if page else HEIGHT,
                'generation': self.generation, 'events': list(self.events),
                'dialog': page.dialog if page else None, 'file_chooser': bool(page and page.file_node),
                'activity': self.activity, 'page': page.id if page else None,
                'active_page': self.active if self.active in self.pages else None,
                'pages': [p.summary(p.id == self.active) for p in self.pages.values()], 'max_pages': MAX_PAGES,
                'frame_sequence': page.frame_seq if page else 0,
                'frame_age_ms': round((time.monotonic() - page.frame_at) * 1000) if page and page.frame_at else None,
                'frame_drops': page.frame_drops if page else 0, 'viewers': page.viewers if page else 0}

    def control(self, actor):
        self.expire()
        if self.owner != actor:
            raise ValueError(f'{self.owner_label or "Nobody"} controls the browser. Claim control first; agents cannot take over from the user.')
        self.expires = time.monotonic() + LEASE_SECONDS

    async def screenshot(self, page):
        # Explicit evidence is always a fresh capture, independent of the viewing stream.
        result = await self.command('Page.captureScreenshot', {'format': 'jpeg', 'quality': 75}, page=page)
        return base64.b64decode(result['data'])

    async def start_stream(self, page):
        async with page.stream_lock:
            if self.pages.get(page.id) is not page or not self.status()['running']:
                raise ValueError('That browser page was closed')
            if not page.streaming:
                for attempt in range(40):
                    try:
                        await self.command('Page.startScreencast', {'format': 'jpeg', 'quality': 75,
                                           'maxWidth': page.width, 'maxHeight': page.height, 'everyNthFrame': 1}, page=page)
                        break
                    except ValueError as exc:
                        # Page.navigate returns before the replacement renderer is active.
                        if ('Not attached to an active page' not in str(exc) or attempt == 39
                                or self.pages.get(page.id) is not page):
                            raise
                        await asyncio.sleep(.05)
                page.streaming = True

    async def latest_frame(self, page):
        # Legacy one-shot observers must not leave a 60fps capture running.
        async with page.stream_lock:
            if self.pages.get(page.id) is not page or not self.status()['running']:
                raise ValueError('That browser page was closed')
            if page.viewers and page.streaming and page.frame:
                return page.frame
            try:
                return await self.screenshot(page)
            except ValueError:
                if self.pages.get(page.id) is page:
                    # A slow or disconnected capture is retryable, not a closed page.
                    raise asyncio.TimeoutError() from None
                raise

    async def stream(self, page):
        page.viewers += 1
        seen = -1
        try:
            await self.start_stream(page)
            while self.pages.get(page.id) is page and self.project == self.runtime.identity and self.status()['running']:
                page.frame_event.clear()
                if page.frame_seq == seen or not page.frame:
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(page.frame_event.wait(), 10)
                    continue
                if seen >= 0:
                    page.frame_drops += max(0, page.frame_seq - seen - 1)
                seen, data = page.frame_seq, page.frame
                headers = (f'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(data)}\r\n'
                           f'X-Browser-ID: {self.id}\r\nX-Page-ID: {page.id}\r\n'
                           f'X-Page-Generation: {page.frame_generation}\r\nX-Frame-Sequence: {seen}\r\n\r\n').encode()
                yield headers + data + b'\r\n'
        finally:
            page.viewers -= 1
            async with page.stream_lock:
                if not page.viewers and page.streaming:
                    with contextlib.suppress(Exception):
                        await self.command('Page.stopScreencast', page=page)
                    page.streaming = False

    async def act(self, actor, label, arguments):
        if not isinstance(arguments, dict):
            raise ValueError('Browser arguments must be an object')
        async with self.lock:
            if self.lifecycle_guard:
                self.lifecycle_guard()
            if arguments.get('action', 'status') != 'status':
                self.last_action = time.monotonic()
            if self.project and self.project != self.runtime.identity:
                await self.close()
                self.events.clear()
            action = arguments.get('action', 'status')
            if action == 'status':
                await self.refresh_titles()
                return self.status(self.page(arguments) if arguments.get('page') else None)
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
            if action in ('open', 'new_page'):
                url = arguments.get('url', '')
                if action == 'open' or url:
                    url = preview_url(url, self.blocked_ports | {self.debug_port})
                if not self.status()['running']:
                    if arguments.get('page'):
                        raise ValueError('That browser page was closed; read status for the current pages')
                    await self.close()
                    if self.capacity_guard:
                        self.capacity_guard()
                    self.owner, self.owner_label = actor, label
                    self.expires = time.monotonic() + LEASE_SECONDS
                    await self.start()
                    page = self.page()
                elif action == 'new_page' or not self.pages:
                    page = await self.new_page()
                else:
                    page = self.page(arguments)
                if url:
                    result = await self.command('Page.navigate', {'url': url}, page=page)
                    if result.get('errorText'):
                        raise ValueError('Navigation failed: ' + result['errorText'])
                    self.note('action', label + ' opened ' + url, page)
                page.frame_at = 0
                return self.status(page)
            if action == 'stop':
                await self.close()
                return self.status()
            if not self.status()['running']:
                raise ValueError('Open a local preview first')
            page = self.page(arguments)
            if action == 'select_page':
                if not arguments.get('page'):
                    raise ValueError('Name the page to select')
                if self.active != page.id:
                    self.active = page.id
                    self.generation += 1
                return self.status()
            if action == 'close_page':
                if not arguments.get('page'):
                    raise ValueError('Name the page to close')
                await self.close_page(page)
                self.note('browser', label + ' closed a shared page', page)
                return self.status()
            if action == 'screenshot':
                return {**self.status(page), 'image': base64.b64encode(await self.screenshot(page)).decode()}
            if action == 'resize':
                page.width = max(360, min(1600, int(arguments.get('width', WIDTH))))
                page.height = max(300, min(1000, int(arguments.get('height', HEIGHT))))
                await self.command('Emulation.setDeviceMetricsOverride', {'width': page.width, 'height': page.height, 'deviceScaleFactor': 1, 'mobile': False}, page=page)
            elif action == 'inspect':
                self.ref_serial += 1000
                expression = """(() => { let n = REF_START; return {title: document.title,
                    text: document.body.innerText.slice(0, 12000), elements: Array.from(document.querySelectorAll(
                    'a,button,input,textarea,select,[role=button],[contenteditable=true]')).filter(e => e.getClientRects().length)
                    .slice(0,150).map(e => { e.setAttribute('data-room-ref', String(++n)); return {ref:n,tag:e.tagName,
                    type:e.type||'',text:(e.innerText||e.getAttribute('aria-label')||e.placeholder||'').slice(0,160)}; })}; })()"""
                elements = await self.evaluate(expression.replace('REF_START', str(self.ref_serial)), page)
                page.title = elements.get('title', '')
                return {**self.status(page), **elements}
            elif action in ('click', 'type'):
                reference = arguments.get('ref')
                if reference is not None:
                    if not isinstance(reference, int) or reference < 1:
                        raise ValueError('Use a ref from inspect')
                    point = await self.evaluate(f"""(() => {{ const e=document.querySelector('[data-room-ref="{reference}"]');
                        if(!e) return null; e.scrollIntoView({{block:'center'}}); e.focus(); const r=e.getBoundingClientRect();
                        return {{x:r.x+r.width/2,y:r.y+r.height/2}}; }})()""", page)
                    if not point:
                        raise ValueError('Element no longer exists; inspect again')
                else:
                    point = arguments
                if action == 'click':
                    x, y = self.point(point, page)
                    for phase in ('mousePressed', 'mouseReleased'):
                        await self.command('Input.dispatchMouseEvent', {'type': phase, 'x': x, 'y': y, 'button': 'left', 'clickCount': 1}, page=page)
                else:
                    value = arguments.get('text', '')
                    if not isinstance(value, str) or len(value) > 16000:
                        raise ValueError('Text must be at most 16000 characters')
                    await self.command('Input.insertText', {'text': value}, page=page)
            elif action == 'pointer':
                phase = arguments.get('phase')
                if phase not in ('mousePressed', 'mouseReleased', 'mouseMoved'):
                    raise ValueError('Invalid pointer event')
                x, y = self.point(arguments, page)
                await self.command('Input.dispatchMouseEvent', {'type': phase, 'x': x, 'y': y, 'button': 'left',
                    'buttons': 0 if phase == 'mouseReleased' else 1, 'clickCount': 1}, page=page)
            elif action == 'scroll':
                x, y = self.point(arguments, page, default=True)
                delta = max(-2000, min(2000, float(arguments.get('delta', 500))))
                await self.command('Input.dispatchMouseEvent', {'type': 'mouseWheel', 'x': x, 'y': y, 'deltaX': 0, 'deltaY': delta}, page=page)
            elif action == 'key':
                key = arguments.get('key', '')
                keys = {'Enter':13,'Tab':9,'Backspace':8,'Delete':46,'Escape':27,'ArrowLeft':37,'ArrowUp':38,'ArrowRight':39,'ArrowDown':40,'Home':36,'End':35,'PageUp':33,'PageDown':34,'a':65}
                if key not in keys:
                    raise ValueError('Unsupported key')
                modifiers = int(arguments.get('modifiers', 0)) & 15
                for phase in ('keyDown', 'keyUp'):
                    await self.command('Input.dispatchKeyEvent', {'type': phase, 'key': key, 'windowsVirtualKeyCode': keys[key], 'modifiers': modifiers}, page=page)
            elif action == 'select':
                reference = arguments.get('ref')
                if not isinstance(reference, int) or reference < 1:
                    raise ValueError('Use a select ref from inspect')
                value = json.dumps(str(arguments.get('text', ''))[:4000])
                changed = await self.evaluate(f"""(() => {{ const e=document.querySelector('select[data-room-ref="{reference}"]');
                    if(!e||!Array.from(e.options).some(o=>o.value==={value})) return false;
                    e.value={value}; e.dispatchEvent(new Event('input',{{bubbles:true}})); e.dispatchEvent(new Event('change',{{bubbles:true}})); return true; }})()""", page)
                if not changed:
                    raise ValueError('Select or option changed; inspect again')
            elif action == 'reload':
                await self.command('Page.reload', page=page)
            elif action == 'dialog':
                await self.command('Page.handleJavaScriptDialog', {'accept': bool(arguments.get('accept')), 'promptText': str(arguments.get('text', ''))[:2000]}, page=page)
            else:
                raise ValueError('Unknown browser action')
            page.frame_at = 0
            return self.status(page)

    def point(self, args, page, default=False):
        x, y = float(args.get('x', page.width / 2 if default else -1)), float(args.get('y', page.height / 2 if default else -1))
        if not (0 <= x < page.width and 0 <= y < page.height):
            raise ValueError('Point is outside the browser viewport')
        return x, y


def browser_capacity():
    """Conservative on-demand process budget, not a claim about measured memory use."""
    available = 2 * 1024 ** 3
    if os.name == 'nt':
        import ctypes
        class Memory(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in ('total', 'available', 'page_total', 'page_available',
                                                       'virtual_total', 'virtual_available', 'extended')]
        value = Memory()
        value.length = ctypes.sizeof(value)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
            available = value.available
    elif hasattr(os, 'sysconf'):
        with contextlib.suppress(ValueError, OSError):
            available = os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
    return min(32, max(1, available // (384 * 1024 ** 2)))


class BrowserManager:
    """Independent browser process/control per stable room agent; shared read-only selection."""
    def __init__(self, runtime):
        self.runtime, self.lock = runtime, asyncio.Lock()
        self.instances, self.agents = {}, {}
        self.project = runtime.identity
        self.debug_ports, self.starting = set(), set()
        self.default = None
        self.reaper = None
        self.selection = contextvars.ContextVar('browser_instance', default=None)
        self.max_instances = browser_capacity()
        self.cleaned_profiles = False

    @property
    def activity(self):
        browser = self.selection.get()
        return browser.activity if browser else None

    def reserve(self, browser):
        self.validate(browser)
        active = sum(b.status()['running'] or b.id in self.starting for b in self.instances.values())
        if active >= self.max_instances and browser.id not in self.starting:
            raise ValueError(f'The browser memory budget allows {self.max_instances} active instances. Stop an unused one first.')
        self.starting.add(browser.id)

    def validate(self, browser):
        if self.instances.get(browser.id) is not browser or self.project != self.runtime.identity:
            raise ValueError('That browser instance was closed; reopen it for this project')

    def summaries(self):
        return [{'id': b.id, 'agent': b.agent, 'label': b.label, 'running': b.status()['running'],
                 'pages': len(b.pages), 'owner': b.owner} for b in self.instances.values()]

    def wrap(self, browser, result):
        return {**result, 'instances': self.summaries(), 'max_instances': self.max_instances,
                'idle_timeout': IDLE_SECONDS}

    def choose(self, actor, label, arguments, create=True):
        instance = arguments.get('browser')
        page = arguments.get('page')
        if page:
            match = next((b for b in self.instances.values() if page in b.pages), None)
            if not match or instance and instance != match.id:
                raise ValueError('That browser page was closed; choose an open page')
            instance = match.id
        if actor != 'user':
            own = self.agents.get(label)
            if instance and instance != own:
                raise ValueError('That page belongs to another agent browser; use your own instance')
            instance = own
        elif not instance:
            running = [b for b in self.instances.values() if b.status()['running']]
            instance = self.default if self.default in self.instances and self.instances[self.default].status()['running'] else (
                running[0].id if running else self.agents.get('user'))
        if instance:
            if instance not in self.instances:
                raise ValueError('That browser instance was closed')
            return self.instances[instance]
        if not create:
            raise ValueError('Open a local preview first')
        browser = Browser(self.runtime)
        browser.agent, browser.label = ('user', 'Your browser') if actor == 'user' else (label, label)
        browser.protected_debug_ports = self.debug_ports
        browser.capacity_guard = lambda: self.reserve(browser)
        browser.lifecycle_guard = lambda: self.validate(browser)
        self.instances[browser.id] = browser
        self.agents[browser.agent] = browser.id
        return browser

    async def prepare(self, actor, label, arguments):
        async with self.lock:
            if not self.cleaned_profiles:
                # Clean once before any concurrent startup can create a profile.
                await asyncio.to_thread(remove_stale_profiles)
                self.cleaned_profiles = True
            if self.project != self.runtime.identity:
                await self.close()
                self.project = self.runtime.identity
            if not self.reaper:
                self.reaper = asyncio.create_task(self.reap())
            return self.choose(actor, label, arguments)

    async def act(self, actor, label, arguments):
        if not isinstance(arguments, dict):
            raise ValueError('Browser arguments must be an object')
        browser = await self.prepare(actor, label, arguments)
        self.selection.set(browser)
        try:
            result = await browser.act(actor, label, arguments)
            if result['running'] and not self.default:
                self.default = browser.id
            return self.wrap(browser, result)
        finally:
            self.starting.discard(browser.id)

    async def publish_activity(self, actor, label, action, phase, page=None):
        browser = await self.prepare(actor, label, {})
        self.selection.set(browser)
        browser.activity = {'actor': actor, 'label': label, 'action': action,
                            'phase': phase, 'time': time.time(), 'page': page or browser.active}
        await self.runtime.emit('browser_activity', self.wrap(browser, browser.status()))

    async def close(self):
        if self.reaper and self.reaper is not asyncio.current_task():
            self.reaper.cancel()
            await asyncio.gather(self.reaper, return_exceptions=True)
        self.reaper = None
        instances = list(self.instances.values())
        self.instances.clear()
        self.agents.clear()
        self.starting.clear()
        self.default = None
        async def close_instance(browser):
            async with browser.lock:
                await browser.close()
        await asyncio.gather(*(close_instance(browser) for browser in instances), return_exceptions=True)

    async def reap(self):
        while True:
            await asyncio.sleep(1)
            for browser in list(self.instances.values()):
                for page in list(browser.pages.values()):
                    if page.streaming and not page.viewers:
                        async with page.stream_lock:
                            if not page.viewers:
                                with contextlib.suppress(Exception):
                                    await browser.command('Page.stopScreencast', page=page)
                                page.streaming = False
                browser.expire()
                if (browser.status()['running'] and not browser.owner and not browser.lock.locked()
                        and not any(p.viewers for p in browser.pages.values())
                        and time.monotonic() - browser.last_action >= IDLE_SECONDS):
                    async with browser.lock:
                        await browser.close()


def install(host, runtime):
    browser = runtime.browser = BrowserManager(runtime)

    @host.app.get('/api/runtime/browser')
    async def status(request: Request):
        """Read-only and never published as activity. page targets one page (a closed id is refused,
        never redirected); project, when given, must be the open project."""
        project = request.query_params.get('project')
        if project is not None and project != runtime.identity:
            return JSONResponse({'error': 'Project changed; reload the browser view'}, 409)
        arguments = {'action': 'status'}
        if request.query_params.get('page'):
            arguments['page'] = request.query_params['page']
        if request.query_params.get('browser'):
            arguments['browser'] = request.query_params['browser']
        try:
            return await browser.act('user', 'You', arguments)
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, 400)

    @host.app.post('/api/runtime/browser')
    async def action(request: Request):
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError('Browser arguments must be an object')
            if body.get('project') != runtime.identity:
                return JSONResponse({'error': 'Project changed; reload the browser view'}, 409)
            result = await browser.act('user', 'You', body)
            selected = browser.selection.get()
            selected.activity = {'actor': 'user', 'label': 'You', 'action': body.get('action', 'status'),
                                 'phase': 'done', 'time': time.time(), 'page': result.get('page')}
            await runtime.emit('browser_activity', browser.wrap(selected, selected.status()))
            return {**result, 'activity': browser.activity}
        except (ValueError, TypeError, OSError) as exc:
            return JSONResponse({'error': str(exc)}, 400)

    @host.app.get('/api/runtime/browser/frame')
    async def frame(request: Request):
        try:
            if request.query_params.get('project') != runtime.identity:
                return Response(status_code=409)
            selected = await browser.prepare('user', 'You', dict(request.query_params))
            page = selected.page(dict(request.query_params))
            return Response(await selected.latest_frame(page), media_type='image/jpeg', headers={'Cache-Control': 'no-store'})
        except ValueError:
            return Response(status_code=404)
        except (OSError, asyncio.TimeoutError):
            return Response(status_code=409)

    @host.app.get('/api/runtime/browser/stream')
    async def stream(request: Request):
        try:
            if request.query_params.get('project') != runtime.identity:
                return Response(status_code=409)
            selected = await browser.prepare('user', 'You', dict(request.query_params))
            page = selected.page(dict(request.query_params))
            # Start/validate before headers; streaming never holds the action lock.
            await selected.start_stream(page)
            return StreamingResponse(selected.stream(page), media_type='multipart/x-mixed-replace; boundary=frame',
                                     headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
        except (ValueError, OSError):
            return Response(status_code=409)

    @host.app.post('/api/runtime/browser/file')
    async def upload(request: Request):
        try:
            selected = await browser.prepare('user', 'You', dict(request.query_params))
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, 400)
        async with selected.lock:
            try:
                if request.query_params.get('project') != runtime.identity or selected.project != runtime.identity:
                    raise ValueError('Project changed; reopen the browser')
                selected.control('user')
                page = selected.page({'page': request.query_params.get('page', '')})
                if not page.file_node:
                    raise ValueError('The page has no pending file picker')
                filename = Path(unquote(request.headers.get('X-File-Name', 'upload')).replace('\\', '/')).name
                if not filename or filename in ('.', '..') or ':' in filename:
                    raise ValueError('Invalid filename')
                target = Path(selected.profile.name) / 'uploads' / filename
                target.parent.mkdir(exist_ok=True)
                count = 0
                with target.open('wb') as output:
                    async for chunk in request.stream():
                        count += len(chunk)
                        if count > 16 * 1024 * 1024:
                            raise ValueError('File must be smaller than 16 MB')
                        output.write(chunk)
                await selected.command('DOM.setFileInputFiles', {'files': [str(target)], 'backendNodeId': page.file_node}, page=page)
                page.file_node = None
                return browser.wrap(selected, selected.status(page))
            except (ValueError, OSError) as exc:
                return JSONResponse({'error': str(exc)}, 400)
