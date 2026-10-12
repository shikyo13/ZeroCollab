"""Motion scene v2: data-only scenes shared by live animation, the Studio and frame-accurate export
(task 6ff4919d; Docs/motion-core.md slice S2; field reference in Docs/motion-scene-v2.md).

A scene is JSON, never code. v2 adds compositions (size, rational fps, duration in frames, props),
tracks, clips, layers (text, image, video, audio, shape, path, captions, group, capture, room),
per-property keyframes with easing, clip transitions, an asset manifest and a props schema.

    issues(scene)      -> [{path, message}], dispatched on scene['version'] (empty list: valid)
    upgrade(scene)     -> the scene as v2; a v1 scene becomes one composition holding one 'room' layer,
                          which motion.js draws exactly as before (pixel-identical v1 rendering)
    legacy(scene)      -> the v1 scene inside a v2 scene that is only a migrated room layer, else None
    revision(scene, composition_id) -> sha256 of the composition and the assets it uses (frame contract)
    frame_ms(frame, fps) -> the time of a frame; fps is {num, den}
"""
import hashlib
import json
import math
import re

VERSION = 2
ID = re.compile(r'^[a-z0-9][a-z0-9-]{0,47}$')
UPLOAD = re.compile(r'^/uploads/[A-Za-z0-9][A-Za-z0-9._-]{0,120}$')
# Files shipped in the motion kit: every path segment starts with a letter or digit, so '.', '..' and empty
# segments are impossible and a ref can never leave the kit folder (systems 9001172a F2).
KIT_ASSET = re.compile(r'^motion:(?=.{1,120}$)[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*$')
COLOR = re.compile(r'^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$')
PATH_DATA = re.compile(r'^[MmLlHhVvCcSsQqTtAaZz0-9eE.,+\-\s]*$')
MAX_ISSUES = 100
MAX_SIDE = 7680
MAX_SECONDS = 3600
MAX_DEPTH = 8
MAX_LAYERS = 1000
MAX_KEYFRAMES = 400

LAYER_TYPES = ('text', 'image', 'video', 'audio', 'shape', 'path', 'captions', 'group', 'capture', 'room')
ASSET_KINDS = ('image', 'video', 'audio', 'font', 'capture')
EASINGS = ('linear', 'ease', 'ease-in', 'ease-out', 'ease-in-out', 'step-start', 'step-end')
TRANSITIONS = ('cut', 'fade', 'slide', 'wipe', 'zoom')
DIRECTIONS = ('left', 'right', 'up', 'down')

# Properties every visual layer has; all are numbers and all can be keyframed.
TRANSFORM = {'x': (-100000, 100000), 'y': (-100000, 100000), 'width': (0, 100000), 'height': (0, 100000),
             'scale': (0, 100), 'scale_x': (-100, 100), 'scale_y': (-100, 100), 'rotate': (-36000, 36000),
             'opacity': (0, 1), 'anchor_x': (0, 1), 'anchor_y': (0, 1), 'blur': (0, 500)}
