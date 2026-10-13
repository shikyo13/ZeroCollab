# Motion scene v2 renderer (S5)

Task 45047b9a. Code: `motion/core/scene.ts` (pure evaluation), `motion/core/random.ts` (seeded random), `motion/render2.js` (DOM renderer and page bootstrap), and the opt-in `transient` option in `motion/motion.js`. Field names are pinned in `Docs/motion-scene-v2.md`; this file pins how they are drawn.

## Pipeline

1. `ZCMotionCore.evaluate(composition, frame, props)` returns a display list: the visible clips bottom to top, each with its clip-local frame, content frame, entering and exiting transition state, and every layer field resolved for that frame (keyframes sampled, `{prop}` bindings looked up). It is pure: no DOM, clock or stored state.
2. `render2.js` builds the DOM once for every clip and layer, then draws each list onto it. It never decides timing.
3. Export and preview use the same `__seekFrame(n)`. The preview player is the v1 player, driving frames through a millisecond adapter.

## Page contract

| Item | Meaning |
|-|-|
| Scripts | `core.js`, `motion.js`, `render2.js`, inline and hash-pinned; `boot.js` is not loaded |
| `zm-options` | `render`, `reduced`, `embed`, `t` (ms), `frame_input` (required; `composition.id` selects the composition), `props` (optional overrides) |
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

## Recipes

- **Zoom into part of a capture:** set `anchor_x` and `anchor_y` to the point of interest (0 to 1 of the capture), put `x`, `y` at that same point, and keyframe `scale` from 1 up. To keep it inside a window, wrap it in a sized `group`.
- **Callout that comes and goes:** a `group` with a rounded `shape` and a `text`. Keyframe the group's `opacity` in and out, and its `y` with a spring easing.
- **Pointer to something:** a `path` with `progress` keyframed from 0 to 1.

## Determinism

- The same composition, props, seed, assets and frame always produce the same display list.
- Two cold renders of the same frame sequence produced identical PNGs: 240 of 240 frames of a layer test scene.
- Seeking in a different order can leave a few antialiased edge pixels one colour level apart. That comes from Chrome's partial repaint, not renderer state, and export always seeks 0 to N-1.
- `random(seed, key)` and `noise(seed, key, t)` are stateless hashes for jitter and random layout. Name keys by what they randomise (for example `${layer}:x`), so adding a layer never shifts another layer's values.
