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
| Proof on screen | A card moves Open to Done while three agents hand off |
| Length and shape | 20 s, 16:9, no voice |
| Source material | Real captures (list the /uploads files) or "synthetic, labelled" |

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

## 4. Typography and layout (1920x1080)

- Body text 48 px or larger, titles 96 px or larger. Captions sit in the lower third, inside a 96 px safe margin.
- No more than about 12 words on screen at once outside a caption.
- Use the same short names chat uses (Frontend, Design, QA), never job titles or internal ids.
- Label synthetic content as synthetic in the scene `source` and, if it could be mistaken for real, on screen.

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
  the room is scaled to fill the frame. In a plain v1 scene, chips stay on screen and the room uses only
  the left 60% of the frame.
- `board` beats move a card by id (`label`) to a column name (`state`); both must exist in the layout.
- Beat times (`at_ms`) count from the start of the room's clip, not the start of the video.

## 7. Check before you show

Render frames at the first second, the busiest moment and the last second. On the contact sheet, check:
- [ ] The promise is readable in the first frame.
- [ ] No text overlaps another element or runs off screen.
- [ ] Every person's state matches the story at that moment.
- [ ] Names are short chat names; nothing internal (ids, tool names, file paths a viewer won't know).
- [ ] The final frame shows the result, held still.
Fix, save with `overwrite: true`, and render frames again. Only then export.

## 8. Known tool behaviour (0.12.44)

- v2 scenes render: text, image, capture, video, shape, path, captions, group and room layers, with
  keyframes and transitions. `frame` and `export` draw the same frames.
- **No sound yet.** An `audio` layer or an unmuted `video` layer validates with a warning, and `export`
  refuses it. Set `"muted": true` on video layers and leave audio out.
- **First composition only.** `frame` and `export` use the first composition; put the video you want first.
- **Assets:** room uploads (`/uploads/<file>`) only, at most 64 MB per scene. Validation does not check that
  an upload exists: the templates use `/uploads/replace-*.png` placeholders, so replace every one first.
- **Formats:** `export` takes a `profile` (see AUTHORING.md, Export formats). Only WebM profiles `publish`
  into chat; for the others, share the job's download link.
- **Render time** is about 2-3 s per second of video. Plan 45-60 s for a 20 s export.
- One export or frame render runs at a time. If busy, wait; don't loop. Always call `export_status` before
  retrying an export, so you never start a second one.

## 9. Templates

- `templates/product-demo-30s.json`: ask, work, result, call to action. Props: product, promise, ask,
  result, cta, accent. Three capture assets to replace.
- `templates/feature-announcement-15s.json`: badge, feature name, highlighted screenshot, benefit, where
  to find it. Props include the highlight box centre.
Both are v2 and render on 0.12.44. Neither uses sound.

## 10. Deliver

`publish` with `post: true` and the `task_id` you own. In the note, state: scene ref, length, real or
synthetic material, and anything you couldn't show and why. Then ask for review on the task.
