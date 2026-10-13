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

Presentation (motion-design 28cb5faf, rendered by render2):
- `camera`: `{keyframes?, follow?}`, one camera over every clip on a track that isn't `fixed`.
  - `follow`: `{cursor, zoom?, idle_ms?}`. `cursor` names a `cursor` layer in this composition. `zoom` is 1 to 8 (default 2.2) and `idle_ms` 200 to 10000 (default 1200). The camera pushes in to each click's target before it lands, holds through moves and keys, and releases to the full frame after `idle_ms` with no events.
  - `keyframes`: up to 2000 of `{frame, region?, layer?, padding?, x?, y?, zoom?, rotate?, easing?}`. frame is in composition frames, non-decreasing. `region` is `[x, y, width, height]` with width and height above 0; with `layer` it's in that layer's box px. `padding` is 0 to 1 (default 0.08), `zoom` 0.05 to 40, `rotate` in degrees. Explicit keyframes override `follow` within their range.
- `motion_blur`: `{shutter?}`, 0 to 360 degrees (default 180). Velocity blur, single pass.
- `backdrop`: `studio` (dark) or `paper` (light and warm; brand text styles switch to ink colours on it).
- `font_asset`: a `font` asset id, the default for text. Font asset ids ending in `-wNNN` (100 to 900) register as weights of one family: `zc-sans-w700` is family `zc-sans` at weight 700.

## Track and clip

