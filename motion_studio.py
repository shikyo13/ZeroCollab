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
from motion_jobs import Cancelled, validate_result
from motion_studio_jobs import submit_owned

ROOT = Path(__file__).resolve().parent
# VP9 in WebM: about 5 s to encode 20 s of 1080p30 on the reference machine, far below capture time.
VP9 = ['-c:v', 'libvpx-vp9', '-pix_fmt', 'yuv420p', '-b:v', '0', '-crf', '30', '-deadline', 'good',
       '-cpu-used', '4', '-row-mt', '1', '-tile-columns', '2', '-an', '-f', 'webm']
KIT = ROOT / 'motion'
BUNDLED = KIT / 'scenes'
# core.js is the shared motion-core library (S1, frontend 1f9560e4); it is inlined first when it ships.
SCRIPTS = ('core.js', 'motion.js', 'boot.js')
V2_SCRIPTS = ('core.js', 'motion.js', 'render2.js')
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
    """Preserve exact migrated v1 rendering; other v2 scenes use the S5 page."""
    import motion_scene
    v1 = motion_scene.legacy(scene)
    if v1 is None:
        if not all((KIT / name).is_file() for name in V2_SCRIPTS):
            raise ValueError('This scene needs the v2 renderer, which is not installed yet')
        return scene
    return v1


def render_plan(scene, composition=None):
    """One selected composition and its exact capture clock; legacy keeps its old geometry."""
    scene = drawable(scene)
    if scene['version'] == 1:
        return {'renderer': 'legacy', 'width': WIDTH, 'height': HEIGHT, 'fps': FPS,
                'duration_ms': scene['duration_ms'], 'duration': scene['duration_ms'] * FPS // 1000}
    import motion_scene
    comp = next((c for c in scene['compositions'] if composition is None or c['id'] == composition), None)
    if comp is None:
        raise ValueError('Unknown composition')
    return {'renderer': 'v2', 'composition': comp['id'], 'width': comp['width'], 'height': comp['height'],
            'fps': dict(comp['fps']), 'duration': comp['duration'],
            'duration_ms': motion_scene.frame_ms(comp['duration'], comp['fps'])}


def require_silent_composition(scene, composition, runtime=None):
    """Sound requires a host-installed worker before admission or page startup."""
    if scene['version'] == 2:
        from motion_audio import contains_sound, require_worker
        comp = next(c for c in scene['compositions'] if c['id'] == composition)
        if contains_sound(comp):
            require_worker(runtime)


MAX_INLINE_ASSET_BYTES = 64 * 1024 * 1024  # total raw bytes in one generated page


def resolved_asset_path(ref, runtime):
    """One authority for picture and audio originals; no scene-supplied path."""
    import motion_scene
    if ref.startswith('/uploads/') and motion_scene.UPLOAD.fullmatch(ref):
        root = getattr(getattr(getattr(runtime, 'host', None), 'store', None), 'upload_dir', None)
        if root is None:
            raise ValueError('Upload assets need the room upload directory')
        root = Path(root).resolve()
        path = (root / ref.removeprefix('/uploads/')).resolve()
    elif motion_scene.KIT_ASSET.fullmatch(ref):
        root = KIT.resolve()
        path = (root / ref.removeprefix('motion:')).resolve()
    elif motion_scene.FILEREF.fullmatch(ref):
        path = Path(runtime.project_room.references().lookup(ref.removeprefix('fileref:'))[0]).resolve()
        root = path.parent  # The registry authorizes this exact original; no scene-supplied path.
    else:
        raise ValueError('Asset ref must be an upload, motion kit asset or registered original')
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('Asset is missing or outside its allowed directory')
    return path


def resolved_frame_input(scene, composition, runtime):
    """Resolve uploads, kit assets and registered originals into a bounded, hash-checked data-only page."""
    import mimetypes
    import motion_scene
    frame = motion_scene.frame_input(scene, composition, 0)
    total = 0
    sources = {}
    for asset in frame['assets']:
        ref = asset['ref']
        if ref in sources:
            raw_hash, src = sources[ref]
        else:
            path = resolved_asset_path(ref, runtime)
            if total + path.stat().st_size > MAX_INLINE_ASSET_BYTES:
                raise ValueError('A motion page can inline at most 64 MiB of asset data')
            with path.open('rb') as stream:
                raw = stream.read(MAX_INLINE_ASSET_BYTES - total + 1)
            total += len(raw)
            if total > MAX_INLINE_ASSET_BYTES:
                raise ValueError('A motion page can inline at most 64 MiB of asset data')
            raw_hash = hashlib.sha256(raw).hexdigest()
            mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            src = f'data:{mime};base64,' + base64.b64encode(raw).decode()
            sources[ref] = (raw_hash, src)
        if asset['sha256'] and asset['sha256'] != raw_hash:
            raise ValueError(f'Asset {asset["id"]} SHA256 does not match its bytes')
        asset.update(sha256=raw_hash, src=src)
    return frame


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
            'version': scene['version'], 'renderable': v1 is not None or all((KIT / n).is_file() for n in V2_SCRIPTS),
            'duration_ms': duration, 'beats': len(v1['beats']) if v1 else 0}