# Per layer type: field -> spec. Spec kinds: ('num', low, high), ('int', low, high), ('color',), ('text', limit),
# ('enum', values), ('bool',), ('asset', kinds), ('points',), ('path',), ('cues',), ('layers',), ('room',).
NUM, INT = 'num', 'int'
TYPE_FIELDS = {
    'text': {'text': ('text', 4000), 'font': ('text', 80), 'font_asset': ('asset', ('font',)), 'size': (NUM, 1, 2000),
             'weight': (INT, 100, 900), 'color': ('color',), 'align': ('enum', ('left', 'center', 'right')),
             'line_height': (NUM, 0.5, 4), 'letter_spacing': (NUM, -50, 200), 'max_width': (NUM, 1, 100000),
             'fit': ('enum', ('none', 'shrink')), 'reveal': (NUM, 0, 1)},
    'image': {'asset': ('asset', ('image', 'capture')), 'fit': ('enum', ('contain', 'cover', 'fill', 'none'))},
    'capture': {'asset': ('asset', ('capture', 'image')), 'fit': ('enum', ('contain', 'cover', 'fill', 'none')),
                'label': ('enum', ('real', 'synthetic')), 'caption': ('text', 200)},
    'video': {'asset': ('asset', ('video',)), 'fit': ('enum', ('contain', 'cover', 'fill', 'none')),
              'trim': (INT, 0, 10 ** 7), 'rate': (NUM, 0.1, 8), 'volume': (NUM, 0, 1), 'muted': ('bool',)},
    'audio': {'asset': ('asset', ('audio',)), 'trim': (INT, 0, 10 ** 7), 'rate': (NUM, 0.1, 8), 'volume': (NUM, 0, 1)},
    'shape': {'shape': ('enum', ('rect', 'ellipse', 'polygon', 'star', 'line')), 'fill': ('color',),
              'stroke': ('color',), 'stroke_width': (NUM, 0, 1000), 'radius': (NUM, 0, 100000),
              'sides': (INT, 3, 64), 'points': ('points',), 'inner_radius': (NUM, 0, 1)},
    'path': {'d': ('path',), 'fill': ('color',), 'stroke': ('color',), 'stroke_width': (NUM, 0, 1000),
             'progress': (NUM, 0, 1)},
    'captions': {'cues': ('cues',), 'font': ('text', 80), 'size': (NUM, 1, 2000), 'color': ('color',),
                 'background': ('color',), 'align': ('enum', ('left', 'center', 'right'))},
    'group': {'layers': ('layers',)},
    'room': {'scene': ('room',)},
}
REQUIRED = {'text': ('text',), 'image': ('asset',), 'capture': ('asset',), 'video': ('asset',), 'audio': ('asset',),
            'shape': ('shape',), 'path': ('d',), 'captions': ('cues',), 'group': ('layers',), 'room': ('scene',)}
COMMON = ('id', 'type', 'name', 'keyframes', 'hidden')


class Report:
    """Collects issues with a JSON-path-like location; stops collecting at MAX_ISSUES."""

    def __init__(self):
        self.items = []

    def add(self, path, message):
        if len(self.items) < MAX_ISSUES:
            self.items.append({'path': path or 'scene', 'message': message})

    def keys(self, obj, path, allowed, required=()):
        if not isinstance(obj, dict):
            self.add(path, 'must be an object')
            return False
        for key in sorted(set(obj) - set(allowed)):
            self.add(f'{path}.{key}', 'is not a known field')
        for key in required:
            if key not in obj:
                self.add(f'{path}.{key}', 'is required')
        return True

    def ident(self, value, path, seen=None):
        if not isinstance(value, str) or not ID.match(value):
            self.add(path, 'must be lowercase letters, digits and hyphens, at most 48')
        elif seen is not None:
            if value in seen:
                self.add(path, f'repeats the id {value!r}')
            seen.add(value)

    def text(self, value, path, limit, required=True):
        if value is None and not required:
            return
        if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
            self.add(path, f'must be text of at most {limit} characters')

    def number(self, value, path, low, high, whole=False):
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        if whole:
            ok = ok and isinstance(value, int)
        if not ok or not low <= value <= high:
            self.add(path, f'must be a {"whole number" if whole else "number"} from {low} to {high}')
            return False
        return True

    def listing(self, value, path, high, low=0):
        if not isinstance(value, list) or not low <= len(value) <= high:
            self.add(path, f'must be a list of {low} to {high} items')
            return []
        return value


# ---- version dispatch ------------------------------------------------------------------------------

def issues(scene):
    """Every problem in a scene as [{path, message}]; an empty list means the scene is valid."""
    if not isinstance(scene, dict):
        return [{'path': 'scene', 'message': 'must be an object'}]
    if scene.get('version') == 1:
        import motion_studio  # v1 stays defined where it renders today
        try:
            motion_studio.validate(scene)
            return []
        except ValueError as exc:
            return [{'path': 'scene', 'message': str(exc)}]
    if scene.get('version') != VERSION:
        return [{'path': 'scene.version', 'message': 'must be 1 or 2'}]
    report = Report()
    V2(report, scene).check()
    return report.items


