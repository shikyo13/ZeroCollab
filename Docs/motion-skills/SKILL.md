---
name: product-demo-motion
description: Make a ZeroCollab product demo or motion graphic with room_motion, from a one-paragraph brief to a published video. Use when someone asks for a demo, teaser, feature highlight, title card, lower-third or "show what the team did" video.
---

# Product demos with room_motion

`motion-agent-interface/AUTHORING.md` covers the tool loop and the scene fields. This skill covers the
craft: turning a brief into a video people understand in one viewing, and checking it before anyone sees it.
Read both. Recipes are in `RECIPES.md` next to this file.

## 1. Before any JSON: the brief

Write these five lines first, in your task check-in. If one is missing, ask, don't invent it.

| Line | Example |
|-|-|
| Viewer | A developer who has never seen ZeroCollab |
| The one promise | You ask once; a team of agents builds, reviews and checks it |
| Proof on screen | The real request, then the finished result clicked through (a card reaching Done alone proves nothing) |
| Length and shape | 20 s, 16:9, no voice |
| Source material | Real recordings (`fileref:` takes) and captures, or "synthetic, labelled" |

One promise per video. A second promise is a second video.

## 2. Storyboard as a table, then scene

Write the shots as a table before writing the scene. One row is one idea.

| # | From | To | On screen | Text | Motion meaning |
|-|-|-|-|-|-|
| 1 | 0.0 s | 2.5 s | Empty board | You ask once: "Add a dark mode toggle." | none, let it read |
| 2 | 2.5 s | 8.0 s | Frontend works | (tool label) | ring ticks = working |

Then map rows to beats (v1) or clips (v2). Keep the table in the check-in; reviewers compare against it.

## 3. Pacing

- **Reading time:** on-screen text needs about 0.3 s per word plus 1 s, and never less than 2 s.
  "You ask once: Add a dark mode toggle" is 7 words, so 3.1 s.
- **One big movement at a time.** The viewer's eye follows one thing. Stagger simultaneous moves by at least 0.4 s.
- **Hold the result.** The final state (card in Done, finished page) stays still for at least 2 s.
- **First 2 s carry the promise.** Someone who stops watching at 2 s should still know what this is.
- **20 s is about 4-6 ideas.** 30 s is 6-8. More than that is a list, not a demo.
- **Keep it moving, but calm** (Zero's bar, `Docs/research/demo-references/quality-bar.md` Q1 and Q11):
  - something moves at least half the time, and no still hold lasts over 3 s;
  - easy to follow, never bouncy or hyper: no easing that overshoots on product footage or the camera. Use
    `ease-in-out`, `expo-out` or `premium`; keep `spring-*` for small decorative graphics;
  - each camera move takes 0.6-1.2 s, and the view holds at least 0.8 s after every click;
  - clicks and typing play at real speed. Only waiting is sped up, and then a `badge` text such as
    "Time-lapse x8" sits on a `fixed` track for as long as the speed-up lasts.

## 4. Typography and layout (1920x1080)

- Use the brand text `style`s (Docs/motion-render2.md, Brand kit) instead of hand-set sizes: `display` or
  `title` for 1-4 word headlines, `subtitle` for the one line under them, `kicker` above, `badge` for
  "Example project" or "Time-lapse". Put them on a `fixed` track so the camera never moves them.
- Body text 48 px or larger. Captions sit in the lower third, inside a 96 px safe margin.
- In product footage the focal text (task title, names, evidence) must read at 1080p: at least 32 px tall
  after framing and zoom, in the app's own typography.
- No more than about 12 words on screen at once outside a caption.
- Use the same short names chat uses (Frontend, Design, QA), never job titles or internal ids.
- Room screenshots are usually much wider than 16:9 (2.4-2.9:1). With `fit: contain` they letterbox and
  their text is small. Frame the part that matters with a composition `camera` keyframe that names the
  capture's `layer` and a `region` in its own pixels, and check it with `frame`.
- Label synthetic content as synthetic in the scene `source` and, if it could be mistaken for real, on screen.
- On a `capture` layer, set `"label": "synthetic"` for anything that isn't a real room capture (mockups,
  edited or staged screenshots). The renderer then draws a fixed "Simulated" badge, which zooming can't
  scale away. The templates default to `"real"`: change it whenever you drop in a mockup.
