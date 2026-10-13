"""room_motion actions on the agent side (tasks 78f77506, 31dcc7da). motion_studio.py holds the renderer and
exporter (systems, S3); this module never edits it, only calls its catalogue, validation and save.

publish: a finished export becomes a room upload, posted to the agent's chat and optionally attached to a task,
where it plays inline (Zero #14291, PM decision deb4f237: videos arrive in chat and on tasks, not on a page).
variant, patch, import_asset (Zero #14804, Remotion parity): re-render a template with another composition and
props, edit a large saved scene in place, and turn a room upload into a hashed scene asset.
inspect (Zero #14935/#14956, QA's video_analysis 53e691c7): measure a video and draw sheets of it, so an agent
judges its export against a reference from numbers and dense frames instead of a few stills.
"""
import asyncio
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from team_builder import text

MAX_PATCH_OPS = 200
# A browser recording's take (desktop d590dce6): clip.webm with this sidecar beside it.
TAKE_SIDECAR = 'sidecar.json'
TAKE_SIDECAR_VERSION = 1
MAX_SIDECAR_BYTES = 16 * 1024 * 1024  # 20000 input events stay well under this
CURSOR_BUTTON_EVENTS = ('down', 'up', 'click')  # the events that carry a button
CURSOR_SUFFIX = '-cursor'  # the cursor layer id: the video layer's id plus this
TAKE_PROBE_REF = '/uploads/take.webm'  # a well-formed stand-in ref for take_issues; never resolved
EXTENSION_KINDS = {'png': 'image', 'jpg': 'image', 'jpeg': 'image', 'webp': 'image', 'gif': 'image',
                   'webm': 'video', 'mp4': 'video', 'woff2': 'font', 'woff': 'font', 'ttf': 'font', 'otf': 'font',
                   'ogg': 'audio', 'mp3': 'audio', 'wav': 'audio', 'm4a': 'audio'}

MAX_VIDEO_BYTES = 1024 * 1024 * 1024  # an exported scene of up to 10 minutes stays far below this
# A v2 scene renders when the room has the S5 renderer files (motion_studio.summary, V2_SCRIPTS); a copy of
# the room without them still says so up front instead of only at frame/export (demo-marketing #14460).
NOT_RENDERABLE = ('Valid, but this copy of the room is missing the v2 renderer files, so frame and export will refuse '
                  'this scene. It saves and keeps, and renders once they are installed.')
VOICE_CAPTIONS = ('Voiceover captions are drawn from the word timings the voice gives when the narration is '
                  'synthesized. A voice that gives none (timing_quality "none") draws no captions, so check a frame '
                  'with captions after synthesis, or add a captions layer with your own cues.')


def layers(node):
    """Every layer under node (compositions, tracks, clips, groups), depth first."""
    if isinstance(node, dict):
        if 'type' in node:
            yield node
        for key in ('tracks', 'clips', 'layers'):
            yield from layers(node.get(key))
    elif isinstance(node, list):
        for item in node:
            yield from layers(item)


def has_layer(node, kind):
    return any(layer.get('type') == kind for layer in layers(node))


def render_note(result, scene=None):
    """validate and save: say up front what export will refuse for a valid scene, so the agent fixes it before
    spending a render (harness 0216d3b8): a missing v2 renderer, and sound, which export can't mix yet."""
    import motion_studio
    summary = result.get('summary') if isinstance(result, dict) else None
    if not isinstance(summary, dict):
        return result
    warnings = list(result.get('warnings', []))
    if summary.get('renderable') is False:
        warnings.append(NOT_RENDERABLE)
    if isinstance(scene, dict) and scene.get('version') == 2:
        if has_layer(scene.get('compositions'), 'ui') and not (motion_studio.KIT / 'ui.js').is_file():
            warnings.append('This scene has ui layers (real product components on scripted data). They validate '
                            'and save, but this copy of the room has no UI bundle (motion/ui.js) yet, so they '
                            'won\'t render until it is installed.')
        if any(layer.get('type') == 'voiceover' and isinstance(layer.get('captions'), dict)
               and layer['captions'].get('enabled') is True for layer in layers(scene.get('compositions'))):
            # systems c1f63907: render2 draws voiceover captions only from the synthesized voice's word timings.
            warnings.append(VOICE_CAPTIONS)
        for comp in scene.get('compositions', []):
            try:
                # The exporter's own rule (systems 57cf037 covers voiceover), so the warning matches the refusal
                # and stops by itself once the audio mix ships.
                motion_studio.require_silent_composition(scene, comp['id'])
            except ValueError:
                warnings.append(f"Composition {comp['id']} has sound (an audio or voiceover layer, or a video layer "
                                "that isn't muted). Export refuses it until audio mixing ships: set \"muted\": true "
                                'on video layers and remove audio and voiceover layers before export. frame previews '
                                'work as they are.')
    if warnings:
        result['warnings'] = warnings
    return result