def upgrade(scene):
    """A valid scene as v2. v1 becomes one 1920x1080, 30/1 fps composition with one full-length visual
    track holding one clip with one 'room' layer: the v1 layout and beats, drawn by motion.js as today."""
    if scene.get('version') == VERSION:
        return scene
    import motion_studio
    motion_studio.validate(scene)
    fps = {'num': motion_studio.FPS, 'den': 1}
    frames = frames_for_ms(scene['duration_ms'], fps)
    room = {k: v for k, v in scene.items() if k not in ('version', 'id', 'name', 'source', 'duration_ms')}
    # The exact v1 duration rides along: whole frames alone can't restore every millisecond value.
    upgraded = {'version': VERSION, 'id': scene['id'], 'migrated_from': {'version': 1, 'duration_ms': scene['duration_ms']},
                'compositions': [{'id': 'main', 'width': motion_studio.WIDTH, 'height': motion_studio.HEIGHT,
                                  'fps': fps, 'duration': frames,
                                  'tracks': [{'id': 'room', 'kind': 'visual', 'clips': [
                                      {'id': 'main', 'from': 0, 'duration': frames,
                                       'layers': [{'id': 'room', 'type': 'room', 'scene': room}]}]}]}]}
    for key in ('name', 'source'):
        if key in scene:
            upgraded[key] = scene[key]
    return upgraded


def legacy(scene):
    """The v1 scene a migrated v2 scene wraps, so the existing renderer and exporter can draw it;
    None for a v2 scene that uses anything beyond that single room layer."""
    if scene.get('version') == 1:
        return scene
    comps = scene.get('compositions') or []
    if len(comps) != 1 or scene.get('assets'):
        return None
    comp = comps[0]
    tracks = comp.get('tracks') or []
    if len(tracks) != 1 or len(tracks[0].get('clips') or []) != 1:
        return None
    clip = tracks[0]['clips'][0]
    layers = clip.get('layers') or []
    if len(layers) != 1 or layers[0].get('type') != 'room' or layers[0].get('keyframes') or clip.get('from') \
            or clip.get('offset') or clip.get('transition') or comp.get('fps') != {'num': 30, 'den': 1} \
            or (comp.get('width'), comp.get('height')) != (1920, 1080):
        return None
    room = layers[0]['scene']
    duration_ms = (scene.get('migrated_from') or {}).get('duration_ms') or math.ceil(frame_ms(comp['duration'], comp['fps']))
    fields = {'version': 1, 'id': scene['id'], 'name': scene.get('name'), 'source': scene.get('source'),
              'duration_ms': duration_ms, **room}
    # The v1 field order, so the page built from it carries the same JSON bytes as the original scene.
    order = ('version', 'id', 'name', 'source', 'duration_ms', 'layout', 'beats')
    v1 = {k: fields[k] for k in order if k in fields and (fields[k] is not None or k not in ('name', 'source'))}
    # Exactly what upgrade() makes from that v1 scene, or nothing: any other field or value (a hidden track,
    # a background, a shorter clip) is something the v1 renderer cannot draw (systems 9001172a F1).
    import motion_studio
    try:
        motion_studio.validate(v1)
    except ValueError:
        return None
    expected = upgrade(v1)
    if 'migrated_from' not in scene:
        expected.pop('migrated_from')
    return v1 if expected == scene else None


# ---- frame contract (Docs/motion-core.md amendment 1) ---------------------------------------------

def frame_ms(frame, fps):
    return frame * 1000 * fps['den'] / fps['num']


def frames_for_ms(ms, fps):
    return max(1, math.floor(ms * fps['num'] / (1000 * fps['den'])))


