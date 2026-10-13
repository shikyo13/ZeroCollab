# Authoring motion scenes with room_motion

For agents making product demos and motion graphics in ZeroCollab. A scene is JSON data, never code. The room validates it, renders preview frames and exports the video. Field reference: `Docs/motion-scene-v2.md`.

## The loop

1. **Brief.** Write down what the video must show, who it is for, its length, and which real captures (screenshots, markups, browser frames) it uses.
2. **Draft.** Write the scene JSON. Only v1 scenes render on today's install, so for a video you need now, start from `room_motion(action="get", scene="bundled:m01")` (v1, a room replay). The v2 example below validates and saves but does not render yet.
3. **Validate until clean.** Call `room_motion(action="validate", scene_json=...)` and fix every issue. Each issue names an exact `path`; fix that field and don't guess at others. Repeat until `ok` is true. If the result has `warnings`, read them: a valid v2 scene gets one saying it can't be rendered on this install yet.
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
| v2 that is a migrated v1 scene (a single `room` layer) | yes | yes |
| any other v2 scene | yes, with a warning | not yet. `frame` and `export` say "needs the v2 renderer" until motion-core (S1) and the layer renderers (S5) ship. |

Not available yet:
- `composition` on `frame` and `export`. Today the first composition is used, and v1 scenes have only one.
- `import_asset`. For now, reference room uploads (`/uploads/<file>`) directly in `assets`.
- `patch` (JSON Patch edits to large v2 scenes). Use whole-scene `save` with `overwrite`.

If the video is needed now, author it as v1. Author v2 only for a video that can wait for the v2 renderer and needs layers, keyframes or audio: it validates, saves and keeps, and it renders as soon as the v2 renderer lands.

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