def share(source, target):
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


async def publish(runtime, run, arguments):
    exporter = runtime.motion
    job_id = str(arguments.get('job_id', ''))
    path = exporter.video(job_id)
    if not path:
        raise ValueError('No finished export with that job_id in this project; check export_status')
    job = next((j for j in exporter.finished() if j.get('id') == job_id), {})
    # Only WebM plays inline in chat and on tasks (systems b1883cae). video() found the file from the job's
    # profile, so its suffix is the container; exports from before profiles are WebM.
    if path.suffix.lower() != '.webm':
        raise ValueError(f"This {job.get('profile') or path.suffix.lstrip('.')} export downloads from its link; "
                         f"publish posts WebM videos. Share /api/runtime/motion/exports/{job_id}/video?download=1")
    if path.stat().st_size > MAX_VIDEO_BYTES:
        raise ValueError('The exported video is larger than 1 GB')
    title = text(arguments.get('name') or f"{job.get('name') or job_id}.webm", 'Video name', 120)
    note = text(arguments.get('note') or '', 'Video note', 2000, required=False)
    room = runtime.project_room
    task_id = arguments.get('task_id')
    task = next((t for t in room.state['tasks'] if t['id'] == task_id), None) if task_id else None
    if task_id and not task:
        raise ValueError('Task was not found')  # before any file is written
    name = uuid.uuid4().hex[:8] + path.suffix.lower()
    target = runtime.host.store.upload_dir / name
    # A hard link when the export and uploads share a volume (no second copy of a large file), else a copy;
    # either way the upload outlives the export file, and the work runs off the event loop.
    await asyncio.to_thread(share, path, target)
    attachment = {'url': '/uploads/' + name, 'name': title, **({'note': note} if note else {})}
    result = {'url': attachment['url'], 'name': title, 'job_id': job_id}
    if task:
        try:
            # task() applies ownership, the card limit and history like any other attachment change.
            room.task(run.name, 'update', task_id=task_id, attachments=[*task.get('attachments', []), attachment])
        except ValueError:
            target.unlink(missing_ok=True)  # refused (for example, not the owner): leave no unreferenced copy
            raise
        result['task_id'] = task_id
    if arguments.get('post', True):
        message = runtime.host.store.add(run.name, note or f'Video: {title}', channel=run.channel,
                                         attachments=[dict(attachment)],
                                         metadata={'no_route': True, 'chain': run.entry['chain']})
        result['message_id'] = message['id']
    await runtime.activity_note(run, 'motion', f'Published video {attachment["url"]} from export {job_id}')
    return result


# ---- variant, patch, import_asset (Zero #14804) ----------------------------------------------------------------

def stored_scene(runtime, ref, *, project_only=False):
    """A catalogued scene by ref (bundled:<id> or project:<id>), as an independent copy."""
    import motion_studio
    found, _ = motion_studio.catalogue(runtime)
    if not isinstance(ref, str) or ref not in found or (project_only and not ref.startswith('project:')):
        what = 'a saved project scene (project:<id>)' if project_only else 'a scene ref from list'
        raise ValueError(f'Unknown or invalid scene; use {what}')
    return copy.deepcopy(found[ref][1])