- **Browser chrome and the OS are personal.** Bookmarks, tabs, the address bar, other windows and the
  taskbar stay out of every frame. Frame inside the app viewport: put the capture in a sized `group` that
  shows only the app area, then check the top and bottom edges with `frame`.

## 4b. Real product footage (recorder, cursor, follow camera)

AUTHORING.md "Record real product footage" has the calls. The craft:
- **Plan the take before recording.** One take per beat, 10-30 s, driven slowly: pause about a second on
  each target before clicking, so the follow camera has something to land on. Short takes stay under the
  64 MiB page limit and are easy to re-record.
- **Plan for device scale 1.** Takes record at device scale 1 (1280x800 CSS px) today, so plan wide framing
  and keep follow `zoom` at about 1.6 or less. Footage shown above about 1.15 screen px per recorded px goes
  soft, and no camera move fixes that. Device scale 2 is pending desktop; until then, a shot that must zoom
  further is softer, whatever the camera does.
- **Inspect the raw take first** (`inspect` with `url="fileref:<id>"`), so a bad take is caught before it is
  built on.
- **Use what `import_asset` returns.** It gives a muted `video` layer and a `cursor` layer, plus the take's
  `width`, `height` and `aspect`. Size the video box to that aspect, and put the cursor layer in the same
  group as the video at the same x and y: the pointer is drawn in the video's box. A sized
  `frame: "window"` group around both clips them together. Then set `camera.follow` to the cursor layer.
  Add explicit camera keyframes only where follow frames the wrong thing.
- **Present it as the product.** Give the footage `frame: "window"` on a `studio` (dark) or `paper` (light)
  backdrop. Never set `motion_blur` on product footage, because it reads as defocus on UI text.
- **Hide secrets with `redact`, never blur.** Keys, emails and account names get a `redact` box inside the
  footage's group, so it moves and zooms with the footage. Check it on every frame where the secret shows.
- **Speed ramps:** keyframe the video's `speed` only over waiting (the video must be `muted`), with the
  time-lapse badge on screen while it runs. Return to speed 1 at least 0.7 s before the next click, and
  don't start the next ramp until at least `idle_ms` (default 1.2 s) after the last click. The cursor's
  travel and the follow hold run on recording time, so they shrink on screen while speed is above 1, and
  the 0.8 s hold collapses.

## 5. Motion grammar: one movement means one thing

| Movement | Means | Use for |
|-|-|-|
| Ring or bar progresses | A tool is running | Work happening |
| A card or preview grows out of a person | That person produced it | Screenshots, pages, annotations |
| A baton travels from A to B | Handoff (review, follow-up, transfer) | Passing work on |
| Comet travels from A to B | A mentions B (@mention) | Asking a teammate something |
| People gather under a task chip | They're working on the same thing | Collaboration on one task |
| Ring turns red and stops, head shakes once | A turn failed | Honest failure shots |
| Gold pulse rises to the You area (top-left); on the Board, a gold card edge | It needs you | Questions, approvals |
| Card glides between columns | Status changed | Board progress |

Never animate something just to fill time. If a movement can't be named from this table, cut it.
Reduced motion: export once with `reduced: true` and check that the story still reads.

## 6. Room scenes (the team at work)

A room is the drawn team-and-board view. Use it as a `room` layer inside a v2 scene, with v2 text clips
for titles and the call to action around it. A plain v1 scene (layout plus beats, no v2) is only for a
quick replay with no titles.

Learned from the first end-to-end runs (QA b5cc1270, S5 renders of `project:zc-demo-20s`):
- **Agent states never end on their own.** Add an `agent` beat with state `Done` or `Waiting` when each
  person's part is over, or they show "Running" until the end. Give every agent who runs a tool an `agent`
  beat with state `Running` first, or they read "Available" while working.
