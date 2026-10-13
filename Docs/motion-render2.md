# Motion scene v2 renderer (S5, S7)

Task 45047b9a (S5) and 28cb5faf (S7, demo-grade camera, footage and type). Code: `motion/core/scene.ts` (pure evaluation), `motion/core/camera.ts` (camera views and affine maths), `motion/core/cursor.ts` (recorded pointer and follow shots), `motion/core/timemap.ts` (source time for footage), `motion/core/audio.ts` (sound plan), `motion/core/random.ts` (seeded random), `motion/render2.js` (DOM renderer and page bootstrap), `motion/brand/fonts` (Inter, OFL), and the opt-in `transient` option in `motion/motion.js`. Field names are pinned in `Docs/motion-scene-v2.md`; this file pins how they are drawn.

## Pipeline

1. `ZCMotionCore.evaluate(composition, frame, props)` returns a display list: the visible clips bottom to top, each with its clip-local frame, content frame, entering and exiting transition state, and every layer field resolved for that frame (keyframes sampled, `{prop}` bindings looked up). It is pure: no DOM, clock or stored state.
2. `render2.js` builds the DOM once for every clip and layer, then draws each list onto it. It never decides timing.
3. Export and preview use the same `__seekFrame(n)`. The preview player is the v1 player, driving frames through a millisecond adapter.

## Page contract

| Item | Meaning |
|-|-|
| Scripts | `core.js`, `motion.js`, `render2.js`, inline and hash-pinned; `boot.js` is not loaded |
| `zm-options` | `render`, `reduced`, `embed`, `t` (ms), `frame_input` (required; `composition.id` selects the composition), `props` (optional overrides), `voice_captions` (optional: `{voiceover layer id: timing sidecar}`, inlined by the server after synthesis) |
| `window.__audioPlan()` | the composition's sound plan (see "Sound plan and voice captions"), computed by the same core as the picture |
| `window.ZCMotionUI` | optional: frontend's `motion/ui.js`, loaded before `render2.js`; required only by `ui` layers |
| `window.__scene` | `{version: 2, id, composition, fps, duration (frames), duration_ms, width, height}` |
| `window.__seekFrame(n)` | Promise resolving with the frame once its pixels are ready (videos seeked, room seeked, two animation frames). Requests made while a frame draws coalesce to the newest. |
| `window.__seek(ms)` | the frame at `ms` (`frameAtMilliseconds`), clamped to `[0, duration - 1]` |
| `window.__ready` | true after fonts, image decode and video data have loaded and the first frame is drawn |
| `window.__error` | plain text |
| CSP | unchanged for text, images, captures and fonts (font assets load from bytes through `FontFace`, so they need no `font-src`); a video asset needs `media-src data:` |
| Stage | `#frame` and `#stage` are sized to the composition; the export viewport is width x height |

## Order and time

- Later tracks draw above earlier ones. Within a track, a clip entering with a transition draws above the clip it replaces.
- Audio tracks and `audio` layers are not drawn; the exporter mixes them.
- Keyframes and caption cues use the clip-local frame, as validated. `offset` shifts embedded content: a room scene's beats and a video's time.

## Transitions

A transition plays across both clips, so it works with transparent clips and on any track. `direction` is the direction of travel.

| kind | Entering clip | Clip it replaces |
|-|-|-|
| `fade` | opacity 0 to 1 | opacity 1 to 0 (a cross dissolve) |
| `slide` | moves in from the far side | is pushed out the near side |
| `wipe` | uncovered from the trailing edge | covered from the same edge |
| `zoom` | scales 0.94 to 1 and fades in | fades out |

The default easing is `ease-in-out`. With reduced motion, `slide`, `wipe` and `zoom` become fades.

## Layer defaults

