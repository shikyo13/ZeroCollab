"""room_motion actions on the agent side (tasks 78f77506, 31dcc7da). motion_studio.py holds the renderer and
exporter (systems, S3); this module never edits it, only calls its catalogue, validation and save.

publish: a finished export becomes a room upload, posted to the agent's chat and optionally attached to a task,
where it plays inline (Zero #14291, PM decision deb4f237: videos arrive in chat and on tasks, not on a page).
variant, patch, import_asset (Zero #14804, Remotion parity): re-render a template with another composition and
props, edit a large saved scene in place, and turn a room upload into a hashed scene asset.
"""
import asyncio
import copy
import hashlib
import os
import shutil
import uuid

from team_builder import text

MAX_PATCH_OPS = 200
EXTENSION_KINDS = {'png': 'image', 'jpg': 'image', 'jpeg': 'image', 'webp': 'image', 'gif': 'image',
                   'webm': 'video', 'mp4': 'video', 'woff2': 'font', 'woff': 'font', 'ttf': 'font', 'otf': 'font',
                   'ogg': 'audio', 'mp3': 'audio', 'wav': 'audio', 'm4a': 'audio'}

MAX_VIDEO_BYTES = 1024 * 1024 * 1024  # an exported scene of up to 10 minutes stays far below this
# A v2 scene renders when the room has the S5 renderer files (motion_studio.summary, V2_SCRIPTS); a copy of
# the room without them still says so up front instead of only at frame/export (demo-marketing #14460).
NOT_RENDERABLE = ('Valid, but this copy of the room is missing the v2 renderer files, so frame and export will refuse '
                  'this scene. It saves and keeps, and renders once they are installed.')


def has_layer(node, kind):
    """Whether any layer under node (compositions, tracks, clips, groups) has type kind."""
    if isinstance(node, dict):
        return node.get('type') == kind or any(has_layer(v, kind) for k, v in node.items()
                                               if k in ('tracks', 'clips', 'layers'))
    return isinstance(node, list) and any(has_layer(v, kind) for v in node)


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


async def import_asset(runtime, run, arguments):
    """A room upload (a screenshot, a recording, any /uploads file) as a scene asset with its real sha256.
    With scene, it's added to that saved project scene; without, the entry is returned to paste in.
    For images it also returns the size and, against the scene's first composition, how it fits."""
    import motion_scene
    url = arguments.get('url')
    if not isinstance(url, str) or not motion_scene.UPLOAD.fullmatch(url):
        raise ValueError('url must be a room upload such as /uploads/1a2b3c4d.png (a screenshot url works)')
    root = runtime.host.store.upload_dir.resolve()
    path = (root / url.removeprefix('/uploads/')).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f'Upload not found: {url}')
    kind = arguments.get('kind') or EXTENSION_KINDS.get(path.suffix.lower().lstrip('.'))
    if kind not in motion_scene.ASSET_KINDS:
        raise ValueError('kind must be one of ' + ', '.join(motion_scene.ASSET_KINDS))
    asset_id = arguments.get('asset_id')
    if not isinstance(asset_id, str) or not motion_scene.ID.fullmatch(asset_id):
        raise ValueError('asset_id must be lowercase letters, digits and hyphens, at most 48 characters')
    project = runtime.identity
    facts = await asyncio.to_thread(upload_facts, path)
    if runtime.identity != project:
        # The room switched project while the file was hashed (systems 9a0066e8 F1): never save into the other
        # project's scene of the same name. Nothing awaits between here and the read and save below.
        raise ValueError('The project changed while the upload was being read; nothing was saved. Try again.')
    asset = {'id': asset_id, 'kind': kind, 'ref': url, 'sha256': facts['sha256']}
    result = {'asset': asset, 'bytes': facts['bytes']}
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