- **Show the ask.** Open the room with a `caption` beat that quotes the request, so the work has a reason.
- **Keep handoffs few** (2-3 in 20 s). In a v2 `room` layer, handoff chips fade after the handoff lands and
  the room is scaled to fill the frame. In a plain v1 scene, chips stay on screen and the room draws at its
  authored coordinates (the old 20 s demo layout used about 60% of the frame).
- `board` beats move a card by id (`label`) to a column name (`state`); both must exist in the layout.
- Beat times (`at_ms`) count from the start of the room's clip, not the start of the video.

## 7. Check before you show

Render frames at the first second, the busiest moment and the last second. On the contact sheet, check:
- [ ] The promise is readable in the first frame.
- [ ] No text overlaps another element or runs off screen.
- [ ] Every person's state matches the story at that moment.
- [ ] Names are short chat names; nothing internal (ids, tool names, file paths a viewer won't know).
- [ ] No personal data: no bookmarks, tabs, address bar, taskbar, private chats or real names outside the team.
- [ ] The final frame shows the result, held still.
Fix, save with `overwrite: true`, and render frames again. Only then export.

After the export, measure it before showing anyone:
`room_motion(action="inspect", job_id=<export job>, sheets=["contact", "motion"])`.
- [ ] `moving_share` is at least 0.5 and `longest_hold_s` is at most 3 (Q1). Look at the motion chart for
  long flat stretches.
- [ ] The probe shows 1920x1080 at 30 fps or more (Q8).
- [ ] Score the rest of Q1-Q11 in `Docs/research/demo-references/quality-bar.md` from the sheets, and put
  the scores in the publish note. Use `vs` against a reference or an earlier cut when you're improving one.

## 8. Known tool behaviour (0.12.48)

- v2 scenes render: text, image, capture, video, shape, path, captions, group, room, cursor and redact
  layers, with keyframes, transitions, the composition camera and the brand kit. `frame` and `export` draw
  the same frames.
- **Not yet installed:** `ui` layers (real Board components) validate but need frontend's bundle to render,
  so record the real app instead for now.
- **No sound yet.** An `audio` or `voiceover` layer, or an unmuted `video` layer, validates with a warning,
  and `export` refuses it. Set `"muted": true` on video layers and leave audio out.
- **First composition only.** `frame` and `export` use the first composition; put the video you want first,
  or use `variant`.
- **Assets:** room uploads (`/uploads/<file>`), kit files (`motion:<path>`) and registered originals
  (`fileref:<id>`: recordings and pasted paths, read where they lie), at most 64 MiB per page. Validation
  does not check that an upload exists: the templates use `/uploads/replace-*.png` placeholders, so replace
  every one first.
- **Formats:** `export` takes a `profile` (see AUTHORING.md, Export formats). Only WebM profiles `publish`
  into chat; for the others, share the job's download link. MP4 isn't available yet.
- **Render time:** room-only scenes take about 2-3 s per second of video. Scenes heavy with screenshots or footage
  take up to about 6 s per second at 30 fps, and about 11-15 s at 60 fps, until faster capture lands. Plan for it.
- **Text limits:** scene `source` is at most 200 characters and `name` at most 80. Put long provenance
  (which captures are real) in the publish note instead.
- One export or frame render runs at a time. If busy, wait; don't loop. Always call `export_status` before
  retrying an export, so you never start a second one.

## 9. Templates

- `templates/product-demo-30s.json`: ask, work, result, call to action. Props: product, promise, ask,
  result, cta, accent. Three capture assets to replace.
- `templates/feature-announcement-15s.json`: badge, feature name, highlighted screenshot, benefit, where
  to find it. Props include the highlight box centre.
Both are v2 and render on 0.12.48. Neither uses sound. Both predate the brand kit and the camera: for new
work, swap their hand-set sizes for `style`s and their scale-and-x/y zooms for camera keyframes.

## 10. Deliver

`publish` with `post: true` and the `task_id` you own. In the note, state: scene ref, length, real or
synthetic material, and anything you couldn't show and why. Then ask for review on the task.
