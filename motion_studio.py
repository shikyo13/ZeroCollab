"""Motion studio: saved synthetic scenes, an in-app preview page and local video export.

A scene is JSON data (vocabulary v1 beats plus a declarative layout), never code. view_html()
builds one self-contained page from motion/scene.html: the page options and the validated scene as
JSON data blocks, motion.js and boot.js inline, and a meta CSP that allows only those two scripts
(by sha256) and no network. The app fetches that page with its session and loads it into a
sandboxed srcdoc frame; export renders the same page headless, seeks every frame through the
scene's own __seek(ms) and pipes the PNGs straight into ffmpeg (VP9/WebM), so preview and video show identical
frames. Nothing here starts an agent, calls a model or reads a session. Every route sits behind the
existing session middleware (security 9ccff0d7: no exemption).

Contract (frontend 89b904b3, daeb1e97, 5c6ab17f):
  GET  /api/runtime/motion/scenes[?project=]          {project, scenes: [...], invalid: [...], export: {...}}
  GET  /api/runtime/motion/view?project=&scene=<ref>[&reduced=1][&t=<ms>]   preview HTML for srcdoc
  GET  /api/runtime/motion/exports[?project=]          {current: job|null, exports: [job]}
  POST /api/runtime/motion/exports {project, scene, reduced}   202 job | 400 | 409 | 503
  POST /api/runtime/motion/exports/{id}/cancel {project}       job | 404 | 409
  GET  /api/runtime/motion/exports/{id}/video[?download=1]     video/webm (VP9) with range support
Scene refs are "bundled:<id>" (shipped in motion/scenes) or "project:<id>" (the open project's
data/projects/<project>/motion/scenes/<id>.json). Exports are kept in .../motion/exports.

Encoder (desktop c7dc70c6, 38b76c64): export is VP9 in WebM; libvpx is BSD-licensed, so no GPL or
nonfree codec is needed. The host names the encoder in config.toml as [motion] ffmpeg = "<absolute
path>"; when set, only that file is used and PATH is never searched (desktop ships a pinned LGPL
build). When unset (web development) the ffmpeg on PATH is used. Either way the build must pass
encoder_check(): the libvpx-vp9 encoder, the png decoder, the image2pipe demuxer and the webm muxer.
"""
import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import httpx
import websockets
from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

import shared_browser

ROOT = Path(__file__).resolve().parent
# VP9 in WebM: about 5 s to encode 20 s of 1080p30 on the reference machine, far below capture time.
VP9 = ['-c:v', 'libvpx-vp9', '-pix_fmt', 'yuv420p', '-b:v', '0', '-crf', '30', '-deadline', 'good',
       '-cpu-used', '4', '-row-mt', '1', '-tile-columns', '2', '-an', '-f', 'webm']
KIT = ROOT / 'motion'
BUNDLED = KIT / 'scenes'
# core.js is the shared motion-core library (S1, frontend 1f9560e4); it is inlined first when it ships.
SCRIPTS = ('core.js', 'motion.js', 'boot.js')
OPTIONAL_SCRIPTS = ('core.js',)
WIDTH, HEIGHT, FPS = 1920, 1080, 30
MAX_SCENE_BYTES = 256 * 1024
CALL_TIMEOUT = 30  # seconds for any one DevTools command; a browser that stops answering fails the job
ID = re.compile(r'^[a-z0-9][a-z0-9-]{0,47}$')
KINDS = ('agent', 'tool', 'board', 'handoff', 'browser', 'caption')
# Fixed rendering inputs, so one machine always produces the same pixels (approved kit r1).
RENDER_FLAGS = ['--headless=new', '--disable-gpu', '--do-not-de-elevate', '--hide-scrollbars', '--mute-audio',
                '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--disable-sync',
                '--disable-background-networking', '--disable-component-update', '--block-new-web-contents',
                '--disable-lcd-text', '--font-render-hinting=none', '--force-device-scale-factor=1',
                '--force-color-profile=srgb', f'--window-size={WIDTH},{HEIGHT}', '--allow-file-access-from-files',
                '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1']


class Cancelled(Exception):
    pass


class Unavailable(Exception):
    """A required local tool (ffmpeg or a Chromium browser) is missing: HTTP 503."""


# ---- scene validation -------------------------------------------------------------------------