def _block(block_id, value):
    # '<' is escaped so no text inside the data can close its block.
    data = json.dumps(value, ensure_ascii=False).replace('<', '\\u003c')
    return f'<script type="application/json" id="{block_id}">{data}</script>'


def ui_bundle(composition):
    """Load the shipped Board/task-card bundle only for the selected UI composition."""
    def layers(items):
        for layer in items:
            if layer.get('hidden'):
                continue
            if layer['type'] == 'ui':
                yield layer
            elif layer['type'] == 'group':
                yield from layers(layer.get('layers', []))

    used = [layer for track in composition['tracks'] if not track.get('hidden') and track['kind'] == 'visual'
            for clip in track['clips']
            for layer in layers(clip['layers'])]
    if not used:
        return None
    if any(layer.get('component') not in ('board', 'task-card') for layer in used):
        raise ValueError('Motion UI supports only board and task-card components')
    bundle = []
    for name, closing in (('ui.js', '</script'), ('ui.css', '</style')):
        try:
            with (KIT / name).open('rb') as stream:
                raw = stream.read(1024 * 1024 + 1)
        except OSError as exc:
            raise ValueError(f'Motion UI needs the installed {name} bundle') from exc
        if len(raw) > 1024 * 1024:
            raise ValueError(f'Motion UI {name} exceeds the 1 MiB inline limit')
        # Match read_text's newline normalization before hashing inline scripts.
        code = raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
        if closing in code.lower():
            raise ValueError(f'Motion UI {name} cannot be inlined')
        bundle.append(code)
    return bundle