def checked_save(runtime, scene, overwrite):
    """Validate, then save; an invalid result is returned as issues and nothing is written."""
    import motion_scene
    import motion_studio
    found = motion_scene.issues(scene)
    if found:
        return None, found
    return motion_studio.save_scene(runtime, scene, overwrite), []


async def variant(runtime, run, arguments):
    """A template re-render: one composition of a v2 scene, with props overridden and checked against its
    props_schema, saved as its own project scene so frame, export and publish use it unchanged."""
    import motion_scene
    import motion_studio
    scene = stored_scene(runtime, arguments.get('scene'))
    if scene['version'] != 2:
        raise ValueError('variant needs a v2 scene with compositions; v1 room replays have no props to vary')
    new_id = arguments.get('new_id')
    if not isinstance(new_id, str) or not motion_scene.ID.fullmatch(new_id):
        raise ValueError('new_id must be lowercase letters, digits and hyphens, at most 48 characters')
    wanted = arguments.get('composition') or scene['compositions'][0]['id']
    comp = next((c for c in scene['compositions'] if c['id'] == wanted), None)
    if comp is None:
        raise ValueError(f"No composition {wanted!r}; this scene has " +
                         ', '.join(c['id'] for c in scene['compositions']))
    overrides = arguments.get('props') or {}
    if not isinstance(overrides, dict):
        raise ValueError('props must be an object of prop names to values')
    comp['props'] = {**comp.get('props', {}), **overrides}
    scene.update(id=new_id, compositions=[comp])  # frame and export render the first composition
    if arguments.get('name'):
        scene['name'] = text(arguments['name'], 'Scene name', 80)
    ref, found = checked_save(runtime, scene, bool(arguments.get('overwrite')))
    if found:
        return {'ok': False, 'issues': found}
    await runtime.activity_note(run, 'motion', f"Saved {ref}, a variant of {arguments.get('scene')} ({wanted})")
    return render_note({'ok': True, 'ref': ref, 'composition': wanted, 'props': comp['props'],
                        'summary': motion_studio.summary(ref, 'project', scene)}, scene)


def _pointer(path, op):
    if not isinstance(path, str) or (path and not path.startswith('/')):
        raise ValueError(f'op {op}: path must be a JSON pointer such as /compositions/0/props/title')
    return [part.replace('~1', '/').replace('~0', '~') for part in path.split('/')[1:]]


def _child(node, part, op):
    if isinstance(node, dict) and part in node:
        return node[part]
    if isinstance(node, list) and part.isdigit() and int(part) < len(node):
        return node[int(part)]
    raise ValueError(f'op {op}: no {part!r} on that path')


def _parent(doc, parts, op):
    node = doc
    for part in parts[:-1]:
        node = _child(node, part, op)
    return node, parts[-1]


def _remove(doc, parts, op):
    node, key = _parent(doc, parts, op)
    if isinstance(node, dict) and key in node:
        return node.pop(key)
    if isinstance(node, list) and key.isdigit() and int(key) < len(node):
        return node.pop(int(key))
    raise ValueError(f'op {op}: nothing to remove at that path')


def _add(doc, parts, value, op):
    node, key = _parent(doc, parts, op)
    if isinstance(node, dict):
        node[key] = value
    elif isinstance(node, list) and (key == '-' or (key.isdigit() and int(key) <= len(node))):
        node.insert(len(node) if key == '-' else int(key), value)
    else:
        raise ValueError(f'op {op}: cannot add at that path')