def _text(value, where, limit, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise ValueError(f'{where} must be text of at most {limit} characters')
    return value


def _int(value, where, low, high):
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ValueError(f'{where} must be a whole number from {low} to {high}')
    return value


def _keys(obj, where, allowed, required=()):
    if not isinstance(obj, dict):
        raise ValueError(f'{where} must be an object')
    extra = set(obj) - set(allowed)
    if extra:
        raise ValueError(f'{where} has unknown field {sorted(extra)[0]!r}')
    for key in required:
        if key not in obj:
            raise ValueError(f'{where} is missing {key!r}')


def _list(value, where, high, low=0):
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise ValueError(f'{where} must be a list of {low} to {high} items')
    return value


def validate(scene):
    """Return the scene unchanged if it is a valid v1 scene, else raise ValueError naming the problem.
    Every string is shown as text (textContent), never markup; limits keep a scene renderable."""
    _keys(scene, 'scene', ('version', 'id', 'name', 'source', 'duration_ms', 'layout', 'beats'),
          ('version', 'id', 'duration_ms', 'layout', 'beats'))
    if scene['version'] != 1:
        raise ValueError('scene version must be 1')
    if not isinstance(scene['id'], str) or not ID.match(scene['id']):
        raise ValueError('scene id must be lowercase letters, digits and hyphens, at most 48')
    _text(scene.get('name'), 'scene name', 80, required=False)
    _text(scene.get('source'), 'scene source', 200, required=False)
    duration = _int(scene['duration_ms'], 'duration_ms', 1000, 600000)

    layout = scene['layout']
    _keys(layout, 'layout', ('title', 'agents', 'board', 'browser', 'cards'))
    _text(layout.get('title'), 'layout title', 120, required=False)
    agents = set()
    for i, a in enumerate(_list(layout.get('agents', []), 'layout agents', 12)):
        _keys(a, f'agent {i + 1}', ('id', 'name', 'x', 'y'), ('id', 'name', 'x', 'y'))
        _text(a['id'], f'agent {i + 1} id', 40)
        _text(a['name'], f'agent {i + 1} name', 40)
        _int(a['x'], f'agent {i + 1} x', 0, WIDTH - 400)
        _int(a['y'], f'agent {i + 1} y', 0, HEIGHT - 236)
        agents.add(a['id'])
    columns = set()
    if 'board' in layout:
        b = layout['board']
        _keys(b, 'board', ('x', 'y', 'columns'), ('x', 'y', 'columns'))
        _int(b['x'], 'board x', 0, WIDTH)
        _int(b['y'], 'board y', 0, HEIGHT)
        for i, name in enumerate(_list(b['columns'], 'board columns', 6, 1)):
            columns.add(_text(name, f'board column {i + 1}', 24))
    if 'browser' in layout:
        w = layout['browser']
        _keys(w, 'browser', ('title', 'nav', 'lines'))
        _text(w.get('title'), 'browser title', 80, required=False)
        for i, item in enumerate(_list(w.get('nav', []), 'browser nav', 8)):
            _text(item, f'browser nav item {i + 1}', 24)
        if 'lines' in w:
            _int(w['lines'], 'browser lines', 0, 12)
    cards = set()
    for i, c in enumerate(_list(layout.get('cards', []), 'layout cards', 20)):
        _keys(c, f'card {i + 1}', ('id', 'title', 'meta', 'column'), ('id', 'title', 'column'))
        _text(c['id'], f'card {i + 1} id', 40)
        _text(c['title'], f'card {i + 1} title', 60)
        _text(c.get('meta'), f'card {i + 1} meta', 80, required=False)
        if c['column'] not in columns:
            raise ValueError(f'card {c["id"]!r} starts in unknown column {c["column"]!r}')
        cards.add(c['id'])

    targets = {'panel', 'sidebar'} if 'browser' in layout else set()
    for i, beat in enumerate(_list(scene['beats'], 'beats', 400, 1)):
        where = f'beat {i + 1}'
        _keys(beat, where, ('at_ms', 'duration_ms', 'kind', 'actor', 'label', 'detail', 'state', 'target',
                            'run_id', 'task_id', 'artifact_id'), ('at_ms', 'duration_ms', 'kind', 'label', 'state'))
        _int(beat['at_ms'], f'{where} at_ms', 0, duration)
        _int(beat['duration_ms'], f'{where} duration_ms', 0, duration)
        if beat['kind'] not in KINDS:
            raise ValueError(f'{where} kind must be one of {", ".join(KINDS)}')
        _text(beat.get('actor'), f'{where} actor', 40, required=False)
        _text(beat['label'], f'{where} label', 120)
        _text(beat['state'], f'{where} state', 40)
        for field, limit in (('detail', 200), ('target', 80), ('run_id', 80), ('task_id', 80), ('artifact_id', 80)):
            _text(beat.get(field), f'{where} {field}', limit, required=False)
        kind, actor = beat['kind'], beat.get('actor')
        if kind in ('agent', 'tool', 'handoff') and actor not in agents:
            raise ValueError(f'{where} names unknown agent {actor!r}')
        if kind == 'handoff' and beat.get('target') not in agents:
            raise ValueError(f'{where} hands off to unknown agent {beat.get("target")!r}')
        if kind == 'board' and (beat['label'] not in cards or beat['state'] not in columns):
            raise ValueError(f'{where} moves an unknown card or to an unknown column')
        if kind == 'browser' and not targets:
            raise ValueError(f'{where} needs a browser in the layout')
    return scene


# ---- scene storage ----------------------------------------------------------------------------

PROJECT_REQUIRED = 'Video export needs a project folder. Create or open a project from Projects.'


def project_dir(runtime):
    identity = getattr(runtime, 'identity', None)
    return runtime.data / 'projects' / identity / 'motion' if identity and identity != 'chat' else None


def load(path):
    """A saved scene of any supported version (motion_scene dispatches on it)."""
    import motion_scene
    if path.stat().st_size > MAX_SCENE_BYTES:
        raise ValueError(f'scene file is larger than {MAX_SCENE_BYTES // 1024} KB')
    scene = json.loads(path.read_text('utf-8'))
    found = motion_scene.issues(scene)
    if found:
        raise ValueError(f"{found[0]['path']}: {found[0]['message']}")
    if scene['id'] != path.stem:
        raise ValueError(f'scene id {scene["id"]!r} must match its file name {path.stem!r}')
    return scene


def drawable(scene):
    """The v1 scene the current renderer draws for this scene, or ValueError: a v2 scene beyond a single
    migrated room layer waits for the v2 renderer (S1/S5) and never falls back to the v1 one silently."""
    import motion_scene
    v1 = motion_scene.legacy(scene)
    if v1 is None:
        raise ValueError('This scene needs the v2 renderer (motion core S1 and layers S5), which is not installed yet')
    return v1


def catalogue(runtime):
    """Scenes by ref. Only names that come from a directory listing are ever opened."""
    found, invalid = {}, []
    folders = [('bundled', BUNDLED)]
    if project_dir(runtime):
        folders.append(('project', project_dir(runtime) / 'scenes'))
    for source, folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob('*.json')):
            try:
                scene = load(path)
                found[f'{source}:{scene["id"]}'] = (source, scene)
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                invalid.append({'file': f'{source}/{path.name}', 'error': str(exc)})
    return found, invalid