def view_html(scene, options, runtime=None):
    """A hash-pinned, network-free page. Legacy keeps boot.js and exact seek-ms behavior;
    v2 uses core/motion/render2 and a resolved FrameInput for the selected (default first) composition.
    Options include render/reduced/embed/t, optional composition/props and transparent export background."""
    scene = drawable(scene)
    options = dict(options)
    if scene['version'] == 1:
        options.pop('transparent', None)
    names = SCRIPTS
    ui = None
    media_csp = ''
    if scene['version'] == 2:
        plan = render_plan(scene, options.get('composition'))
        options['frame_input'] = resolved_frame_input(scene, plan['composition'], runtime)
        names = V2_SCRIPTS
        comp = next(c for c in scene['compositions'] if c['id'] == plan['composition'])
        ui = ui_bundle(comp)
        if any(a['kind'] == 'video' for a in options['frame_input']['assets']):
            media_csp = 'media-src data:; '
    scripts = [(KIT / name).read_text('utf-8') for name in names
               if name not in OPTIONAL_SCRIPTS or (KIT / name).is_file()]
    if ui is not None:
        scripts.insert(-1, ui[0])  # core, motion, UI, render2; included in the CSP hashes below
    if any('</script' in code.lower() for code in scripts):
        raise RuntimeError('motion scripts cannot be inlined')
    hashes = ' '.join("'sha256-" + base64.b64encode(hashlib.sha256(code.encode()).digest()).decode() + "'"
                      for code in scripts)
    csp = (f"default-src 'none'; script-src {hashes}; style-src 'unsafe-inline'; img-src data:; "
           f"{media_csp}base-uri 'none'; form-action 'none'")
    page = (KIT / 'scene.html').read_text('utf-8')
    if ui is not None:
        page = page.replace('</head>', f'<style>{ui[1]}</style></head>', 1)
    if scene['version'] == 2 and options.get('transparent'):
        # Preserve an explicitly authored background. Otherwise alpha profiles
        # must not acquire the legacy page's opaque canvas/stage defaults.
        comp = next(c for c in scene['compositions'] if c['id'] == options['frame_input']['composition']['id'])
        style = 'html,body.render {background:transparent}'
        if not comp.get('background'):
            style += '#stage {background:transparent}'
        page = page.replace('</head>', f'<style>{style}</style></head>', 1)
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
    inventories = {category: run('-' + category) for category in
                   ('encoders', 'decoders', 'demuxers', 'muxers', 'filters')}
    lists = {'encoder libvpx-vp9': inventories['encoders'], 'decoder png': inventories['decoders'],
             'demuxer image2pipe': inventories['demuxers'], 'muxer webm': inventories['muxers']}
    missing = [need for need, listing in lists.items()
               if not any(line.split()[1:2] == [need.split()[1]] for line in listing.splitlines() if line.split())]
    config = next((line for line in version.splitlines() if line.startswith('configuration:')), '')
    license_ = 'nonfree' if '--enable-nonfree' in config else 'GPL' if '--enable-gpl' in config else 'LGPL'
    result = {'path': path, 'version': (version.splitlines() or [''])[0][:120], 'license': license_, 'missing': missing}
    from motion_encode import inventory, probe_profiles
    result['formats'] = probe_profiles(path, {name: inventory(text) for name, text in inventories.items()})
    _encoder_cache[stamp] = result
    return result


def require_tools(runtime, format='webm'):
    """The encoder's capability record, or Unavailable naming what is missing."""
    check = encoder_check(ffmpeg_path(runtime))
    from motion_encode import profile
    profile(format)
    if format == 'webm' and check['missing']:
        raise Unavailable('The installed FFmpeg cannot export WebM video; it lacks ' + ', '.join(check['missing']))
    capability = check['formats'][format]
    if not capability['available']:
        raise Unavailable(capability.get('reason') or f'{format} is not available in this encoder')
    if not shared_browser.executable():
        raise Unavailable('Video export needs Microsoft Edge, Google Chrome or Chromium')
    return check


