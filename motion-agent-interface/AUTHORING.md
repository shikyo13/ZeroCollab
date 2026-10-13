# Authoring motion scenes with room_motion

For agents making product demos and motion graphics in ZeroCollab. A scene is JSON data, never code. The room validates it, renders preview frames and exports the video. Field reference: `Docs/motion-scene-v2.md`.

Craft, pacing and ready-made recipes: `Docs/motion-skills/SKILL.md` and `RECIPES.md`.

## The loop

1. **Brief.** Write down what the video must show, who it is for, its length, and which real captures (screenshots, markups, browser frames) it uses.
2. **Draft.** Write the scene JSON in v2, starting from the example below or a template in `Docs/motion-skills/templates/`. Use v1 (`room_motion(action="get", scene="bundled:m01")`) only for a plain room replay.
3. **Validate until clean.** Call `room_motion(action="validate", scene_json=...)` and fix every issue. Each issue names an exact `path`; fix that field and don't guess at others. Repeat until `ok` is true. Then read any `warnings`: they name what export will refuse, such as sound in a composition, while the scene is still valid to save.
4. **Save.** Call `room_motion(action="save", scene_json=...)`, which returns `project:<id>`. Saving again needs `overwrite: true`.
5. **Look at it.** Call `room_motion(action="frame", scene="project:<id>", t_ms=[0, 3000, 8000])`. It returns one PNG per time plus a contact sheet. These are exactly the frames the video will contain.
6. **Show the team.** Post the contact sheet with `room_markup` (mark anything you are unsure about) and ask for a review on your task.
7. **Export.** Call `room_motion(action="export", scene="project:<id>")`, then `export_status`. The default is WebM; see Export formats below for the others. Only one export or frame render runs at a time. If it's busy, wait and try again.
8. **Deliver.** Once `export_status` shows a WebM job `done`, call `room_motion(action="publish", job_id=..., task_id=..., note=...)`. The video is posted to this chat and attached to the task, and it plays inline in both places. That's where Zero watches it, approves it or asks for changes. There's no separate video page.

## Export formats

Pass `profile` to `export`. The room checks each profile against its own FFmpeg. A profile it can't encode is refused with the reason, and it is never swapped for another.

| profile | File | Use it for | publish |
|-|-|-|-|
| `webm` (default) | .webm, VP9 | the demo video people watch in chat and on tasks | yes, plays inline |
| `vp9-alpha` | .webm, VP9 with transparency | overlays and lower thirds placed over other footage | yes, plays inline |
| `av1` | .webm, AV1 | a smaller file at the same quality; slower to encode | yes, plays inline |
| `prores4444` | .mov, ProRes 4444 with alpha | handing to a video editor | no, share its download link |
| `gif` | .gif, looping | a short silent loop for docs or a README | no, share its download link |
| `png` | .png, the first frame only | a poster image; for a chosen moment use `frame` instead | no, share its download link |
| `png-sequence` | .zip of numbered PNG frames plus frames.json | frame-exact editing elsewhere | no, share its download link |

For a profile that doesn't publish, `export_status` gives the job's `video_url`; add `?download=1` to download it.

## What renders today

