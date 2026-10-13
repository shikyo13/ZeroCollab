# Motion scene v2: field reference

Task 6ff4919d (slice S2 of Docs/motion-core.md). Code: `motion_scene.py`. This file pins the names that S1 (motion-core), S3 (render farm), S4 (Studio), S5 (layers) and S6 (`room_motion`) share. Rename nothing here without updating all of them.

**Rendering:** v2 scenes render through the S5 renderer (`Docs/motion-render2.md`); v1 and migrated v1 scenes keep their original renderer. Sound is not exported yet: `export` refuses a composition with an `audio` layer or an unmuted `video` layer, and `room_motion` validate and save warn first. `frame` and `export` use the first composition.

A scene is JSON data, never code. Validation returns every problem as `[{path, message}]`, for example `{"path": "scene.compositions[0].tracks[0].clips[1].from", "message": "overlaps clip 1 by more than its transition"}`. An empty list means valid. At most 100 issues are reported.

## API (motion_scene.py)

| Call | Returns |
|-|-|
| `issues(scene)` | issue list; dispatches on `version` (1 or 2) |
| `upgrade(scene)` | the scene as v2 (v1 becomes one `room` layer) |
| `legacy(scene)` | the v1 scene a migrated v2 scene wraps, else `None` |
| `revision(scene, composition_id)` | sha256 of the composition plus the assets it uses |
| `frame_ms(frame, fps)` | the time of a frame, `frame * 1000 * den / num` |
| `frames_for_ms(ms, fps)` | whole frames in a duration (floor, at least 1) |

## Scene

| Field | Type | Notes |
|-|-|-|
| `version` | 2 | required |
| `id` | id | lowercase letters, digits and hyphens, at most 48; matches the file name |
| `name`, `source` | text | optional, at most 80 / 200 |
| `assets` | list of asset | at most 200 |
| `compositions` | list of composition | 1 to 16 |
| `migrated_from` | `{version: 1, duration_ms}` | set by `upgrade()`; keeps the exact v1 duration |

## Asset

`{id, kind, ref, sha256?, name?}`
- `kind`: `image`, `video`, `audio`, `font` or `capture` (a room screenshot, markup or browser frame).
- `ref`: a room upload `/uploads/<file>` or a kit file `motion:<path>`. URLs and file paths are refused, so a scene can never reach the network or the disk.
- `sha256` is optional while authoring. The export pipeline (S3) fills it and the frame contract's asset manifest uses it.

## Composition

`{id, name?, width, height, fps, duration, background?, seed?, props?, props_schema?, tracks}`
- `width` and `height`: 16 to 7680.
- `fps` is rational: `{num, den}`, for example `{num: 30, den: 1}` or `{num: 30000, den: 1001}`. It must work out to 1 to 240 fps.
- `duration` is in frames, at most one hour.
- `seed` (0 to 2^31-1) drives noise and random layout.
- `props_schema` is a JSON Schema subset: `{type: "object", properties: {name: {type, title?, description?, default?, enum?, minimum?, maximum?, maxLength?, format?}}, required?}`.
  - `type` is `string`, `number`, `integer` or `boolean`.
  - `format` is `color` or `multiline`.
  - Property names are `[a-z][a-z0-9_]*`.
- `props` holds default values, checked against the schema.

## Track and clip

- Track: `{id, kind, name?, muted?, hidden?, clips}`. `kind` is `visual` or `audio`. Audio tracks hold only `audio` layers, and `audio` layers sit only on audio tracks.
- Clip: `{id, name?, from, duration, offset?, transition?, layers}`. All times are in frames of the composition.
  - `from` is the start, `duration` the length, and `offset` how far into the content to start.
  - A clip may not run past the composition's end.
  - Clips on one track may overlap only by the later clip's transition duration.
- Transition, on the clip it leads into: `{kind, duration, easing?, direction?}`.
  - `kind` is `cut`, `fade`, `slide`, `wipe` or `zoom`.
  - `direction` is `left`, `right`, `up` or `down`.

## Layer

Every layer has `{id, type, name?, hidden?, keyframes?}`. Ids are unique within a composition, and clips and layers share that namespace.

Visual layers (all types except `audio` and `room`) also take transform numbers. All of these can be keyframed:

| Field | Range |
|-|-|
| `x`, `y` | -100000 to 100000 px |
| `width`, `height` | 0 to 100000 px |
| `scale` | 0 to 100 |
| `scale_x`, `scale_y` | -100 to 100 |
| `rotate` | degrees |
| `opacity` | 0 to 1 |
| `anchor_x`, `anchor_y` | 0 to 1 |
| `blur` | 0 to 500 px |

| type | Fields (required in bold) |
|-|-|
| `text` | **text**, font, font_asset, size, weight, color, align (left, center, right), line_height, letter_spacing, max_width, fit (none, shrink), reveal (0 to 1) |
| `image` | **asset** (image or capture), fit (contain, cover, fill, none) |
| `capture` | **asset** (capture or image), fit, label (real, synthetic), caption |
| `video` | **asset** (video), fit, trim (frames), rate (0.1 to 8), volume (0 to 1), muted |
| `audio` | **asset** (audio), trim, rate, volume |
| `shape` | **shape** (rect, ellipse, polygon, star, line), fill, stroke, stroke_width, radius, sides, points ([x, y] pairs), inner_radius |
| `path` | **d** (SVG path data: commands and numbers only), fill, stroke, stroke_width, progress (0 to 1, draws the path on) |
| `captions` | **cues** ([{from, duration, text}], in frames from the clip start, not overlapping), font, size, color, background, align |
| `group` | **layers** (nested, at most 8 deep) |
| `room` | **scene**: a v1 room scene's `layout` and `beats` (agents, board, browser and cards), drawn by motion.js. The scene takes its id and duration from the scene and the clip. |

Colours are `#rgb`, `#rrggbb` or `#rrggbbaa`.

A number, colour, text, enum or boolean field may instead be `{"prop": "<name>"}`, bound to a composition prop of a matching type.

## Keyframes and easing

`keyframes: {"<field>": [{frame, value, easing?}, ...]}`
- Frames count from the clip's start, so moving a clip keeps its animation. They must increase strictly and stay inside the clip.
- Animatable fields are the transform numbers plus the type's numeric and colour fields.
- `easing` applies from the previous keyframe to this one:
  - a name: `linear`, `ease`, `ease-in`, `ease-out`, `ease-in-out`, `step-start` or `step-end`;
  - `{"bezier": [x1, y1, x2, y2]}`, with x1 and x2 from 0 to 1;
  - `{"spring": {mass?, stiffness?, damping?}}`.

S1 implements these names in motion-core. The renderer evaluates frame n of a composition, never wall time.

## v1 migration

`upgrade(v1)` produces:
- one composition `main`, 1920x1080 at `{num: 30, den: 1}`, whose duration is `floor(duration_ms * 30 / 1000)` frames, the same frame count as the v1 exporter;
- one visual track `room` holding clip `main`;
- one `room` layer containing the v1 `layout` and `beats`.

`legacy()` returns the original v1 scene in v1 field order. The existing preview and exporter therefore build a byte-identical page for it and render it exactly as before. A v2 scene that uses anything beyond that single room layer gets `None`: it waits for the v2 renderer (S1, S3, S5) and never falls back silently.

## Frame contract mapping (motion-core.md amendment 1)

| Contract field | From |
|-|-|
| composition | `composition.id` plus `revision(scene, id)` |
| fps | `composition.fps` |
| frame | integer, 0 to `duration - 1` |
| seed | `composition.seed` (default 0) |
| assets | the `assets` the composition references: `{id, sha256, ref}` |