def revision(scene, composition_id):
    """Content hash of one composition and the assets it references: the frame contract's
    composition revision, so receipts and caches change exactly when the rendered input does."""
    comp = next(c for c in scene['compositions'] if c['id'] == composition_id)
    used = set(_asset_refs(comp))
    assets = [a for a in scene.get('assets', []) if a['id'] in used]
    canonical = json.dumps({'composition': comp, 'assets': assets}, sort_keys=True, separators=(',', ':'),
                           ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def frame_input(scene, composition_id, frame, resolve=None):
    """The renderer's FrameInput (pinned with frontend in 1f9560e4/965a2c48):
    {composition: {id, revision}, fps: {num, den}, frame, seed, assets: [{id, kind, sha256, ref, src}]}.
    resolve(ref) returns the data: URL the page may load (its CSP allows no network); assets it cannot
    resolve are left without src, so the renderer reports them instead of fetching anything."""
    scene = upgrade(scene)
    comp = next(c for c in scene['compositions'] if c['id'] == composition_id)
    if not (isinstance(frame, int) and 0 <= frame < comp['duration']):
        raise ValueError(f"frame must be a whole number from 0 to {comp['duration'] - 1}")
    used = set(_asset_refs(comp))
    assets = []
    for a in scene.get('assets', []):
        if a['id'] in used:
            item = {'id': a['id'], 'kind': a['kind'], 'sha256': a.get('sha256', ''), 'ref': a['ref']}
            src = resolve(a['ref']) if resolve else None
            if src:
                item['src'] = src
            assets.append(item)
    return {'composition': {'id': comp['id'], 'revision': revision(scene, comp['id'])}, 'fps': dict(comp['fps']),
            'frame': frame, 'seed': comp.get('seed', 0), 'assets': assets}


def _asset_refs(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ('asset', 'font_asset') and isinstance(value, str):
                yield value
            else:
                yield from _asset_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _asset_refs(item)


# ---- v2 validation ---------------------------------------------------------------------------------

class V2:
    def __init__(self, report, scene):
        self.r, self.scene = report, scene
        self.assets, self.props, self.ids, self.count, self.fps, self.migrated_ms = {}, {}, set(), 0, None, None

    def check(self):
        r, scene = self.r, self.scene
        r.keys(scene, 'scene', ('version', 'id', 'name', 'source', 'migrated_from', 'assets', 'compositions'),
               ('version', 'id', 'compositions'))
        r.ident(scene.get('id'), 'scene.id')
        r.text(scene.get('name'), 'scene.name', 80, required=False)
        r.text(scene.get('source'), 'scene.source', 200, required=False)
        self.migrated_ms = None
        if 'migrated_from' in scene:
            m = scene['migrated_from']
            if r.keys(m, 'scene.migrated_from', ('version', 'duration_ms'), ('version', 'duration_ms')):
                if m.get('version') != 1:
                    r.add('scene.migrated_from.version', 'can only be 1')
                if r.number(m.get('duration_ms'), 'scene.migrated_from.duration_ms', 1000, 600000, whole=True):
                    self.migrated_ms = m['duration_ms']
        seen = set()
        for i, asset in enumerate(r.listing(scene.get('assets', []), 'scene.assets', 200)):
            self.asset(asset, f'scene.assets[{i}]', seen)
        comps = set()
        for i, comp in enumerate(r.listing(scene.get('compositions'), 'scene.compositions', 16, 1)):
            self.composition(comp, f'scene.compositions[{i}]', comps)

    def asset(self, a, path, seen):
        r = self.r
        if not r.keys(a, path, ('id', 'kind', 'ref', 'sha256', 'name'), ('id', 'kind', 'ref')):
            return
        r.ident(a.get('id'), f'{path}.id', seen)
        if a.get('kind') not in ASSET_KINDS:
            r.add(f'{path}.kind', f'must be one of {", ".join(ASSET_KINDS)}')
        ref = a.get('ref')
        if not isinstance(ref, str) or not (UPLOAD.match(ref) or KIT_ASSET.match(ref)):
            # Never a URL or a file path: assets are this room's uploads or files shipped with the motion kit.
            r.add(f'{path}.ref', "must be a room upload '/uploads/<file>' or a kit file 'motion:<file>'")
        if 'sha256' in a and not (isinstance(a['sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', a['sha256'])):
            r.add(f'{path}.sha256', 'must be 64 lowercase hexadecimal characters')
        r.text(a.get('name'), f'{path}.name', 120, required=False)
        if isinstance(a.get('id'), str):
            self.assets[a['id']] = a.get('kind')

    def composition(self, c, path, seen):
        r = self.r
        if not r.keys(c, path, ('id', 'name', 'width', 'height', 'fps', 'duration', 'background', 'seed', 'props',
                                'props_schema', 'tracks'), ('id', 'width', 'height', 'fps', 'duration', 'tracks')):
            return
        r.ident(c.get('id'), f'{path}.id', seen)
        r.text(c.get('name'), f'{path}.name', 80, required=False)
        r.number(c.get('width'), f'{path}.width', 16, MAX_SIDE, whole=True)
        r.number(c.get('height'), f'{path}.height', 16, MAX_SIDE, whole=True)
        fps = c.get('fps')
        fps_ok = r.keys(fps, f'{path}.fps', ('num', 'den'), ('num', 'den')) and \
            r.number(fps.get('num'), f'{path}.fps.num', 1, 240000, whole=True) & \
            r.number(fps.get('den'), f'{path}.fps.den', 1, 1001, whole=True)
        if fps_ok and not 1 <= fps['num'] / fps['den'] <= 240:
            r.add(f'{path}.fps', 'must be between 1 and 240 frames per second')
            fps_ok = False
        self.fps = fps if fps_ok else None
        limit = math.floor(MAX_SECONDS * fps['num'] / fps['den']) if fps_ok else 10 ** 9
        duration = c.get('duration')
        duration_ok = r.number(duration, f'{path}.duration', 1, limit, whole=True)
        if 'background' in c:
            self.color(c['background'], f'{path}.background')
        if 'seed' in c:
            r.number(c['seed'], f'{path}.seed', 0, 2 ** 31 - 1, whole=True)
        self.props = self.props_schema(c.get('props_schema'), f'{path}.props_schema')
        if 'props' in c:
            self.props_values(c['props'], f'{path}.props')
        self.ids, self.count = set(), 0
        track_ids = set()
        for i, track in enumerate(r.listing(c.get('tracks'), f'{path}.tracks', 64, 1)):
            self.track(track, f'{path}.tracks[{i}]', track_ids, duration if duration_ok else None)

    def props_schema(self, schema, path):
        """A JSON Schema subset the Studio inspector can render: an object of typed properties."""
        r, props = self.r, {}
        if schema is None:
            return props
        if not r.keys(schema, path, ('type', 'properties', 'required'), ('type', 'properties')):
            return props
        if schema.get('type') != 'object':
            r.add(f'{path}.type', "must be 'object'")
        properties = schema.get('properties')
        if not isinstance(properties, dict) or len(properties) > 64:
            r.add(f'{path}.properties', 'must be an object of at most 64 properties')
            return props
        for name, spec in properties.items():
            where = f'{path}.properties.{name}'
            if not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', name):
                r.add(where, 'property names are lowercase letters, digits and underscores, at most 40')
            if not r.keys(spec, where, ('type', 'title', 'description', 'default', 'enum', 'minimum', 'maximum',
                                         'maxLength', 'format'), ('type',)):
                continue
            kind = spec.get('type')
            if kind not in ('string', 'number', 'integer', 'boolean'):
                r.add(f'{where}.type', "must be 'string', 'number', 'integer' or 'boolean'")
                continue
            if spec.get('format') not in (None, 'color', 'multiline'):
                r.add(f'{where}.format', "can only be 'color' or 'multiline'")
            r.text(spec.get('title'), f'{where}.title', 80, required=False)
            r.text(spec.get('description'), f'{where}.description', 400, required=False)
            props[name] = spec
        for i, name in enumerate(r.listing(schema.get('required', []), f'{path}.required', 64)):
            if name not in props:
                r.add(f'{path}.required[{i}]', f'names no property {name!r}')
        return props

    def props_values(self, values, path):
        r = self.r
        if not isinstance(values, dict):
            r.add(path, 'must be an object')
            return
        for name, value in values.items():
            spec = self.props.get(name)
            if not spec:
                r.add(f'{path}.{name}', 'is not in props_schema')
                continue
            kind = spec['type']
            ok = (kind == 'string' and isinstance(value, str) and len(value) <= spec.get('maxLength', 4000)) or \
                 (kind == 'boolean' and isinstance(value, bool)) or \
                 (kind in ('number', 'integer') and isinstance(value, (int, float)) and not isinstance(value, bool)
                  and (kind == 'number' or isinstance(value, int)))
            if not ok:
                r.add(f'{path}.{name}', f'must be a {kind}')
            elif 'enum' in spec and value not in spec['enum']:
                r.add(f'{path}.{name}', 'must be one of its enum values')

    def track(self, t, path, seen, duration):
        r = self.r
        if not r.keys(t, path, ('id', 'kind', 'name', 'muted', 'hidden', 'clips'), ('id', 'kind', 'clips')):
            return
        r.ident(t.get('id'), f'{path}.id', seen)
        if t.get('kind') not in ('visual', 'audio'):
            r.add(f'{path}.kind', "must be 'visual' or 'audio'")
        clips = r.listing(t.get('clips'), f'{path}.clips', 500)
        spans = []
        for i, clip in enumerate(clips):
            span = self.clip(clip, f'{path}.clips[{i}]', t.get('kind'), duration)
            if span:
                spans.append((span, i))
        spans.sort()
        for ((start, end, fade), i), ((nstart, nend, nfade), j) in zip(spans, spans[1:]):
            # Clips on one track may overlap only by the later clip's transition.
            if nstart < end and end - nstart > nfade:
                r.add(f'{path}.clips[{j}].from', f'overlaps clip {i + 1} by more than its transition')

    def clip(self, c, path, track_kind, duration):
        r = self.r
        if not r.keys(c, path, ('id', 'name', 'from', 'duration', 'offset', 'transition', 'layers'),
                      ('id', 'from', 'duration', 'layers')):
            return None
        r.ident(c.get('id'), f'{path}.id', self.ids)
        start_ok = r.number(c.get('from'), f'{path}.from', 0, 10 ** 9, whole=True)
        length_ok = r.number(c.get('duration'), f'{path}.duration', 1, 10 ** 9, whole=True)
        if 'offset' in c:
            r.number(c['offset'], f'{path}.offset', 0, 10 ** 9, whole=True)
        if start_ok and length_ok and duration is not None and c['from'] + c['duration'] > duration:
            r.add(f'{path}.duration', 'runs past the end of the composition')
        fade = 0
        if 'transition' in c:
            fade = self.transition(c['transition'], f'{path}.transition', c.get('duration') if length_ok else None)
        length = c['duration'] if length_ok else None
        for i, layer in enumerate(r.listing(c.get('layers'), f'{path}.layers', 200, 1)):
            self.layer(layer, f'{path}.layers[{i}]', track_kind, length, 1)
        return (c['from'], c['from'] + c['duration'], fade) if start_ok and length_ok else None

    def transition(self, t, path, length):
        r = self.r
        if not r.keys(t, path, ('kind', 'duration', 'easing', 'direction'), ('kind', 'duration')):
            return 0
        if t.get('kind') not in TRANSITIONS:
            r.add(f'{path}.kind', f'must be one of {", ".join(TRANSITIONS)}')
        if not r.number(t.get('duration'), f'{path}.duration', 0, length if length else 10 ** 9, whole=True):
            return 0
        if 'easing' in t:
            self.easing(t['easing'], f'{path}.easing')
        if 'direction' in t and t['direction'] not in DIRECTIONS:
            r.add(f'{path}.direction', f'must be one of {", ".join(DIRECTIONS)}')
        return t['duration']

    def layer(self, layer, path, track_kind, length, depth):
        r = self.r
        self.count += 1
        if self.count > MAX_LAYERS:
            if self.count == MAX_LAYERS + 1:
                r.add(path, f'a composition holds at most {MAX_LAYERS} layers')
            return
        if not isinstance(layer, dict):
            r.add(path, 'must be an object')
            return
        kind = layer.get('type')
        if kind not in LAYER_TYPES:
            r.add(f'{path}.type', f'must be one of {", ".join(LAYER_TYPES)}')
            return
        fields = TYPE_FIELDS[kind]
        visual = kind not in ('audio', 'room')
        allowed = COMMON + tuple(fields) + (tuple(TRANSFORM) if visual else ())
        if not r.keys(layer, path, allowed, ('id', 'type') + REQUIRED[kind]):
            return
        r.ident(layer.get('id'), f'{path}.id', self.ids)
        r.text(layer.get('name'), f'{path}.name', 80, required=False)
        if (track_kind == 'audio') != (kind == 'audio'):
            r.add(f'{path}.type', 'audio tracks hold only audio layers, and audio layers sit on audio tracks')
        for name, (low, high) in TRANSFORM.items():
            if visual and name in layer:
                self.value(layer[name], f'{path}.{name}', ('num', low, high))
        for name, spec in fields.items():
            if name not in layer:
                continue
            if spec[0] == 'layers':
                if depth >= MAX_DEPTH:
                    r.add(f'{path}.layers', f'groups nest at most {MAX_DEPTH} deep')
                    continue
                for i, child in enumerate(r.listing(layer['layers'], f'{path}.layers', 200, 1)):
                    self.layer(child, f'{path}.layers[{i}]', track_kind, length, depth + 1)
            elif spec[0] == 'room':
                self.room(layer['scene'], f'{path}.scene', length)
            else:
                self.value(layer[name], f'{path}.{name}', spec)
        if 'keyframes' in layer:
            animatable = {**({n: ('num', lo, hi) for n, (lo, hi) in TRANSFORM.items()} if visual else {}),
                          **{n: s for n, s in fields.items() if s[0] in (NUM, INT, 'color')}}
            self.keyframes(layer['keyframes'], f'{path}.keyframes', animatable, length)

    def value(self, value, path, spec):
        """A literal, or {"prop": name} bound to a composition prop of a matching type."""
        r = self.r
        if isinstance(value, dict) and set(value) == {'prop'}:
            prop = self.props.get(value['prop']) if isinstance(value['prop'], str) else None
            if not prop:
                r.add(path, f'binds unknown prop {value["prop"]!r}')
            else:
                wanted = {'text': 'string', 'color': 'string', 'enum': 'string', 'bool': 'boolean'}.get(
                    spec[0], 'number' if spec[0] in (NUM, INT) else None)
                if wanted is None or prop['type'] not in ((wanted, 'integer') if wanted == 'number' else (wanted,)):
                    r.add(path, f'binds prop {value["prop"]!r} of the wrong type')
            return
        kind = spec[0]
        if kind == NUM:
            r.number(value, path, spec[1], spec[2])
        elif kind == INT:
            r.number(value, path, spec[1], spec[2], whole=True)
        elif kind == 'color':
            self.color(value, path)
        elif kind == 'text':
            r.text(value, path, spec[1])
        elif kind == 'enum':
            if value not in spec[1]:
                r.add(path, f'must be one of {", ".join(spec[1])}')
        elif kind == 'bool':
            if not isinstance(value, bool):
                r.add(path, 'must be true or false')
        elif kind == 'asset':
            if value not in self.assets:
                r.add(path, f'names no asset {value!r}' if isinstance(value, str) else 'must be an asset id')
            elif self.assets[value] not in spec[1]:
                r.add(path, f'must name a {" or ".join(spec[1])} asset')
        elif kind == 'points':
            points = r.listing(value, path, 512, 2)
            if any(not (isinstance(p, list) and len(p) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                                   and math.isfinite(v) for v in p)) for p in points):
                r.add(path, 'must be [x, y] number pairs')
        elif kind == 'path':
            if not isinstance(value, str) or not value.strip() or len(value) > 20000 or not PATH_DATA.match(value):
                r.add(path, 'must be SVG path data (commands and numbers only), at most 20000 characters')
        elif kind == 'cues':
            ends = -1
            for i, cue in enumerate(r.listing(value, path, 2000, 1)):
                where = f'{path}[{i}]'
                if not r.keys(cue, where, ('from', 'duration', 'text'), ('from', 'duration', 'text')):
                    continue
                if r.number(cue.get('from'), f'{where}.from', 0, 10 ** 9, whole=True) and \
                        r.number(cue.get('duration'), f'{where}.duration', 1, 10 ** 9, whole=True):
                    if cue['from'] < ends:
                        r.add(f'{where}.from', 'starts before the previous cue ends')
                    ends = cue['from'] + cue['duration']
                r.text(cue.get('text'), f'{where}.text', 400)

    def keyframes(self, frames, path, animatable, length):
        r = self.r
        if not isinstance(frames, dict):
            r.add(path, 'must be an object of property -> keyframes')
            return
        for name, points in frames.items():
            where = f'{path}.{name}'
            if name not in animatable:
                r.add(where, 'is not an animatable property of this layer')
                continue
            previous = -1
            for i, point in enumerate(r.listing(points, where, MAX_KEYFRAMES, 1)):
                at = f'{where}[{i}]'
                if not r.keys(point, at, ('frame', 'value', 'easing'), ('frame', 'value')):
                    continue
                # Keyframe frames count from the clip's start, so moving a clip keeps its animation.
                if r.number(point.get('frame'), f'{at}.frame', 0, (length - 1) if length else 10 ** 9, whole=True):
                    if point['frame'] <= previous:
                        r.add(f'{at}.frame', 'must come after the previous keyframe')
                    previous = point['frame']
                self.value(point.get('value'), f'{at}.value', animatable[name])
                if 'easing' in point:
                    self.easing(point['easing'], f'{at}.easing')

    def easing(self, value, path):
        """A named curve, {"bezier": [x1, y1, x2, y2]} or {"spring": {mass, stiffness, damping}}."""
        r = self.r
        if isinstance(value, str):
            if value not in EASINGS:
                r.add(path, f'must be one of {", ".join(EASINGS)}, a bezier or a spring')
            return
        if isinstance(value, dict) and set(value) == {'bezier'}:
            b = value['bezier']
            if not (isinstance(b, list) and len(b) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                                and math.isfinite(v) for v in b)
                    and 0 <= b[0] <= 1 and 0 <= b[2] <= 1 and all(-10 <= v <= 10 for v in b)):
                r.add(f'{path}.bezier', 'must be [x1, y1, x2, y2] with x1 and x2 from 0 to 1')
            return
        if isinstance(value, dict) and set(value) == {'spring'}:
            s = value['spring']
            if r.keys(s, f'{path}.spring', ('mass', 'stiffness', 'damping'), ()):
                for name, (low, high) in (('mass', (0.01, 100)), ('stiffness', (0.01, 10000)), ('damping', (0, 1000))):
                    if name in s:
                        r.number(s[name], f'{path}.spring.{name}', low, high)
            return
        r.add(path, f'must be one of {", ".join(EASINGS)}, a bezier or a spring')

    def color(self, value, path):
        if isinstance(value, dict) and set(value) == {'prop'}:
            self.value(value, path, ('color',))
        elif not isinstance(value, str) or not COLOR.match(value):
            self.r.add(path, 'must be a colour like #1e90ff or #1e90ffcc')

    def room(self, body, path, length):
        """A v1 room scene (layout and beats) drawn by motion.js; checked with the v1 rules."""
        import motion_studio
        if not isinstance(body, dict):
            self.r.add(path, 'must be an object with layout and beats')
            return
        clash = {'version', 'id', 'duration_ms'} & set(body)
        if clash:
            self.r.add(path, f'takes its id and duration from the scene and clip, not {sorted(clash)[0]!r}')
            return
        # The room's beats are in milliseconds: they must fit the clip (or a migrated scene's exact v1 duration).
        exact = self.migrated_ms or (math.ceil(frame_ms(length, self.fps)) if length and self.fps else 600000)
        duration_ms = max(1000, min(600000, exact))
        try:
            motion_studio.validate({'version': 1, 'id': 'room', 'duration_ms': duration_ms, **body})
        except ValueError as exc:
            self.r.add(path, str(exc))