| Layer | Defaults and behaviour |
|-|-|
| all visual | `x`, `y` place the anchor point (default anchor 0, 0, the top left); `rotate` and `scale` turn about the anchor; `blur` in px |
| `text` | 48 px, weight 400, `#e8ecee`, line height 1.2, the scene font. Unsized text is as wide as its content, wrapping only at `max_width` or newlines. `reveal` shows a fraction of the characters while the rest keeps its space, so lines never re-wrap. `fit: shrink` scales the size down until the text fits `width` (or `max_width`) and `height`. |
| `image`, `capture`, `video` | the full composition size unless sized; `fit` defaults to `contain` |
| `capture` | `label: synthetic` shows a **Simulated** badge, and `caption` shows a caption strip. Both belong to the frame, not the picture, so zooming into a capture never scales them away. |
| `group` | with both `width` and `height` it clips its children: a viewport for zooming and panning a capture inside a window |
| `shape` | 100 x 100. A `line` defaults to a 4 px accent stroke, other shapes to an accent fill. `polygon` takes `sides` or `points`; `star` takes `sides` and `inner_radius` (0.5). |
| `path` | coordinates in composition px; 4 px accent stroke; `progress` draws it on |
| `captions` | centred 60 px above the bottom; 40 px white on `#000000b3`; at most 80% of the width |
| `room` | the v1 room scene on its own 1920 x 1080 stage, scaled so its content fills the composition's 5% safe area (at most 2.5x). Handoff arrows and chips are transient here: they fade 1.2 s after the handoff lands (v1 pages are unchanged). |
| `text` `style` | a brand style (below) supplies defaults; anything the layer sets itself wins. `stagger` brings words in one after another (expo-out, each rising `rise` px over `frames`, one every `every` frames from clip frame `from`); the final layout is kept, so nothing re-wraps mid-move. |
| `frame: window` | on `image`, `capture`, `video`, `ui` and sized `group`: rounded corners, a hairline and a soft shadow, so the app reads as a product shot. A sized window group clips its children, which makes it a viewport. |
| `video` `speed` | retimes the picture only (see "Recorded footage"); the schema requires `muted: true` with it |
| `cursor` | the recorded pointer, drawn in its source video's box (see "Recorded footage") |
| `redact` | an opaque box over a secret in footage: the transform's box filled with `color` (default `#1b2023`, any alpha dropped). Its own `opacity` and `blur` are never applied, its corners are square, and it sits above its siblings within its own group. Put it in the footage's group so it moves and zooms with it; effects on that group or the camera act on footage that is already covered. |
| `ui` | `ZCMotionUI.mount(host, layer, context)` once at build, then `handle.seek(context)` every frame (awaited like a video seek), `handle.dispose()` on pagehide. `context` is `{frameContext, composition, clip: {id, from, duration, frame, contentFrame}, values}`; a seek must be a pure function of it. |

## Brand kit

| Style | Size, weight | Use |
|-|-|-|
| `display` | 140, 800 | one or two hero words |
| `title` | 96, 800 | opening and closing lines |
| `headline` | 64, 800 | a shot's one-line claim |
| `subtitle` | 40, 400 | the line under a title |
| `kicker` | 24, 600, caps | the label above a headline |
| `lt-name`, `lt-role` | 40, 600 and 28, 400 | lower third |
| `caption` | 36, 600, on a plate sized to its text | burned-in caption |
| `badge` | 22, 600, caps, on an accent pill | "Example project", "Time-lapse x8", "Scripted data" |

- **Type:** Inter v4.1 (SIL OFL 1.1, `motion/brand/fonts`, receipt with source hashes), loaded from bytes through `FontFace`. Font asset ids ending in `-w400`, `-w600` or `-w800` are weights of one family, so `font_asset: 'zc-sans-w400'` plus a style's weight picks the right file. Product footage keeps the app's own typography; Inter is for authored overlays.
- **Backdrops:** `studio` (dark, a soft teal light top left) and `paper` (light and warm). On `paper`, brand styles switch to ink colours so contrast holds; captions and badges keep their plates.

## Camera

- One transform over every clip on a track that isn't `fixed`. Fixed tracks (titles, lower thirds, labels) and each clip's honesty HUD (the Simulated badge and caption strip) sit above it and never move with the shot.
- `camera.keyframes`: each key is a framing at a composition frame: a `region` `[x, y, w, h]` (composition px, or the named `layer`'s own box px, followed through that layer's transforms) fitted with `padding`, or explicit `x`, `y` (the point at the centre), `zoom` and `rotate`. `easing` shapes the move into that key, including `premium`, `expo-out`, `expo-in-out`, `spring-soft` and `spring-snappy`.
- Zoom blends in log space about the point both framings share, so a push-in feels even and nothing drifts sideways. That point ignores rotation: a key pair that also rotates blends its centre on the same curve but does not hold one point still.
- `camera.follow: {cursor, zoom?, idle_ms?}` adds keys from a cursor layer's recording (below), outside the range the explicit keys cover; explicit keys win inside it.
- **Motion blur is off unless a composition sets `motion_blur`, and product footage never sets it** (design aba6b24a): even capped, it softens UI text and on a zoom it reads as defocus. When set, it comes only from camera motion between frames, is capped at 2.5 screen px, and is zero once the camera lands. It is for decorative graphics.