- Track: `{id, kind, name?, muted?, hidden?, fixed?, clips}`. `kind` is `visual` or `audio`. Audio tracks hold only `audio` layers, and `audio` layers sit only on audio tracks. A `fixed` track ignores the camera (titles, lower thirds, badges).
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
| `text` | **text**, font, font_asset, size, weight, color, align (left, center, right), line_height, letter_spacing, max_width, fit (none, shrink), reveal (0 to 1), style (display, title, headline, subtitle, kicker, lt-name, lt-role, caption: a brand style that explicit fields override; display is a 140 px headline), stagger (`{from?, every?, frames?, rise?}`: words enter one after another with expo-out; from is the clip frame to start, 0 to the clip's last frame, default 0; every is frames between words, 0 to 120, default 4; frames is each word's entrance, 1 to 240, default 14; rise is px, 0 to 400, default 26; not keyframable) |
| `image` | **asset** (image or capture), fit (contain, cover, fill, none), frame (`window`) |
| `capture` | **asset** (capture or image), fit, label (real, synthetic), caption, frame (`window`) |
| `video` | **asset** (video), fit, trim (frames), rate (0.1 to 8), volume (0 to 1), muted, frame (`window`) |
| `audio` | **asset** (audio), trim, rate, volume, role (`voice`, `music` or `sfx`), loop, duck (music only: `{against, gain?, attack_ms?, release_ms?}`, see "Sound" below) |
| `voiceover` | **text** (at most 4000), **voice** (a registry id such as `piper:en-us-ljspeech-high`, never a path or URL), rate (0.5 to 2), volume (0 to 1), captions (`{enabled}`). Narration synthesized once by a local voice; see "Sound" below. |
| `shape` | **shape** (rect, ellipse, polygon, star, line), fill, stroke, stroke_width, radius, sides, points ([x, y] pairs), inner_radius |
| `path` | **d** (SVG path data: commands and numbers only), fill, stroke, stroke_width, progress (0 to 1, draws the path on) |
| `captions` | **cues** ([{from, duration, text}], in frames from the clip start, not overlapping), font, size, color, background, align |
| `group` | **layers** (nested, at most 8 deep), frame (`window`) |
| `room` | **scene**: a v1 room scene's `layout` and `beats` (agents, board, browser and cards), drawn by motion.js. The scene takes its id and duration from the scene and the clip. |
| `ui` | **component** (`board` or `task-card`), **label** (`scripted`), **data**, states, task (the task id a `task-card` shows; required for it). Real ZeroCollab components (frontend `motion/ui.js`, task d7fe16c8) drawn from scripted data, frame (`window`). See "ui layers" below. |
| `cursor` | **source** (a `video` layer in the same clip, groups included), **events**, **viewport** (`[width, height]` CSS px), **fps** (the recording's `{num, den}`), size (4 to 400), color. The pointer recorded with that clip, drawn in its box: see "cursor layers" below. |

Colours are `#rgb`, `#rrggbb` or `#rrggbbaa`.

### ui layers

A `ui` layer mounts the product's own components, such as the Board's Lane, TaskCard and SpeechBubble, on scripted data. They are never fed a live room's data. `label: "scripted"` records that provenance in the scene. The layer takes the normal transform and its keyframes; its data is never interpolated.

- **data** is the complete snapshot shown from the clip's content frame 0.
- **states** is `[{frame, data}]`, up to 120 entries. Each `data` is a complete snapshot that replaces the previous one from that clip content frame on (step semantics). Frames are whole numbers from 1, strictly increasing. The component times its own transitions (a card moving, a bubble opening) from the frame age since the state that changed it, so any frame renders the same whatever was rendered before.

A snapshot (`data`, and each state's `data`):

| Field | Type and limits |
|-|-|
| **epoch_seconds** | number: the clock the components use for "2m ago" |
| project_title | text, at most 80 characters |
| **agents** | up to 16 of `{id, label, color}`: id as a scene id, label at most 40 characters, color `#rrggbb` |
| **tasks** | up to 40 of `{id, title, status, owner, labels, priority, next_action, execution_state, updated}`. Required: id (scene id), title (at most 120), status (`open`, `working`, `blocked`, `review` or `done`). owner is an agent id or empty; labels are up to 4 of at most 24 characters; priority 1 to 5; next_action at most 300; execution_state is one of the Board's states (`executing`, `queued`, `waiting_on_human`, `needs_attention`, `blocked`, `waiting_on_dependency`, `in_review`, `idle`, `done`); updated is epoch seconds. |
| follow | up to 16 live bubbles `{agent, task, text, tool, open, scroll, move}`. agent and task must exist in this snapshot; text is the reply (at most 4000), tool a label (at most 60), open a boolean, scroll a number of pixels or `"end"`, move `{from, to}` statuses for a card that just moved. |

Field names here are snake_case like the rest of the scene. The renderer maps them to the components' own props.

### cursor layers

A `cursor` layer is the pointer recorded with a screen recording (desktop's recorder sidecar), drawn by render2 in the box of its `source` video layer: viewport px map to the box by box width / recording width, so keep the video box at the recording's aspect. Nothing is burned into the recording itself.

- **events**: up to 20000 of `{frame, type, x, y, button?, rect?}`, frames in the recording's own clock and non-decreasing.
  - `type` is `move`, `down`, `up`, `click`, `scroll` or `key`. `x` and `y` are required except on `key` markers, which carry no key name or text, so typed secrets can't be rebuilt.
  - `button` is `left`, `middle` or `right`. `rect` is the clicked target `[x, y, width, height]` in viewport px.
- **viewport** and **fps** are copied from the recording's sidecar by `import_asset`: viewport px map to the box by box width / viewport width (correct at device scale 2), and event frames convert to time on the recording's clock, then to composition frames through the video's trim and rate.
- The cursor's own transform fields are small offsets inside the source box: x and y nudge it, scale sizes the pointer, opacity fades it.
- The composition camera's `follow` uses these events to zoom to each click.

### Sound

Proposed by systems (task 6e90b5b7, `Docs/motion-audio.md`), which owns synthesis and the mix. Until the mix ships, `export` refuses sound (audio, voiceover, or unmuted video) and `room_motion` validate and save warn about it first.

- `audio` and `voiceover` layers sit only on audio tracks, and audio tracks hold only them. Neither takes the transform.
- Sound plays at one `rate` from one `trim` per clip: only `volume` can be keyframed. Source time in seconds is (trim + (local frame + offset) * rate) * fps.den / fps.num, using the composition's fps.
- `role` (default `sfx`) only groups layers for ducking. `loop` repeats the source within the clip and cuts it at the clip's end.
- `duck` sits on a `music` layer: `against` names 1 to 16 voice layers (a `voiceover`, or an `audio` layer with role `voice`), `gain` 0 to 1, `attack_ms` 0 to 5000 and `release_ms` 0 to 10000. There's no automatic global mix.
- `voiceover` text is synthesized once at preparation, not per frame. Text that doesn't fit its clip is refused with the duration it needs; words are never cut. An unavailable voice is refused, never swapped. Caption timing comes from the engine's word or phoneme alignment, and the caption layer format is agreed with motion-design before it becomes schema.

A number, colour, text, enum or boolean field may instead be `{"prop": "<name>"}`, bound to a composition prop of a matching type.

## Keyframes and easing

`keyframes: {"<field>": [{frame, value, easing?}, ...]}`
- Frames count from the clip's start, so moving a clip keeps its animation. They must increase strictly and stay inside the clip.
- Animatable fields are the transform numbers plus the type's numeric and colour fields.
- `easing` applies from the previous keyframe to this one:
  - a name: `linear`, `ease`, `ease-in`, `ease-out`, `ease-in-out`, `step-start`, `step-end`, `expo-out`, `expo-in-out`, `premium`, `spring-soft` or `spring-snappy`;
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