| Scene | Validates | Frames and video |
|-|-|-|
| v1 (layout plus beats) | yes | yes |
| v2: text, image, capture, video, shape, path, captions, group and room layers, with keyframes and transitions | yes | yes |
| v2 with sound (an `audio` layer, or a `video` layer that isn't `"muted": true`) | yes, with a warning | frames yes; `export` refuses it until audio mixing ships |

`frame` and `export` draw the same frame through the same seek, so a preview frame is exactly that frame of the video.

Not available yet:
- Sound in exports. Mute video layers and leave out audio layers.
- Rendering a composition other than the first in place. Use `variant` (below), which saves the one you choose as its own scene.

## Templates, edits and real assets

- **`variant`**: re-render a template without editing it. `room_motion(action="variant", scene="project:launch", new_id="launch-square", composition="square", props={"title": "Now in 1:1"})` saves `project:launch-square` with only that composition, its props overridden. Props are checked against the composition's `props_schema`, so an unknown name or a wrong type comes back as an issue and nothing is saved. Then `frame`, `export` and `publish` it as usual.
- **`patch`**: change part of a large saved scene with RFC 6902 JSON Patch, for example `ops=[{"op": "replace", "path": "/compositions/0/props/title", "value": "Shipped"}]`. Supported ops are `add` (`-` appends to a list), `remove`, `replace`, `move`, `copy` and `test`. If any op fails, or the result doesn't validate, nothing is written. A patch can't change the scene id.
- **`import_asset`**: use a real room upload, such as a `room_browser` screenshot url or an image someone dropped in chat. `room_motion(action="import_asset", url="/uploads/1a2b3c4d.png", asset_id="board", kind="capture", scene="project:launch")` adds `{id, kind, ref, sha256}` with the file's real hash. For images it also returns `width`, `height` and `aspect`, plus `fit`: the `contain` size in the scene's first composition and the `fill_scale` that fills the frame with no black bars. Wide room screenshots are often 2.4 to 2.9:1, so plan a zoom or pan to the region that matters, not a letterboxed full view.

One page can carry at most 64 MB of assets.

## Inspect a video against the bar

Judge an export by measuring it, not from a few stills. `inspect` reads a video the room already shares, never a raw path or web URL:
- `job_id` for an export;
- `url` for a `/uploads/...` video;
- a pasted original as `fileref:<id>` or its `/api/files/ref/<id>/<name>` link.

`room_motion(action="inspect", job_id="0123456789ab", vs="fileref:3f9c0a1b2c3d4e5f", sheets=["contact", "motion", "dense"], start=4, end=10, fps=6)`

- **`analysis`**:
  - `probe`: resolution, fps, codecs and duration;
  - `cuts` and soft `transitions`, as times in seconds;
  - `shots`, `mean_shot_s` and `cuts_per_minute`;
  - `mean_motion` and `moving_share`;
  - `holds` and `longest_hold_s`, which are still stretches of 1 s or more;
  - `audio`: mean and max dB, silences, `integrated_lufs` and `loudness_range_lu`.

  Lists over 100 entries are cut to the first 100, with a `<name>_total` count. The first 10 minutes are measured.
- **`compare`** (with `vs`): the headline numbers of both videos side by side. `vs` takes the same forms, or `export:<job_id>`.
- **`sheets`** (default `contact` and `motion`), each returned as a `/uploads` PNG and shown to you:
  - `contact`: 8 frames across the video.
  - `dense`: every `1/fps` seconds from `start` to `end`, which is the one for small icon and cursor motion. `fps` is up to 10 and defaults to 4. There are at most 48 tiles, so narrow the range or lower `fps`.
  - `motion`: movement over time, with cuts as yellow lines. Holds read flat, and camera moves read as humps.
  - `shots`: the middle frame of each of the first 30 shots, with their ranges. `truncated` is true when the video has more.
  - `frames`: one PNG per `t_ms` (up to 6).

Results are cached by file, so asking again for other sheets of the same video doesn't measure it again. A long video can take a minute or two.

## v1: room replay vocabulary

- `layout` places up to 12 agents, an optional board with up to 6 columns, an optional browser panel, and up to 20 cards.
- `beats` (up to 400) run at `at_ms` for `duration_ms`.
- Beat kinds: `agent` (actor, label, state), `tool`, `handoff` (actor to target), `board` (moves card `label` to column `state`), `browser` and `caption`.
- Every string is shown as plain text. Keep labels short: at most 120 characters, and one idea per beat.

## v2: compositions, tracks, clips, layers

```json
{"version": 2, "id": "launch-teaser",
 "assets": [{"id": "board", "kind": "capture", "ref": "/uploads/1543d555.png"}],
 "compositions": [{"id": "main", "width": 1920, "height": 1080, "fps": {"num": 30, "den": 1}, "duration": 180,
   "props_schema": {"type": "object", "properties": {"title": {"type": "string", "maxLength": 80}}},
   "props": {"title": "Spec to working app"},
   "tracks": [{"id": "v", "kind": "visual", "clips": [{"id": "intro", "from": 0, "duration": 180, "layers": [
     {"id": "title", "type": "text", "text": {"prop": "title"}, "size": 96, "color": "#ffffff", "x": 960, "y": 200, "anchor_x": 0.5,
      "keyframes": {"opacity": [{"frame": 0, "value": 0}, {"frame": 20, "value": 1, "easing": "ease-out"}]}},
     {"id": "shot", "type": "capture", "asset": "board", "label": "real", "x": 320, "y": 320, "width": 1280, "height": 720,
      "keyframes": {"scale": [{"frame": 10, "value": 0.94}, {"frame": 40, "value": 1, "easing": {"spring": {"damping": 14}}}]}}
   ]}]}]}]}
```

Rules that most often cause issues:
- **Times are frames.** Composition `duration`, clip `from`, `duration` and `offset`, and caption cues are all in frames. Keyframe `frame` counts from the clip's start.
- **Assets** are only room uploads (`/uploads/<file>`) or kit files (`motion:<path>`). Never URLs or disk paths. Use real captures with `"type": "capture"` and set `label` to `real` or `synthetic`.
- **Ids** are lowercase, digits and hyphens, and are unique across a composition's clips and layers.
- **Audio** layers go only on `audio` tracks.
- **Props:** use `{"prop": "<name>"}` to bind a field to a composition prop of the same type, so the same scene can be re-rendered with new text.
- **Easing:** a name (`linear`, `ease`, `ease-in`, `ease-out`, `ease-in-out`, `step-start`, `step-end`), `{"bezier": [x1, y1, x2, y2]}` or `{"spring": {mass, stiffness, damping}}`.

## Good demo habits

- Use the room's real captures: shared-browser frames, screenshots and markups. Label synthetic content as synthetic.
- Keep one message per beat or clip, with text large enough to read at 1080p (48 px or more for body text).
- Check frames at the start, the busiest moment and the end before exporting.
- Don't invent fields. Validation refuses unknown fields by name.
