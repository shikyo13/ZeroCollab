"""Pure scene edits and recording-sidecar helpers; no room callbacks."""
import copy
import hashlib
import json
from pathlib import Path

MAX_PATCH_OPS = 200
TAKE_SIDECAR = 'sidecar.json'
TAKE_SIDECAR_VERSION = 1
MAX_SIDECAR_BYTES = 16 * 1024 * 1024
CURSOR_BUTTON_EVENTS = ('down', 'up', 'click')
CURSOR_SUFFIX = '-cursor'
TAKE_PROBE_REF = '/uploads/take.webm'

def text(value, label, maximum=16000, required=True):
    if not isinstance(value, str) or len(value) > maximum or required and not value.strip():
        raise ValueError(f'{label} must be text of at most {maximum} characters')
    return value.strip()

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
    return type(a) is type(b) and a == b

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