def summary(ref, source, scene):
    import motion_scene
    v1 = motion_scene.legacy(scene)
    if scene['version'] == 1:
        duration = scene['duration_ms']
    else:
        comp = scene['compositions'][0]
        duration = round(motion_scene.frame_ms(comp['duration'], comp['fps']))
    return {'ref': ref, 'id': scene['id'], 'name': scene.get('name') or scene['id'], 'source': source,
            'version': scene['version'], 'renderable': v1 is not None,
            'duration_ms': duration, 'beats': len(v1['beats']) if v1 else 0}

def _block(block_id, value):
    # '<' is escaped so no text inside the data can close its block.
    data = json.dumps(value, ensure_ascii=False).replace('<', '\\u003c')
    return f'<script type="application/json" id="{block_id}">{data}</script>'


def view_html(scene, options):
    """One self-contained page: options and scene as JSON data blocks, motion.js and boot.js inline.
    Its meta CSP allows exactly those two inline scripts (by sha256) and nothing from the network,
    so it needs no second request and no token when loaded into a srcdoc frame.
    options: {render: bool, reduced: bool, embed: bool, t: int ms}."""
    scripts = [(KIT / name).read_text('utf-8') for name in SCRIPTS
               if name not in OPTIONAL_SCRIPTS or (KIT / name).is_file()]
    if any('</script' in code.lower() for code in scripts):
        raise RuntimeError('motion scripts cannot be inlined')
    hashes = ' '.join("'sha256-" + base64.b64encode(hashlib.sha256(code.encode()).digest()).decode() + "'"
                      for code in scripts)
    csp = (f"default-src 'none'; script-src {hashes}; style-src 'unsafe-inline'; img-src data:; "
           "base-uri 'none'; form-action 'none'")
    page = (KIT / 'scene.html').read_text('utf-8')
    page = page.replace('<!--ZM-CSP-->', f'<meta http-equiv="Content-Security-Policy" content="{csp}">', 1)
    body = (_block('zm-options', options) + '\n' + _block('zm-scene', scene) + '\n'
            + ''.join(f'<script>{code}</script>\n' for code in scripts))
    return page.replace('<!--ZM-SCENE-->', body, 1)


# ---- export -----------------------------------------------------------------------------------

_encoder_cache = {}


def ffmpeg_path(runtime):
    """The host-configured encoder ([motion] ffmpeg, an absolute path), else ffmpeg on PATH.
    A configured path is never replaced by a PATH lookup, even when it is missing."""
    configured = ((getattr(runtime, 'cfg', None) or {}).get('motion') or {}).get('ffmpeg')
    if configured:
        path = Path(configured)
        if not path.is_absolute() or not path.is_file():
            raise Unavailable('The configured FFmpeg for video export was not found: ' + str(configured)[:200])
        return str(path)
    found = shutil.which('ffmpeg')
    if not found:
        raise Unavailable('Video export needs FFmpeg with VP9 (libvpx) support, and none was found')
    return found