class Exporter:
    """Existing export/frame owners share the host's one heavy-job lease."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.current = None
        self.task = None  # compatibility: newest export owner, never cancellation authority
        self.tasks = set()
        self.generation = 0
        self.capability = None

    @property
    def gate(self):
        return self.runtime.host.media_jobs

    def _submit(self, job, operation, *, publish_result=None):
        project = self.runtime.identity

        def check_project():
            if self.runtime.identity != project:
                raise Cancelled('The project changed during the media job')

        ticket = submit_owned(self.gate, job, operation, check_project, publish_result=publish_result)
        self.tasks.add(ticket.task)
        ticket.task.add_done_callback(self.tasks.discard)
        return ticket

    def busy(self):
        return bool(self.current and self.current['phase'] in ('starting', 'rendering', 'encoding', 'publishing'))

    def finished(self):
        folder = project_dir(self.runtime)
        if not folder or not (folder / 'exports').is_dir():
            return []
        jobs = []
        for meta in (folder / 'exports').glob('*.json'):
            try:
                job = json.loads(meta.read_text('utf-8'))
                from motion_encode import profile
                extension = profile(job.get('profile', 'webm')).extension
                if meta.with_suffix('.' + extension).is_file():
                    jobs.append(job)
            except (OSError, ValueError):
                continue
        return sorted(jobs, key=lambda j: j.get('finished', 0), reverse=True)

    def video(self, job_id):
        if not re.fullmatch(r'[0-9a-f]{12}', job_id or ''):
            return None
        folder = project_dir(self.runtime)
        if folder is None:
            return None
        from motion_encode import profile
        try:
            meta = json.loads((folder / 'exports' / f'{job_id}.json').read_text('utf-8'))
            extension = profile(meta.get('profile', 'webm')).extension
        except (OSError, ValueError):
            return None
        path = folder / 'exports' / f'{job_id}.{extension}'
        return path if path.is_file() else None

    async def prepare_start(self, ref, reduced, format='webm', *, owner='user'):
        """Return a queued export immediately; cold probes run after admission."""
        return self.start(ref, reduced, format, owner=owner)

    def start(self, ref, reduced, format='webm', *, encoder=None, owner='user'):
        base = project_dir(self.runtime)
        if not base:
            raise ValueError(PROJECT_REQUIRED)
        scenes, _ = catalogue(self.runtime)
        if ref not in scenes:
            raise ValueError('Unknown or invalid scene')
        scene = json.loads(json.dumps(drawable(scenes[ref][1])))
        plan = render_plan(scene)
        require_silent_composition(scene, plan.get('composition'), self.runtime)
        from motion_encode import profile
        selected = profile(format)
        self.generation += 1
        job = {'id': uuid.uuid4().hex[:12], 'scene': ref, 'scene_id': scene['id'],
                        'kind': 'export', 'owner': owner,
                        'generation': self.generation,
                        'name': scene.get('name') or scene['id'], 'reduced': bool(reduced), 'phase': 'starting',
                        'frame': 0, 'frames': 1 if format == 'png' else plan['duration'],
                        'width': plan['width'], 'height': plan['height'], 'renderer': plan['renderer'],
                        'composition': plan.get('composition'),
                        'fps': plan['fps'], 'profile': format, 'format': selected.extension,
                        'transparent': selected.alpha and plan['renderer'] == 'v2',
                        'mime': selected.mime, 'codec': 'vp9' if format == 'webm' else selected.encoder,
                        'created': time.time(),
                        'project': self.runtime.identity}
        async def operation(ticket, check):
            from motion_render import file_io
            self.current = job
            # Even the old synchronous start() fallback is inside the lease.
            checked = await file_io(require_tools, self.runtime, format) if encoder is None else encoder
            check()
            capability = checked['formats'][format]
            if not capability['available']:
                raise Unavailable(capability.get('reason') or f'{format} is not available in this encoder')
            job['encoder'] = {k: checked[k] for k in ('version', 'license')}
            return await self.run(job, scene, base / 'exports', checked['path'], check)
        ticket = self._submit(job, operation)
        self.task = ticket.task
        if ticket.lease is not None:
            self.current = job
        return self.gate.status(ticket.key[0], project=ticket.key[2]) | public(job)

    def cancel(self, job_id, *, actor='user', is_user=False, generation=None):
        ticket = self.gate.entries.get(job_id)
        if not ticket or ticket.job['kind'] != 'export' or ticket.key[2] != self.runtime.identity:
            return None
        return self.gate.cancel(job_id, ticket.key[1] if generation is None else generation,
                                project=self.runtime.identity, actor=actor, is_user=is_user)

    async def capabilities(self, *, owner='user'):
        """Cold scenes-page probes are deduplicated owned jobs, not queue bypasses."""
        try:
            path = ffmpeg_path(self.runtime)
            stamp = (path, Path(path).stat().st_mtime_ns)
            if stamp in _encoder_cache:
                checked = require_tools(self.runtime)
                return {'available': True, 'reason': None, 'license': checked['license']}
        except (Unavailable, OSError) as exc:
            return {'available': False, 'reason': str(exc), 'license': None}
        key = (self.runtime.identity, stamp)
        if self.capability is None or self.capability[0] != key or self.capability[1].terminal_at is not None:
            job = {'kind': 'capability', 'owner': owner, 'project': self.runtime.identity}
            async def operation(ticket, check):
                from motion_render import file_io
                checked = await file_io(require_tools, self.runtime)
                check()
                return {'available': True, 'reason': None, 'license': checked['license']}
            self.capability = (key, self._submit(job, operation))
        ticket = self.capability[1]
        answer = await self.gate.reply(ticket)
        if answer['phase'] == 'done':
            return answer['result']
        if answer['phase'] in ('failed', 'cancelled', 'unavailable'):
            return {'available': False, 'reason': answer.get('error') or answer['phase'], 'license': None}
        return {'available': None, 'pending': True, 'reason': 'Preparing encoder capabilities',
                'license': None, 'job': answer}

    def submit_frame(self, ref, scene, times, reduced, *, owner, run=None):
        """Backend may call this and use gate.reply/status; never wrap in a second lease."""
        scene = json.loads(json.dumps(drawable(scene)))
        plan = render_plan(scene)
        times = list(times)
        if not 1 <= len(times) <= MAX_FRAMES or any(isinstance(t, bool) or not isinstance(t, (int, float))
                or not 0 <= t <= plan['duration_ms'] for t in times):
            raise ValueError(f't_ms must contain 1 to {MAX_FRAMES} times within the composition')
        times = [round(t, 4) for t in times]
        job = {'kind': 'frame', 'owner': owner, 'project': self.runtime.identity,
               'scene': ref, 'frames': len(times), 'frame': 0}
        upload_dir = self.runtime.host.store.upload_dir
        async def operation(ticket, check):
            from motion_render import file_io
            from motion_audio_process import _join
            created = []
            complete = False
            try:
                check()
                if not shared_browser.executable():
                    raise Unavailable('Preview frames need Microsoft Edge, Google Chrome or Chromium')
                pngs = await render_frames(self.runtime, scene, times, reduced, check_cancel=check, job=job)
                sheet = await file_io(contact_sheet, pngs, times) if len(pngs) > 1 else None
                payloads = list(pngs) + ([sheet] if sheet is not None else [])
                paths = [upload_dir / (uuid.uuid4().hex[:8] + '.png') for _ in payloads]
                answer = validate_result({'scene': ref,
                    'frames': [{'t_ms': t, 'url': '/uploads/' + path.name} for t, path in zip(times, paths)],
                    'sheet_url': '/uploads/' + paths[-1].name if sheet is not None else None,
                    'width': plan['width'], 'height': plan['height'], 'checks': []})
                check()
                for path, png in zip(paths, payloads):
                    created.append(path)
                    await file_io(path.write_bytes, png)
                    check()
                complete = True
                return answer
            finally:
                if not complete:
                    async def cleanup():
                        for path in created:
                            await file_io(lambda p=path: p.unlink(missing_ok=True))
                    await _join(asyncio.create_task(cleanup()), propagate=True)
        async def show(answer):
            if run is not None:
                await self.runtime.live.publish(self.runtime.live.artifact(
                    run, answer.get('sheet_url') or answer['frames'][0]['url'], 'upload'))
        return self._submit(job, operation, publish_result=show)

    async def close(self):
        """Room shutdown: stop a running export so its browser and ffmpeg end with the room."""
        from motion_audio_process import _join
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await _join(asyncio.ensure_future(asyncio.gather(*tasks, return_exceptions=True)), propagate=True)

    async def run(self, job, scene, folder, ffmpeg, check):
        from motion_render import file_io
        from motion_audio_process import _join
        extension = job.get('format', 'webm')
        partial = folder / f'{job["id"]}.part.{extension}'
        final = folder / f'{job["id"]}.{extension}'
        metadata = folder / f'{job["id"]}.json'
        work = None
        committed = False
        try:
            check()
            work = Path(tempfile.mkdtemp(prefix='zerocollab-motion-'))
            folder.mkdir(parents=True, exist_ok=True)
            started = time.perf_counter()
            hashes = await export_video(scene, job['reduced'], partial, work, job, ffmpeg,
                                        check_cancel=check, runtime=self.runtime)
            check()
            job['render_s'] = round(time.perf_counter() - started, 2)
            from motion_render import FrameClock
            fps = job.get('fps', FPS)
            clock = FrameClock(fps['num'], fps['den']) if isinstance(fps, dict) else FrameClock(fps)
            result = {**public(job), 'phase': 'done', 'finished': time.time(), 'bytes': partial.stat().st_size,
                      'seconds': float(clock.ms(job['frames']) / 1000),
                      'frames_digest': hashlib.sha256(''.join(hashes).encode()).hexdigest(),
                      'video_url': f'/api/runtime/motion/exports/{job["id"]}/video'}
            result = validate_result(result)
            check()
            await file_io(os.replace, partial, final)
            check()
            await file_io(metadata.write_text, json.dumps(result, indent=1), 'utf-8')
            check()
            job.update({k: v for k, v in result.items() if k not in ('phase', 'finished')})
            committed = True
            return result
        except BaseException:
            committed = False
            raise
        finally:
            parent = asyncio.current_task()
            async def cleanup():
                if work is not None:
                    await remove_tree(work)
                keep = committed and not parent.cancelling()
                try:
                    check()
                except Cancelled:
                    keep = False
                for path in (partial,) if keep else (partial, final, metadata):
                    await file_io(lambda p=path: p.unlink(missing_ok=True))
            await _join(asyncio.create_task(cleanup()), propagate=True)


async def remove_tree(path, attempts=50):
    for _ in range(attempts):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        await asyncio.sleep(.1)


def public(job):
    return {k: v for k, v in job.items() if k != 'cancel'} if job else None


async def export_video(scene, reduced, out, work, job, ffmpeg, *, check_cancel=None, runtime=None):
    """Prepare sound once, then capture exact picture frames with generated captions."""
    from contextlib import AsyncExitStack
    from motion_render import file_io

    check = check_cancel or (lambda: None)
    require_silent_composition(scene, job.get('composition') or render_plan(scene).get('composition'), runtime)
    page = work / 'scene.html'
    options = {'render': True, 'reduced': bool(reduced), 'transparent': job.get('transparent', False),
               **({'composition': job['composition']} if job.get('composition') else {})}
    await file_io(lambda: page.write_text(view_html(scene, options, runtime), 'utf-8'))
    check()
    async with AsyncExitStack() as prepared:
        audio = None
        if job.get('renderer') == 'v2':
            from motion_audio import contains_sound, page_plan, prepare, voice_cache
            comp = next(c for c in scene['compositions'] if c['id'] == job['composition'])
            plan, assets = await page_plan(page, job, work, check) if contains_sound(comp) else ({'sounds': []}, {})
            if plan['sounds']:
                job['audio_phase'] = 'preparing'
                cache = await voice_cache(runtime)
                audio = await prepared.enter_async_context(prepare(
                    plan, assets, job['fps'], job['frames'], ffmpeg, work, cache,
                    lambda ref: resolved_asset_path(ref, runtime),
                    profile_name=job.get('profile', 'webm'), check_cancel=check))
                options['voice_captions'] = audio.captions
                job['audio'] = audio.receipt
                job['audio_phase'] = 'ready'
                await file_io(lambda: page.write_text(view_html(scene, options, runtime), 'utf-8'))
        return await encode_video_page(page, out, work, job, ffmpeg,
                                       check_cancel=check, audio=audio.wav if audio else None)


async def encode_video_page(page, out, work, job, ffmpeg, *, check_cancel, audio=None):
    """Existing PNG ordered sink, with an optional exact-duration mixed WAV input."""
    from motion_encode import output_args, profile
    from motion_render import FrameClock, file_io
    from motion_audio_process import _join, STOP_SECONDS
    import subprocess
    if check_cancel:
        check_cancel()
    selected_name = job.get('profile', 'webm')
    selected = profile(selected_name)
    fps = job.get('fps', FPS)
    clock = FrameClock(fps['num'], fps['den']) if isinstance(fps, dict) else FrameClock(fps)
    destination = out
    if selected.sequence:
        (work / 'frames').mkdir()
        destination = work / 'frames' / '%06d.png'
    flags = output_args(selected_name, audio=True) if audio else (VP9 if selected_name == 'webm' else output_args(selected_name))
    spawning = asyncio.create_task(asyncio.create_subprocess_exec(
        ffmpeg, '-y', '-loglevel', 'error', '-f', 'image2pipe',
        '-framerate', f'{clock.num}/{clock.den}', '-c:v', 'png',
        '-i', '-', *(['-i', str(audio), '-map', '0:v:0', '-map', '1:a:0'] if audio else []),
        *flags, str(destination),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == 'nt' else 0))
    try:
        encoder = await asyncio.shield(spawning)
    except BaseException:
        encoder = await _join(spawning)
        encoder.kill()
        await _join(asyncio.create_task(encoder.wait()))
        raise
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
        await render(page, job, sink, work / 'profile', check_cancel=check_cancel)
        job['phase'] = 'encoding'
        encoder.stdin.close()
        while encoder.returncode is None:
            if check_cancel:
                check_cancel()
            if job.get('cancel'):
                raise Cancelled()
            try:
                await asyncio.wait_for(encoder.wait(), .25)
            except asyncio.TimeoutError:
                pass
        if encoder.returncode != 0:
            raise await failure()
        if selected.sequence:
            def archive():
                import zipfile
                with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_STORED) as packed:
                    file_hashes = []
                    for frame in range(job['frames']):
                        if job.get('cancel'):
                            raise Cancelled()
                        if check_cancel:
                            check_cancel()
                        image = work / 'frames' / f'{frame:06d}.png'
                        with image.open('rb') as stream:
                            file_hashes.append(hashlib.file_digest(stream, 'sha256').hexdigest())
                        packed.write(image, image.name)
                    packed.writestr('frames.json', json.dumps({'fps': {'num': clock.num, 'den': clock.den},
                        'frames': job['frames'], 'sha256': file_hashes,
                        'render_sha256': hashes}, separators=(',', ':')))
            await file_io(archive)
        return hashes
    finally:
        async def close_encoder():
            try:
                if encoder.returncode is None:
                    encoder.kill()
                await asyncio.wait_for(encoder.wait(), STOP_SECONDS)
            finally:
                errors.cancel()
                await asyncio.gather(errors, return_exceptions=True)
        await _join(asyncio.create_task(close_encoder()), propagate=True)


async def render(page, job, sink, profile, *, check_cancel=None):
    """Shared owned worker for sequential MCP still times and bounded ordered video frames.
    Signature and PNG-only sink remain compatible with agent preview/contact sheets.
    """
    from motion_render import ChromiumWorker, FrameClock, RenderIdentity, render_frames as capture_frames

    def check():
        if job.get('cancel'):
            raise Cancelled()
        if check_cancel:
            check_cancel()

    fps = job.get('fps', FPS)
    clock = FrameClock(fps['num'], fps['den']) if isinstance(fps, dict) else FrameClock(fps)
    digest = hashlib.sha256(page.read_bytes()).hexdigest()
    # Cache is not enabled until resolved asset/browser identities are pinned by
    # the app integration. This page hash covers the inlined v1 scene and scripts.
    identity = RenderIdentity(job.get('scene_id', 'preview'), digest,
                              hashlib.sha256(b'').hexdigest(), digest,
                              job.get('width', WIDTH), job.get('height', HEIGHT), clock)
    check()
    job['phase'] = 'rendering'
    if job.get('times'):
        async with ChromiumWorker(page, profile, identity, check,
                                  legacy_ms=job.get('renderer') != 'v2',
                                  frame_seek=job.get('renderer') == 'v2',
                                  transparent=job.get('transparent', False)) as worker:
            for i, ms in enumerate(job['times']):
                check()
                await sink(await worker.capture_ms(ms))
                job['frame'] = i + 1
        return

    async def ordered_sink(frame, png, receipt):
        await sink(png)
        job['frame'] = frame + 1

    def factory(slot):
        return ChromiumWorker(page, profile.parent / f'{profile.name}-{slot}',
                              identity, check, legacy_ms=job.get('renderer') != 'v2',
                              frame_seek=job.get('renderer') == 'v2',
                              transparent=job.get('transparent', False))

    await capture_frames(identity, range(job['frames']), factory, ordered_sink, check,
                         # v1's animated SVG stroke rasterization depends on seek
                         # history at two sampled frames. Parallelism is opt-in
                         # until the deterministic S1/S5 frame renderer is wired.
                         concurrency=job.get('workers', 1))


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


async def render_frames(runtime, scene, times, reduced, *, check_cancel=None, job=None):
    """PNG bytes of the scene at each time (ms), through the same page and __seek(ms) as export, so a
    preview frame is pixel-identical to that frame of the video."""
    from motion_audio_process import _join
    def check():
        if check_cancel:
            check_cancel()
        if runtime.identity != project:
            raise Cancelled()
    project = runtime.identity
    check()
    work = Path(tempfile.mkdtemp(prefix='zerocollab-motion-frame-'))
    plan = render_plan(scene)
    job = job if job is not None else {}
    job.update({**plan, 'times': times, 'frames': len(times), 'frame': 0, 'phase': 'starting'})
    pngs = []

    async def sink(png):
        pngs.append(png)
    try:
        page = work / 'scene.html'
        from motion_render import file_io
        await file_io(lambda: page.write_text(view_html(scene, {'render': True, 'reduced': bool(reduced)}, runtime), 'utf-8'))
        check()
        await asyncio.wait_for(render(page, job, sink, work / 'profile', check_cancel=check), FRAME_SECONDS)
    except asyncio.TimeoutError:
        raise RuntimeError(f'The preview frames did not render within {FRAME_SECONDS} seconds') from None
    finally:
        await _join(asyncio.create_task(remove_tree(work)), propagate=True)
    check()
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
        return {'current': public(current), 'exports': exporter.finished(),
                'media_jobs': exporter.gate.snapshot(runtime.identity)}
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
        job = exporter.cancel(str(arguments.get('job_id', '')), actor=run.name,
                              generation=arguments.get('generation'))
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
        job = await exporter.prepare_start(ref, bool(arguments.get('reduced')), arguments.get('profile', 'webm'),
                                           owner=run.name)
        await runtime.activity_note(run, 'motion', f'Started a video export of {ref}')
        return job
    if action != 'frame':
        raise ValueError('action must be list, get, validate, save, frame, export, export_status or export_cancel')
    times = arguments.get('t_ms', 0)
    times = times if isinstance(times, list) else [times]
    ticket = exporter.submit_frame(ref, scene, times, arguments.get('reduced'),
                                   owner=run.name, run=run)
    from motion_agent import job_answer
    return job_answer(await exporter.gate.reply(ticket))


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
        project = runtime.identity
        found, invalid = catalogue(runtime)
        if project_dir(runtime) is None:
            export = {'available': False, 'reason': PROJECT_REQUIRED, 'license': None}
        else:
            export = await exporter.capabilities()
        if runtime.identity != project:
            return JSONResponse({'error': changed}, 409)
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
            t = max(0, min(render_plan(scene)['duration_ms'], int(q.get('t', '0'))))
        except ValueError:
            t = 0
        options = {'render': False, 'reduced': q.get('reduced') == '1', 'embed': True, 't': t}
        project = runtime.identity
        try:
            page = await asyncio.to_thread(view_html, scene, options, runtime)
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, 400)
        if runtime.identity != project:
            return JSONResponse({'error': changed}, 409)
        return HTMLResponse(page, headers=nosniff)

    @host.app.get('/api/runtime/motion/exports')
    async def exports(request: Request):
        if wrong_project(request.query_params.get('project')):
            return JSONResponse({'error': changed}, 409)
        current = exporter.current if exporter.current and exporter.current.get('project') == runtime.identity else None
        return {'current': public(current), 'exports': exporter.finished(),
                'media_jobs': exporter.gate.snapshot(runtime.identity)}

    @host.app.post('/api/runtime/motion/exports')
    async def start(request: Request):
        try:
            body = await request.json()
            if not isinstance(body, dict) or not isinstance(body.get('scene'), str):
                raise ValueError('Choose a scene to export')
            if body.get('project') != runtime.identity:
                return JSONResponse({'error': changed}, 409)
            job = await exporter.prepare_start(body['scene'], bool(body.get('reduced')))
            return JSONResponse(job, 202)
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
        job = exporter.cancel(job_id, is_user=True)
        return job if job else JSONResponse({'error': 'No running export with that id'}, 404)

    @host.app.get('/api/runtime/motion/exports/{job_id}/video')
    async def video(job_id: str, request: Request):
        path = exporter.video(job_id)
        if not path:
            return Response(status_code=404)
        from motion_encode import PROFILES
        mime = next(p.mime for p in PROFILES.values() if '.' + p.extension == path.suffix)
        disposition = 'attachment' if request.query_params.get('download') else 'inline'
        return FileResponse(path, media_type=mime, headers=nosniff,
                            content_disposition_type=disposition, filename=path.name)


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
        plan = render_plan(scene)
        job = {**plan, 'frames': plan['duration'], 'phase': 'starting', 'frame': 0}
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
