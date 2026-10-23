# Motion recipes

Ready-to-run prompts for an agent that has `SKILL.md` and `room_motion`. Each recipe states what it needs,
the shot table to start from, and what "done" looks like. Times assume 30 fps.

All recipes render on 0.12.44. Keep them silent: exports refuse sound until audio mixing ships (SKILL.md section 8).

## 1. 30 s product demo from real room captures (v2)

**Ask the requester for:** the one promise, the request text, and three captures: the ask (chat),
the work (Board or shared browser), the result (finished page or app). If they have none, use
`room_read` to find recent `/uploads` screenshots and markups and confirm the picks with them.

**Start from:** `templates/product-demo-30s.json`. Replace the three `/uploads/replace-*.png` refs and the props.

| # | Frames | Shows | Text |
|-|-|-|-|
| 1 | 0-105 | Product name, promise | props product, promise |
| 2 | 90-255 | The ask capture, eases in | prop ask |
| 3 | 240-615 | The work capture, slow zoom in and out | 2 captions: who did what |
| 4 | 600-810 | The result capture, held | prop result |
| 5 | 795-900 | Promise and call to action | props promise, cta |

**No captures yet?** Replace the work clip's capture with a `room` layer: three agents, a board card moving
to Done, and the ask as the room's first caption beat. See `project:zc-demo-20s` for a working example.

**Done when:** frames at 1 s, 15 s and 29 s pass the SKILL.md checklist, the export is published to the
requester's task, and the note says which captures are real.

## 2. Feature highlight, 15 s (v2)

**Ask for:** feature name, one-line benefit, where to find it, and one screenshot with the feature visible.

**Start from:** `templates/feature-announcement-15s.json`. Set `box_x`/`box_y` to the centre of the feature
in the screenshot; check it with `frame` at about 6 s and move it until the box sits on the feature.

**Done when:** the highlight box frames the feature at 6 s, the benefit is readable at 10 s, and the last
2 s say where to find it.

## 3. "The last 10 minutes of the team in 30 seconds" (room layer)

**Gather:** `room_read` the task list and recent messages for the window. Keep only events a viewer can
see: a task changing column, a handoff (review requested, approved), a tool running, a screenshot posted.
Drop routine reads and check-ins.

**Compress:** map real time to scene time with one rule and say it in the first caption, for example
"10 minutes in 30 seconds". Keep order; never reorder events to make a better story.

**Build:** one `agent` per person who acted (at most 6), the board with the columns that changed, one
`board` beat per column change, one `handoff` beat per real handoff, `tool` beats only for the 3-4 longest
pieces of work. Close each person's part with an `agent` beat (Done or Waiting).

**Done when:** every beat traces to a real task or message (list them in the publish note), and the scene
`source` says "replay of <time window>, compressed".

## 4. Title and lower-third pack (v2)

A small reusable scene with one composition per item, all bound to props, for use inside other videos:
- **Title card** (3 s): product name 120 px, subtitle 56 px, fade in over 0.5 s, hold.
- **Lower-third** (4 s): accent bar slides in from the left (0.3 s, ease-out), name 56 px and role 40 px
  appear 0.2 s later, everything leaves together at the end.
- **End card** (4 s): promise 88 px, call to action 56 px in the accent colour.

Keep every item inside the 96 px safe margin and at least 48 px for any text. Check each composition with
`frame` at its midpoint.

## Writing a new recipe

Same shape as above: what to ask for, what to start from, a shot table, and a "done when" that someone
else can check from frames alone.