def encoder_check(path):
    """Capabilities of one ffmpeg build, cached by path and modification time. Returns
    {path, version, license, missing}; missing lists what export needs and the build lacks.
    license is 'LGPL' unless the build was configured with --enable-gpl or --enable-nonfree."""
    import subprocess
    stamp = (path, Path(path).stat().st_mtime_ns)
    if stamp in _encoder_cache:
        return _encoder_cache[stamp]

    def run(*args):
        return subprocess.run([path, '-hide_banner', *args], capture_output=True, text=True, timeout=15).stdout

    version = run('-version')
    lists = {'encoder libvpx-vp9': run('-encoders'), 'decoder png': run('-decoders'),
             'demuxer image2pipe': run('-demuxers'), 'muxer webm': run('-muxers')}
    missing = [need for need, listing in lists.items()
               if not any(line.split()[1:2] == [need.split()[1]] for line in listing.splitlines() if line.split())]
    config = next((line for line in version.splitlines() if line.startswith('configuration:')), '')
    license_ = 'nonfree' if '--enable-nonfree' in config else 'GPL' if '--enable-gpl' in config else 'LGPL'
    result = {'path': path, 'version': (version.splitlines() or [''])[0][:120], 'license': license_, 'missing': missing}
    _encoder_cache[stamp] = result
    return result


def require_tools(runtime):
    """The encoder's capability record, or Unavailable naming what is missing."""
    check = encoder_check(ffmpeg_path(runtime))
    if check['missing']:
        raise Unavailable('The installed FFmpeg cannot export WebM video; it lacks ' + ', '.join(check['missing']))
    if not shared_browser.executable():
        raise Unavailable('Video export needs Microsoft Edge, Google Chrome or Chromium')
    return check