## Recorded footage

- **One set of props.** `compositionProps(composition, overrides)` merges the composition's defaults under the caller's overrides once per pair. The frame, every footage clock, the camera and its follow keys, and the sound plan all read that object, so a prop-bound `trim`, `rate` or `speed` moves the picture and the camera together.
- **One clock.** `timeMap(video)` integrates `speed` (keyframable, default `rate`) over the clip's content frames from `trim`, trapezoid by frame and cached per layer, with an exact inverse that returns the earliest frame showing a time (the first frame of a freeze). The picture, its cursor and the follow camera all read it, so a speed ramp keeps them together. Sound can't follow a ramp, so a video with `speed` must be muted; `audioPlan` refuses one that isn't.
- **Cursor.** Events are in the recording's viewport px and frames. Only pointer events (move, down, up, click, scroll) place the pointer; a key never does. A critically damped spring chases each target at a fixed 1/240 s step, so every frame reads the same point however it is seeked. When the recording jumps to a point it then presses (scripted input does this a frame before the press), the move starts up to 0.3 s earlier, so the drawn pointer lands on the press rather than after it. A click draws a ring that opens and fades over 0.5 s.
- **Follow.** Activity (clicks, scrolls, keys) groups into bursts; gaps under `idle_ms` join a burst. Each burst frames its first click's `rect`, or a `zoom` box around it, from 0.7 s before to `idle_ms` after, then releases; bursts close together pan instead of zooming out and in, the hold ending where the next push starts so the pan always takes about 0.6 s. Bursts outside what the clip shows (a trimmed head, past its end) are dropped. A click's ring starts on its `down`; a `click` starts one only when no `down` came in the last 0.5 s.
- **Source density.** A capture should not be shown above about 1.15 screen px per recorded px. Record at device scale 2 for shots that zoom in; enlarging a 1x recording softens its text however the camera moves.

## Sound plan and voice captions

- `audioPlan(composition, props)` lists every audible layer (`audio`, `voiceover`, and `video` that isn't muted or silent), with the same hidden and muted rules as the picture: its clip timing, trim, rate, role, loop, ducking and a volume sample for every local frame, clamped to 0 to 1 (a spring-eased key may overshoot in the picture, not in the mix). The mixer owns synthesis and PCM.
- Voice captions draw one HUD caption per narrated voiceover whose captions are enabled and whose timing sidecar the server inlined. Pages hold about two lines and turn at sentence ends, and stay 0.6 s after the last word. `word` timing highlights the spoken word, `phrase` times whole pages, and `none` draws nothing, so no timing is ever invented.

## Recipes

- **Zoom into part of a capture:** set `anchor_x` and `anchor_y` to the point of interest (0 to 1 of the capture), put `x`, `y` at that same point, and keyframe `scale` from 1 up. To keep it inside a window, wrap it in a sized `group`.
- **Callout that comes and goes:** a `group` with a rounded `shape` and a `text`. Keyframe the group's `opacity` in and out, and its `y` with a spring easing.
- **Pointer to something:** a `path` with `progress` keyframed from 0 to 1.

## Determinism

- The same composition, props, seed, assets and frame always produce the same display list.
- Two cold renders of the same frame sequence produced identical PNGs: 240 of 240 frames of a layer test scene.
- Seeking in a different order can leave a few antialiased edge pixels one colour level apart. That comes from Chrome's partial repaint, not renderer state, and export always seeks 0 to N-1.
- `random(seed, key)` and `noise(seed, key, t)` are stateless hashes for jitter and random layout. Name keys by what they randomise (for example `${layer}:x`), so adding a layer never shifts another layer's values.