def json_equal(a, b):
    """RFC 6902 section 4.6 equality: same JSON type, recursively. Unlike Python's ==, true is not 1 and false
    is not 0 (systems 9a0066e8 F2); numbers still compare by value, so 1 equals 1.0."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(json_equal(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b  # strings and null


def apply_patch(doc, ops):
    """RFC 6902 JSON Patch (add, remove, replace, move, copy, test) on a copy; any failing op fails them all."""
    if not isinstance(ops, list) or not 1 <= len(ops) <= MAX_PATCH_OPS:
        raise ValueError(f'ops must be a list of 1 to {MAX_PATCH_OPS} JSON Patch operations')
    doc = copy.deepcopy(doc)
    for n, op in enumerate(ops):
        if not isinstance(op, dict) or op.get('op') not in ('add', 'remove', 'replace', 'move', 'copy', 'test'):
            raise ValueError(f'op {n}: op must be add, remove, replace, move, copy or test')
        parts = _pointer(op.get('path'), n)
        if not parts:
            raise ValueError(f'op {n}: replace the whole scene with save instead')
        kind = op['op']
        if kind in ('add', 'replace', 'test') and 'value' not in op:
            raise ValueError(f'op {n}: {kind} needs a value')
        if kind == 'test':
            node, key = _parent(doc, parts, n)
            if not json_equal(_child(node, key, n), op['value']):
                raise ValueError(f"op {n}: test failed at {op['path']}")
        elif kind == 'remove':
            _remove(doc, parts, n)
        elif kind == 'replace':
            _remove(doc, parts, n)
            _add(doc, parts, copy.deepcopy(op['value']), n)
        elif kind == 'add':
            _add(doc, parts, copy.deepcopy(op['value']), n)
        else:
            source = _pointer(op.get('from'), n)
            if not source:
                raise ValueError(f'op {n}: from must point inside the scene')
            if kind == 'move':
                if op['path'].startswith(op['from'] + '/'):
                    raise ValueError(f'op {n}: cannot move a value into itself')
                value = _remove(doc, source, n)
            else:
                node, key = _parent(doc, source, n)
                value = copy.deepcopy(_child(node, key, n))
            _add(doc, parts, value, n)
    return doc


async def patch(runtime, run, arguments):
    """Edit a saved project scene with JSON Patch; the result must validate before anything is written."""
    import motion_studio
    ref = arguments.get('scene')
    scene = stored_scene(runtime, ref, project_only=True)
    edited = apply_patch(scene, arguments.get('ops'))
    if edited.get('id') != scene['id']:
        raise ValueError('patch cannot change the scene id; use variant (or save) for a new scene')
    saved, found = checked_save(runtime, edited, True)
    if found:
        return {'ok': False, 'issues': found}  # the stored scene is unchanged
    await runtime.activity_note(run, 'motion', f"Edited {saved} ({len(arguments['ops'])} change(s))")
    return render_note({'ok': True, 'ref': saved, 'applied': len(arguments['ops']),
                        'summary': motion_studio.summary(saved, 'project', edited)}, edited)


def upload_path(runtime, url):
    """The file behind a /uploads/<name> url, contained in the upload folder."""
    root = runtime.host.store.upload_dir.resolve()
    path = (root / url.removeprefix('/uploads/')).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f'Upload not found: {url}')
    return path


def upload_facts(path):
    """sha256 and, for raster images, pixel size; the caller runs this off the event loop."""
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    facts = {'sha256': digest.hexdigest(), 'bytes': path.stat().st_size}
    if EXTENSION_KINDS.get(path.suffix.lower().lstrip('.')) == 'image':
        try:
            from PIL import Image
            with Image.open(path) as image:
                facts['width'], facts['height'] = image.size
        except Exception:  # an unreadable image still imports; it just has no size hint
            pass
    return facts


def take_cursor(path, motion_scene):
    """The pointer track of a browser recording (desktop d590dce6), read from the sidecar the recorder writes
    beside its clip, as cursor-layer fields: viewport, fps and events normalised to the scene's cursor events
    (motion-design dbfe259e). None when the file isn't a take or its sidecar is unreadable."""
    try:
        with path.with_name(TAKE_SIDECAR).open('rb') as stream:
            raw = stream.read(MAX_SIDECAR_BYTES + 1)  # bounded even if the file grows after any size check
        if len(raw) > MAX_SIDECAR_BYTES:
            return None
        data = json.loads(raw.decode('utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get('version') != TAKE_SIDECAR_VERSION:
        return None
    width, height, fps, events = data.get('width'), data.get('height'), data.get('fps'), data.get('events')
    # The viewport also sizes the video box and its fit, so bad sides (0, False) end here (systems 9d30fe52).
    if not (motion_scene.viewport_side(width) and motion_scene.viewport_side(height)
            and isinstance(fps, dict) and isinstance(events, list)):
        return None
    track = []
    for event in events[:motion_scene.MAX_CURSOR_EVENTS]:
        if not isinstance(event, dict) or event.get('type') not in motion_scene.CURSOR_EVENTS:
            continue
        # t_ms, scroll deltas and named keys stay in the sidecar: the layer takes frame, type, position, button.
        item = {key: event[key] for key in ('frame', 'type', 'x', 'y') if key in event}
        if event.get('button') in motion_scene.CURSOR_BUTTONS and event['type'] in CURSOR_BUTTON_EVENTS:
            item['button'] = event['button']
        track.append(item)
    return {'viewport': [width, height], 'fps': {'num': fps.get('num'), 'den': fps.get('den')}, 'events': track}


def take_issues(layers, motion_scene):
    """The take's video and cursor layers checked by the scene schema in a one-clip scene, so import only
    returns layers that validate (systems 602d4e93): [] or the issues."""
    video, cursor = layers['video'], layers['cursor']
    width, height = cursor['viewport']
    probe = {'version': motion_scene.VERSION, 'id': 'take', 'name': 'take',
             'assets': [{'id': video['asset'], 'kind': 'video', 'ref': TAKE_PROBE_REF}],
             'compositions': [{'id': 'take', 'width': width, 'height': height, 'fps': {'num': 1, 'den': 1},
                               'duration': 1, 'tracks': [{'id': 'take', 'kind': 'visual', 'clips': [
                                   {'id': 'take-clip', 'from': 0, 'duration': 1, 'layers': [video, cursor]}]}]}]}
    return motion_scene.issues(probe)


def asset_source(runtime, url):
    """(path, scene ref) for import_asset: a room upload, or an original registered with the room (a pasted
    path or a finished recording, as fileref:<id> or its /api/files/ref link), read where it lies."""
    import motion_scene
    if isinstance(url, str) and motion_scene.UPLOAD.fullmatch(url):
        return upload_path(runtime, url), url
    match = FILEREF_INPUT.fullmatch(url) if isinstance(url, str) else None
    if match:
        return runtime.project_room.references().lookup(match.group(1))[0], f'fileref:{match.group(1)}'
    raise ValueError('url must be a room upload such as /uploads/1a2b3c4d.png (a screenshot url works), or a '
                     'registered original: fileref:<id> from a recording or a pasted path')


async def import_asset(runtime, run, arguments):
    """A room upload or a registered original (a recording, a pasted path) as a scene asset with its real sha256.
    With scene, it's added to that saved project scene; without, the entry is returned to paste in.
    For images and recordings it also returns the size and, against the scene's first composition, how it
    fits; a recording also brings its video and cursor layers, ready to place."""
    import motion_scene
    import motion_studio
    url = arguments.get('url')
    path, asset_ref = asset_source(runtime, url)
    kind = arguments.get('kind') or EXTENSION_KINDS.get(path.suffix.lower().lstrip('.'))
    if kind not in motion_scene.ASSET_KINDS:
        raise ValueError('kind must be one of ' + ', '.join(motion_scene.ASSET_KINDS))
    if motion_scene.FILEREF.fullmatch(asset_ref) and kind not in motion_scene.FILEREF_KINDS:
        raise ValueError(f"a registered original can be a {', '.join(motion_scene.FILEREF_KINDS)} asset")
    asset_id = arguments.get('asset_id')
    if not isinstance(asset_id, str) or not motion_scene.ID.fullmatch(asset_id):
        raise ValueError('asset_id must be lowercase letters, digits and hyphens, at most 48 characters')
    project = runtime.identity
    facts = await asyncio.to_thread(upload_facts, path)
    take = await asyncio.to_thread(take_cursor, path, motion_scene) if kind == 'video' else None
    if runtime.identity != project:
        # The room switched project while the file was hashed (systems 9a0066e8 F1): never save into the other
        # project's scene of the same name. Nothing awaits between here and the read and save below.
        raise ValueError('The project changed while the upload was being read; nothing was saved. Try again.')
    asset = {'id': asset_id, 'kind': kind, 'ref': asset_ref, 'sha256': facts['sha256']}
    result = {'asset': asset, 'bytes': facts['bytes']}
    warnings = []
    if facts['bytes'] > motion_studio.MAX_INLINE_ASSET_BYTES:
        # No downscale, transcode or copy is made: the agent trims the source or records a shorter take.
        warnings.append(f'This file is {facts["bytes"] / 2 ** 20:.0f} MiB, over the '
                        f'{motion_studio.MAX_INLINE_ASSET_BYTES // 2 ** 20} MiB that frame and export can inline '
                        'per page, so they will refuse it. A scene\'s distinct assets together share that limit too.')
    if take:
        # The box keeps the recording's aspect so viewport px map straight onto it (desktop d590dce6). The
        # cursor's source is the video layer returned with it; place both in the same clip.
        layer_id = arguments.get('layer_id') or asset_id
        if not isinstance(layer_id, str) or not motion_scene.ID.fullmatch(layer_id):
            raise ValueError('layer_id must be lowercase letters, digits and hyphens, at most 48 characters')
        facts['width'], facts['height'] = take['viewport']
        cursor_id = layer_id[:motion_scene.MAX_ID - len(CURSOR_SUFFIX)] + CURSOR_SUFFIX
        layers = {'video': {'id': layer_id, 'type': 'video', 'asset': asset_id, 'muted': True, 'frame': 'window'},
                  'cursor': {'id': cursor_id, 'type': 'cursor', 'source': layer_id, **take}}
        found = take_issues(layers, motion_scene)
        if found:
            warnings.append(f"The recording's input track didn't validate as a cursor layer, so only the video "
                            f"layer is returned: {found[0]['path']} {found[0]['message']}")
            del layers['cursor']
        result['layers'] = layers
    if warnings:
        result['warnings'] = warnings
    if 'width' in facts:
        result.update(width=facts['width'], height=facts['height'],
                      aspect=round(facts['width'] / facts['height'], 4))
    ref = arguments.get('scene')
    if not ref:
        return result
    scene = stored_scene(runtime, ref, project_only=True)
    if scene['version'] != 2:
        raise ValueError('import_asset adds to v2 scenes; v1 room replays have no assets')
    if 'width' in facts:
        comp = scene['compositions'][0]
        fit = min(comp['width'] / facts['width'], comp['height'] / facts['height'])
        # contain shows the whole image inside the frame; fill_scale on top of it covers the frame with no bars.
        result['fit'] = {'composition': comp['id'],
                         'contain': {'width': round(facts['width'] * fit), 'height': round(facts['height'] * fit)},
                         'fill_scale': round(max(comp['width'] / facts['width'],
                                                 comp['height'] / facts['height']) / fit, 3)}
    if any(a['id'] == asset_id for a in scene.get('assets', [])) and not arguments.get('overwrite'):
        raise ValueError(f'{ref} already has an asset {asset_id!r}; pass overwrite true to replace it')
    scene['assets'] = [*[a for a in scene.get('assets', []) if a['id'] != asset_id], asset]
    saved, found = checked_save(runtime, scene, True)
    if found:
        return {'ok': False, 'issues': found, **result}
    await runtime.activity_note(run, 'motion', f'Added asset {asset_id} ({url}) to {saved}')
    return {'ok': True, 'ref': saved, **result}


# ---- inspect (Zero #14935/#14956, QA 53e691c7) -----------------------------------------------------------------

INSPECT_SHEETS = ('contact', 'dense', 'motion', 'shots', 'frames')
DEFAULT_SHEETS = ('contact', 'motion')
DENSE_FPS = 4             # default dense rate: 12 s in 48 tiles, enough to follow small icon motion (Zero #14956)
MAX_LISTED = 100          # times listed per series (cuts, transitions, silences); the motion chart draws them all
SERIES = ('frame_motion', 'scene_per_second')  # per-sample series: drawn by the motion chart, not returned
DERIVED_HEX = 16          # sheet names: the first 64 bits of the PNG's sha256, as app.UPLOAD_HASH_HEX names uploads
INSPECT_SLOTS = asyncio.Semaphore(2)  # at most two inspections run FFmpeg at once; the rest wait their turn
EXPORT_REF = 'export:'
# A pasted original (app /api/files/reference): fileref:<id> or its served link /api/files/ref/<id>/<name>.
FILEREF_INPUT = re.compile(r'(?:fileref:|/api/files/ref/)([0-9a-f]{16})(?:/[^/]+)?')
VIDEO_REFS = ('a room upload (/uploads/<name>), export:<job_id>, or a pasted file (fileref:<id> or its '
              '/api/files/ref/<id>/<name> link)')


def video_source(runtime, ref):
    """The local file behind a room ref; never a raw path or URL, so inspect reads only what the room shares."""
    import motion_scene
    if not isinstance(ref, str) or not ref:
        raise ValueError('Name the video: ' + VIDEO_REFS)
    if ref.startswith(EXPORT_REF):
        path = runtime.motion.video(ref.removeprefix(EXPORT_REF))
        if not path:
            raise ValueError('No finished export with that job_id in this project; check export_status')
        return path
    if motion_scene.UPLOAD.fullmatch(ref):
        return upload_path(runtime, ref)
    match = FILEREF_INPUT.fullmatch(ref)
    if match:
        return runtime.project_room.references().lookup(match.group(1))[0]
    raise ValueError(f'{ref[:120]} is not a video the room can read; use ' + VIDEO_REFS)


def number(arguments, key, default, what):
    value = arguments.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f'{key} must be {what}')
    return float(value)


def inspect_options(arguments):
    """Which sheets to draw and their range, checked before any FFmpeg runs."""
    sheets = arguments.get('sheets', DEFAULT_SHEETS)
    sheets = [sheets] if isinstance(sheets, str) else sheets
    if not isinstance(sheets, (list, tuple)) or any(s not in INSPECT_SHEETS for s in sheets):
        raise ValueError('sheets lists any of ' + ', '.join(INSPECT_SHEETS))
    in_seconds = 'a time in seconds, 0 or more'
    options = {'sheets': list(dict.fromkeys(sheets)), 'start': number(arguments, 'start', 0.0, in_seconds),
               'end': number(arguments, 'end', None, in_seconds),
               'fps': number(arguments, 'fps', DENSE_FPS, 'frames per second for the dense sheet')}
    if options['fps'] <= 0:  # the default end divides by it (QA 0168df23 N1)
        raise ValueError('fps must be above 0')
    if 'frames' in options['sheets']:
        t_ms = arguments.get('t_ms', 0)
        t_ms = t_ms if isinstance(t_ms, list) else [t_ms]
        if any(isinstance(t, bool) or not isinstance(t, (int, float)) or t < 0 for t in t_ms):
            raise ValueError('t_ms lists the frame times in milliseconds, 0 or more')
        options['times'] = [t / 1000 for t in t_ms]
    return options


def analysis_summary(analysis):
    """What the agent reads: every measure, with long time lists capped and the per-sample series left out."""
    summary = {k: v for k, v in analysis.items() if k not in SERIES}
    if summary.get('audio'):
        summary['audio'] = dict(summary['audio'])  # analyze() caches its result: never trim that in place
    for holder, key in ((summary, 'cuts'), (summary, 'transitions'), (summary.get('audio'), 'silences')):
        if holder and len(holder[key]) > MAX_LISTED:
            holder[key + '_total'] = len(holder[key])
            holder[key] = holder[key][:MAX_LISTED]
    return summary


def store_derived(made, upload_dir):
    """A generated PNG into uploads under its content hash, so inspecting the same video again reuses the file."""
    target = upload_dir / (hashlib.sha256(made.read_bytes()).hexdigest()[:DERIVED_HEX] + made.suffix)
    if target.exists():
        made.unlink()
    else:
        os.replace(made, target)
    return '/uploads/' + target.name


def inspect_files(ffmpeg, path, vs_path, options, upload_dir):
    """analyze, compare and draw the requested sheets; runs off the event loop. Sheets are drawn in a scratch
    folder inside uploads (same volume, so the move is a rename) that is removed whatever happens."""
    import video_analysis
    analysis = video_analysis.analyze(ffmpeg, path)
    result = {'analysis': analysis_summary(analysis)}
    if vs_path:
        result['compare'] = video_analysis.compare(analysis, video_analysis.analyze(ffmpeg, vs_path))
    length, cuts = analysis['measured_s'], analysis['cuts']
    sheets = []
    with tempfile.TemporaryDirectory(dir=upload_dir, prefix='.inspect-') as work:
        work = Path(work)
        for kind in options['sheets']:
            out = work / f'{kind}.png'
            entry = {'kind': kind}
            if kind == 'contact':
                video_analysis.contact_sheet(ffmpeg, path, length, out)
                entry['frames'] = video_analysis.SHEET_FRAMES
            elif kind == 'dense':
                start, fps = options['start'], options['fps']
                if start >= length:
                    raise ValueError(f'start is past the end of the video ({length} s measured)')
                end = options['end'] or min(length, start + video_analysis.DENSE_MAX_TILES / fps)
                video_analysis.dense_sheet(ffmpeg, path, start, end, fps, out)
                entry.update(start=start, end=round(end, 3), fps=fps)
            elif kind == 'motion':
                video_analysis.motion_chart(ffmpeg, analysis['frame_motion'], cuts, length, out)
            elif kind == 'shots':
                fps = analysis['probe']['fps']
                if not fps:
                    raise ValueError('The shot sheet needs the frame rate, and this video does not report one')
                entry['shots'] = video_analysis.shot_sheet(ffmpeg, path, cuts, length, fps, out)
                if len(entry['shots']) < analysis['shots']:  # the sheet shows only the first ones (QA r3b)
                    entry['truncated'] = True
            else:  # frames: one PNG per time
                made = video_analysis.frames(ffmpeg, path, options['times'], work, 'frame')
                sheets += [{'kind': kind, 't_ms': round(t * 1000), 'url': store_derived(m, upload_dir)}
                           for t, m in zip(options['times'], made)]
                continue
            entry['url'] = store_derived(out, upload_dir)
            sheets.append(entry)
    result['sheets'] = sheets
    return result


async def inspect(runtime, run, arguments):
    """Measure a video (an export, an upload or a pasted original) and draw sheets of it; with vs, compare the
    headline numbers against a reference such as one of Zero's quality-bar videos."""
    import motion_studio
    job_id = arguments.get('job_id')
    ref = f'{EXPORT_REF}{job_id}' if job_id else arguments.get('url')
    path = video_source(runtime, ref)
    vs = arguments.get('vs')
    vs_path = video_source(runtime, vs) if vs else None
    options = inspect_options(arguments)
    try:
        ffmpeg = motion_studio.ffmpeg_path(runtime)
    except motion_studio.Unavailable as exc:
        raise ValueError(str(exc)) from None
    async with INSPECT_SLOTS:
        try:
            result = await asyncio.to_thread(inspect_files, ffmpeg, path, vs_path, options,
                                             runtime.host.store.upload_dir)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            # An unreadable or truncated video is an answer for the agent, not a server fault.
            raise ValueError(f'Could not analyse {ref}: {exc}') from None
    if result['sheets']:
        await runtime.live.publish(runtime.live.artifact(run, result['sheets'][0]['url'], 'upload'))
    await runtime.activity_note(run, 'motion', f'Inspected {ref}' + (f' against {vs}' if vs else ''))
    return {'video': ref, **({'vs': vs} if vs else {}), **result}