class Exporter:
    """One export at a time: a render is CPU-heavy and owns a headless browser while it runs."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.current = None
        self.task = None
        self.framing = None  # an agent's frame render (room_motion frame) shares the one render slot

    def busy(self):
        return bool(self.current and self.current['phase'] in ('starting', 'rendering', 'encoding'))

    def finished(self):
        folder = project_dir(self.runtime)
        if not folder or not (folder / 'exports').is_dir():
            return []
        jobs = []
        for meta in (folder / 'exports').glob('*.json'):
            try:
                job = json.loads(meta.read_text('utf-8'))
                if meta.with_suffix('.webm').is_file():
                    jobs.append(job)
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(jobs, key=lambda j: j.get('finished', 0), reverse=True)

    def video(self, job_id):
        if not re.fullmatch(r'[0-9a-f]{12}', job_id or ''):
            return None
        folder = project_dir(self.runtime)
        path = folder / 'exports' / f'{job_id}.webm' if folder else None
        return path if path and path.is_file() else None

    def start(self, ref, reduced):
        if self.busy():
            raise RuntimeError('An export is already running; cancel it or wait for it to finish')
        if self.framing:
            raise RuntimeError(f'{self.framing} is rendering a preview frame; try again in a few seconds')
        base = project_dir(self.runtime)
        if not base:
            raise ValueError(PROJECT_REQUIRED)
        scenes, _ = catalogue(self.runtime)
        if ref not in scenes:
            raise ValueError('Unknown or invalid scene')
        scene = drawable(scenes[ref][1])
        encoder = require_tools(self.runtime)
        self.current = {'id': uuid.uuid4().hex[:12], 'scene': ref, 'scene_id': scene['id'],
                        'name': scene.get('name') or scene['id'], 'reduced': bool(reduced), 'phase': 'starting',
                        'frame': 0, 'frames': scene['duration_ms'] * FPS // 1000, 'width': WIDTH, 'height': HEIGHT,
                        'fps': FPS, 'format': 'webm', 'codec': 'vp9', 'created': time.time(),
                        'project': self.runtime.identity,
                        'encoder': {k: encoder[k] for k in ('version', 'license')}}
        job = self.current
        self.task = asyncio.create_task(self.run(job, scene, base / 'exports', encoder['path']))
        return public(job)

    def cancel(self, job_id):
        job = self.current
        if not job or job['id'] != job_id:
            return None
        if job['phase'] in ('starting', 'rendering', 'encoding'):
            job['cancel'] = True
        return public(job)

    async def close(self):
        """Room shutdown: stop a running export so its browser and ffmpeg end with the room."""
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def run(self, job, scene, folder, ffmpeg):
        partial = folder / f'{job["id"]}.part.webm'
        work = Path(tempfile.mkdtemp(prefix='zerocollab-motion-'))
        try:
            folder.mkdir(parents=True, exist_ok=True)
            started = time.perf_counter()
            hashes = await export_video(scene, job['reduced'], partial, work, job, ffmpeg)
            job['render_s'] = round(time.perf_counter() - started, 2)
            final = folder / f'{job["id"]}.webm'
            os.replace(partial, final)
            job.update(phase='done', finished=time.time(), bytes=final.stat().st_size,
                       seconds=scene['duration_ms'] / 1000,
                       frames_digest=hashlib.sha256(''.join(hashes).encode()).hexdigest(),
                       video_url=f'/api/runtime/motion/exports/{job["id"]}/video')
            (folder / f'{job["id"]}.json').write_text(json.dumps(public(job), indent=1), 'utf-8')
        except Cancelled:
            job.update(phase='cancelled', finished=time.time())
        except asyncio.CancelledError:
            job.update(phase='cancelled', finished=time.time(), error='The room shut down during the export')
            raise
        except Exception as exc:  # reported to the user as the job's error, never retried
            job.update(phase='failed', finished=time.time(), error=str(exc) or type(exc).__name__)
        finally:
            # Cleanup never changes the outcome: a browser that was just killed can hold its
            # profile files for a moment on Windows (frontend c0977a2f), so retry, then give up.
            await remove_tree(work)
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass


async def remove_tree(path, attempts=50):
    for _ in range(attempts):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        await asyncio.sleep(.1)


def public(job):
    return {k: v for k, v in job.items() if k != 'cancel'} if job else None


async def export_video(scene, reduced, out, work, job, ffmpeg):
    """Render every frame of scene and pipe it into ffmpeg, writing a VP9 WebM to out.
    Returns the sha256 of each frame's PNG in order. No frame is stored on disk. ffmpeg is always
    stopped before this returns, including on cancellation or room shutdown."""
    page = work / 'scene.html'
    page.write_text(view_html(scene, {'render': True, 'reduced': bool(reduced)}), 'utf-8')
    encoder = await asyncio.create_subprocess_exec(
        ffmpeg, '-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', str(FPS), '-c:v', 'png',
        '-i', '-', *VP9, str(out),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    errors = asyncio.create_task(encoder.stderr.read())

    async def failure():
        await asyncio.wait({errors}, timeout=5)
        text = errors.result().decode(errors='replace').strip() if errors.done() else ''
        return RuntimeError('ffmpeg failed: ' + (text[-300:] or f'exit {encoder.returncode}'))

    hashes = []

    async def sink(png):
        hashes.append(hashlib.sha256(png).hexdigest())
        try:
            encoder.stdin.write(png)
            await encoder.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            raise await failure()

    try:
        await render(page, job, sink, work / 'profile')
        job['phase'] = 'encoding'
        encoder.stdin.close()
        while encoder.returncode is None:
            if job.get('cancel'):
                raise Cancelled()
            try:
                await asyncio.wait_for(encoder.wait(), .25)
            except asyncio.TimeoutError:
                pass
        if encoder.returncode != 0:
            raise await failure()
        return hashes
    finally:
        if encoder.returncode is None:
            encoder.kill()
            await encoder.wait()
        errors.cancel()


async def render(page, job, sink, profile):
    """Load the page headless and pass each frame's PNG to sink, in order. The browser runs in a
    kill-on-close job on Windows, so no child outlives the export; every DevTools command times out."""
    def check():
        if job.get('cancel'):
            raise Cancelled()

    profile.mkdir()
    args = [shared_browser.executable(), *RENDER_FLAGS, f'--user-data-dir={profile}', 'about:blank']
    proc = (shared_browser.WindowsBrowserProcess(args) if os.name == 'nt' else
            await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL,
                                                 stderr=asyncio.subprocess.DEVNULL))
    ws = None
    try:
        marker = profile / 'DevToolsActivePort'
        for _ in range(150):
            check()
            if marker.exists() and marker.read_text().strip():
                break
            code = proc.returncode
            if code is not None and not (os.name == 'nt' and code == 0):
                raise RuntimeError('The browser exited during startup')
            await asyncio.sleep(.1)
        else:
            raise RuntimeError('The browser did not start within 15 seconds')
        port = int(marker.read_text().splitlines()[0])
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            pages = (await client.get(f'http://127.0.0.1:{port}/json/list')).json()
        ws = await asyncio.wait_for(websockets.connect(
            next(p for p in pages if p['type'] == 'page')['webSocketDebuggerUrl'], max_size=64 * 1024 * 1024), CALL_TIMEOUT)
        ids = iter(range(1, 1 << 30))

        async def reply(mid):
            while True:
                msg = json.loads(await ws.recv())
                if msg.get('id') == mid:
                    return msg

        async def call(method, **params):
            mid = next(ids)
            await ws.send(json.dumps({'id': mid, 'method': method, 'params': params}))
            try:
                msg = await asyncio.wait_for(reply(mid), CALL_TIMEOUT)
            except asyncio.TimeoutError:
                raise RuntimeError(f'The browser stopped responding ({method})') from None
            if 'error' in msg:
                raise RuntimeError(f'{method}: {msg["error"].get("message")}')
            return msg.get('result', {})

        async def evaluate(expression, wait=False):
            r = await call('Runtime.evaluate', expression=expression, awaitPromise=wait, returnByValue=True)
            if 'exceptionDetails' in r:
                raise RuntimeError(r['exceptionDetails'].get('text') or 'scene script error')
            return r.get('result', {}).get('value')

        await call('Network.enable')
        await call('Network.setBlockedURLs', urls=['http://*', 'https://*', 'ws://*', 'wss://*'])
        await call('Emulation.setDeviceMetricsOverride', width=WIDTH, height=HEIGHT, deviceScaleFactor=1, mobile=False)
        await call('Page.navigate', url=page.as_uri())
        for _ in range(400):
            check()
            state = await evaluate('({ready: window.__ready === true, error: window.__error || null})')
            if state and state['error']:
                raise RuntimeError(state['error'])
            if state and state['ready']:
                break
            await asyncio.sleep(.025)
        else:
            raise RuntimeError('The scene did not become ready within 10 seconds')

        job['phase'] = 'rendering'
        # An export seeks every frame; an agent's preview (room_motion frame) seeks only the times it asked for.
        seeks = job.get('times') or [round(i * 1000 / FPS, 4) for i in range(job['frames'])]
        for i, ms in enumerate(seeks):
            check()
            await evaluate(f'window.__seek({ms})', wait=True)
            shot = await call('Page.captureScreenshot', format='png', fromSurface=True,
                              clip={'x': 0, 'y': 0, 'width': WIDTH, 'height': HEIGHT, 'scale': 1})
            await sink(base64.b64decode(shot['data']))
            job['frame'] = i + 1
    finally:
        if ws:
            await ws.close()
        if os.name == 'nt':
            proc.terminate()
            proc.close_handles()
        elif proc.returncode is None:
            proc.kill()
            await proc.wait()


# ---- agent tool: room_motion (task 78f77506, motion-agent-interface/CONTRACT.md r2) -----------------

MAX_FRAMES = 6
FRAME_SECONDS = 30
AGENT_READS = ('list', 'get', 'validate', 'export_status')


def save_scene(runtime, scene, overwrite=False):
    """Write a valid scene to the open project's motion/scenes/<id>.json, atomically. The file name is the
    validated id (no separators), bundled scenes are never written, and an existing id needs overwrite."""
    import motion_scene
    base = project_dir(runtime)
    if not base:
        raise ValueError(PROJECT_REQUIRED)
    found = motion_scene.issues(scene)
    if found:
        raise ValueError(f"The scene is not valid ({len(found)} issue(s)); call validate first. "
                         f"First: {found[0]['path']}: {found[0]['message']}")
    data = json.dumps(scene, indent=1, ensure_ascii=False)
    if len(data.encode()) > MAX_SCENE_BYTES:
        raise ValueError(f'scene is larger than {MAX_SCENE_BYTES // 1024} KB')
    folder = base / 'scenes'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{scene['id']}.json"
    if path.exists() and not overwrite:
        raise ValueError(f"project:{scene['id']} already exists; pass overwrite true to replace it")
    partial = folder / f".{scene['id']}.{uuid.uuid4().hex[:6]}.tmp"
    partial.write_text(data, 'utf-8')
    os.replace(partial, path)
    return f"project:{scene['id']}"


def contact_sheet(pngs, times):
    """One PNG of up to 6 frames in a 3-column grid at 640x360 each, labelled with their times."""
    import io
    from PIL import Image, ImageDraw
    w, h = 640, 360
    cols = min(3, len(pngs))
    rows = (len(pngs) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * w, rows * h), '#000000')
    draw = ImageDraw.Draw(sheet)
    for i, (png, ms) in enumerate(zip(pngs, times)):
        frame = Image.open(io.BytesIO(png)).convert('RGB').resize((w, h))
        x, y = (i % cols) * w, (i // cols) * h
        sheet.paste(frame, (x, y))
        draw.rectangle((x, y, x + 110, y + 26), fill='#000000')
        draw.text((x + 8, y + 6), f'{ms / 1000:.2f} s', fill='#ffffff')
    out = io.BytesIO()
    sheet.save(out, 'PNG')
    return out.getvalue()


async def render_frames(runtime, scene, times, reduced):
    """PNG bytes of the scene at each time (ms), through the same page and __seek(ms) as export, so a
    preview frame is pixel-identical to that frame of the video."""
    work = Path(tempfile.mkdtemp(prefix='zerocollab-motion-frame-'))
    job = {'times': times, 'frames': len(times), 'frame': 0, 'phase': 'starting'}
    pngs = []

    async def sink(png):
        pngs.append(png)
    try:
        page = work / 'scene.html'
        page.write_text(view_html(scene, {'render': True, 'reduced': bool(reduced)}), 'utf-8')
        await asyncio.wait_for(render(page, job, sink, work / 'profile'), FRAME_SECONDS)
    except asyncio.TimeoutError:
        raise RuntimeError(f'The preview frames did not render within {FRAME_SECONDS} seconds') from None
    finally:
        await remove_tree(work)
    return pngs


async def agent_action(runtime, run, arguments):
    """room_motion for an agent turn. Reads are open; save/frame/export change the room (changes_room), so
    sealed decision turns and manager check-ins never reach them. No argument names a path, URL or binary."""
    import motion_scene
    exporter = runtime.motion
    action = arguments.get('action', 'list')
    found, invalid = catalogue(runtime)
    if action == 'list':
        return {'project': runtime.identity, 'scenes': [summary(ref, s, scene) for ref, (s, scene) in found.items()],
                'invalid': invalid, 'export': {'format': 'webm', 'width': WIDTH, 'height': HEIGHT, 'fps': FPS,
                                               'project_folder': project_dir(runtime) is not None}}
    if action == 'export_status':
        current = exporter.current if exporter.current and exporter.current.get('project') == runtime.identity else None
        return {'current': public(current), 'exports': exporter.finished()}
    if action == 'validate':
        scene = arguments.get('scene_json')
        found_issues = motion_scene.issues(scene)
        if found_issues:
            return {'ok': False, 'issues': found_issues}
        return {'ok': True, 'summary': summary(f"project:{scene['id']}", 'project', scene), 'checks': []}
    if action == 'save':
        scene = arguments.get('scene_json')
        ref = save_scene(runtime, scene, bool(arguments.get('overwrite')))
        await runtime.activity_note(run, 'motion', f'Saved motion scene {ref}')
        return {'ref': ref, 'summary': summary(ref, 'project', scene)}
    if action == 'export_cancel':
        job = exporter.cancel(str(arguments.get('job_id', '')))
        if not job:
            raise ValueError('No running export with that id')
        return job
    ref = arguments.get('scene')
    if not isinstance(ref, str) or ref not in found:
        raise ValueError('Unknown or invalid scene; use a ref from list, such as bundled:m01 or project:<id>')
    scene = found[ref][1]
    if action == 'get':
        return scene
    if action == 'export':
        job = exporter.start(ref, bool(arguments.get('reduced')))
        await runtime.activity_note(run, 'motion', f'Started a video export of {ref}')
        return job
    if action != 'frame':
        raise ValueError('action must be list, get, validate, save, frame, export, export_status or export_cancel')
    drawn = drawable(scene)
    times = arguments.get('t_ms', 0)
    times = times if isinstance(times, list) else [times]
    if not 1 <= len(times) <= MAX_FRAMES or any(isinstance(t, bool) or not isinstance(t, (int, float))
                                                 or not 0 <= t <= drawn['duration_ms'] for t in times):
        raise ValueError(f"t_ms must be one time or a list of 1 to {MAX_FRAMES} times from 0 to {drawn['duration_ms']} ms")
    if exporter.busy():
        job = public(exporter.current)
        raise RuntimeError(f"A video export is rendering ({job['frame']} of {job['frames']} frames); try again after it")
    if exporter.framing:
        raise RuntimeError(f'{exporter.framing} is rendering preview frames; try again in a few seconds')
    exporter.framing = run.name
    try:
        if not shared_browser.executable():
            raise Unavailable('Preview frames need Microsoft Edge, Google Chrome or Chromium')
        pngs = await render_frames(runtime, drawn, [round(t, 4) for t in times], arguments.get('reduced'))
    finally:
        exporter.framing = None
    folder = runtime.host.store.upload_dir
    frames = []
    for t, png in zip(times, pngs):
        name = uuid.uuid4().hex[:8] + '.png'
        (folder / name).write_bytes(png)
        frames.append({'t_ms': t, 'url': '/uploads/' + name})
    sheet = None
    if len(pngs) > 1:
        name = uuid.uuid4().hex[:8] + '.png'
        (folder / name).write_bytes(contact_sheet(pngs, times))
        sheet = '/uploads/' + name
    return {'scene': ref, 'frames': frames, 'sheet_url': sheet, 'width': WIDTH, 'height': HEIGHT, 'checks': []}


# ---- routes -----------------------------------------------------------------------------------

def install(host, runtime):
    exporter = runtime.motion = Exporter(runtime)
    host.app.router.on_shutdown.append(exporter.close)
    nosniff = {'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'}
    changed = 'Project changed; reload the Motion page'

    def wrong_project(value):
        return value is not None and value != runtime.identity

    @host.app.get('/api/runtime/motion/scenes')
    async def scenes(request: Request):
        if wrong_project(request.query_params.get('project')):
            return JSONResponse({'error': changed}, 409)
        found, invalid = catalogue(runtime)
        if project_dir(runtime) is None:
            export = {'available': False, 'reason': PROJECT_REQUIRED, 'license': None}
        else:
            try:
                encoder = await asyncio.to_thread(require_tools, runtime)  # cached after the first check
                export = {'available': True, 'reason': None, 'license': encoder['license']}
            except Unavailable as exc:
                export = {'available': False, 'reason': str(exc), 'license': None}
        return {'project': runtime.identity, 'scenes': [summary(ref, s, scene) for ref, (s, scene) in found.items()],
                'invalid': invalid,
                'export': {**export, 'format': 'webm', 'codec': 'vp9', 'mime': 'video/webm',
                           'width': WIDTH, 'height': HEIGHT, 'fps': FPS}}

    @host.app.get('/api/runtime/motion/view')
    async def view(request: Request):
        q = request.query_params
        if q.get('project') != runtime.identity:
            return JSONResponse({'error': changed}, 409)
        found, _ = catalogue(runtime)
        entry = found.get(q.get('scene', ''))
        if not entry:
            return JSONResponse({'error': 'Unknown or invalid scene'}, 404)
        try:
            scene = drawable(entry[1])
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, 409)
        try:
            t = max(0, min(scene['duration_ms'], int(q.get('t', '0'))))
        except ValueError:
            t = 0
        options = {'render': False, 'reduced': q.get('reduced') == '1', 'embed': True, 't': t}
        return HTMLResponse(view_html(scene, options), headers=nosniff)

    @host.app.get('/api/runtime/motion/exports')
    async def exports(request: Request):
        if wrong_project(request.query_params.get('project')):
            return JSONResponse({'error': changed}, 409)
        current = exporter.current if exporter.current and exporter.current.get('project') == runtime.identity else None
        return {'current': public(current), 'exports': exporter.finished()}

    @host.app.post('/api/runtime/motion/exports')
    async def start(request: Request):
        try:
            body = await request.json()
            if not isinstance(body, dict) or not isinstance(body.get('scene'), str):
                raise ValueError('Choose a scene to export')
            if body.get('project') != runtime.identity:
                return JSONResponse({'error': changed}, 409)
            return JSONResponse(exporter.start(body['scene'], bool(body.get('reduced'))), 202)
        except RuntimeError as exc:
            return JSONResponse({'error': str(exc)}, 409)
        except Unavailable as exc:
            return JSONResponse({'error': str(exc)}, 503)
        except (ValueError, json.JSONDecodeError) as exc:
            return JSONResponse({'error': str(exc)}, 400)

    @host.app.post('/api/runtime/motion/exports/{job_id}/cancel')
    async def cancel(job_id: str, request: Request):
        try:
            body = await request.json()
        except json.JSONDecodeError:
            body = {}
        if not isinstance(body, dict) or body.get('project') != runtime.identity:
            return JSONResponse({'error': changed}, 409)
        job = exporter.cancel(job_id)
        return job if job else JSONResponse({'error': 'No running export with that id'}, 404)

    @host.app.get('/api/runtime/motion/exports/{job_id}/video')
    async def video(job_id: str, request: Request):
        path = exporter.video(job_id)
        if not path:
            return Response(status_code=404)
        disposition = 'attachment' if request.query_params.get('download') else 'inline'
        return FileResponse(path, media_type='video/webm', headers=nosniff,
                            content_disposition_type=disposition, filename=f'{job_id}.webm')


if __name__ == '__main__':
    # Agent and terminal entry point, same engine as the app:
    #   python motion_studio.py <scene.json> <out.webm> [--reduced]
    import argparse
    import sys
    ap = argparse.ArgumentParser(description='Render a motion scene to WebM (same engine as the app).')
    ap.add_argument('scene', type=Path)
    ap.add_argument('out', type=Path)
    ap.add_argument('--reduced', action='store_true')
    args = ap.parse_args()

    async def cli():
        scene = drawable(load(args.scene))
        ffmpeg = require_tools(None)['path']
        job = {'frames': scene['duration_ms'] * FPS // 1000, 'phase': 'starting', 'frame': 0}
        work = Path(tempfile.mkdtemp(prefix='zerocollab-motion-'))
        try:
            started = time.perf_counter()
            hashes = await export_video(scene, args.reduced, args.out, work, job, ffmpeg)
        finally:
            await remove_tree(work)
        print(json.dumps({'out': str(args.out), 'frames': len(hashes), 'seconds': round(time.perf_counter() - started, 2),
                          'frames_digest': hashlib.sha256(''.join(hashes).encode()).hexdigest()}, indent=1))

    try:
        asyncio.run(cli())
    except (Unavailable, ValueError, RuntimeError) as exc:
        sys.exit(str(exc))
