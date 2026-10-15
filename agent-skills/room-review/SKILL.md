---
name: room-review
description: Use when requesting or answering a room_review.
version: 4
---

# Review

One review per change. It exists to catch a bug that would reach Zero, not to polish the change.

## Requesting

- Code changes get one review before merge. Docs and comment-only changes need none.
- Ask the owner of the code the change touches. Say what changed, why, and what you checked.
- Name the commit or branch so the reviewer reads the right thing. No hash, receipt or identity paragraphs.
- UI changes include a screenshot of the actual component.

## Answering

- Read the change and the code it touches. Say what you ran and what you only read.
- Block only for a concrete bug: the inputs or state, the wrong result, and the fix. Everything else is a note the owner may take or leave.
- Approve in one pass. A fix that does exactly what the reviewer asked ships without another review.
- Don't ask for new tests, guards, checks or cases unless they fix a failure you can show.
- When a change crosses a seam (server to page, app to room), run or ask for one end-to-end check through the real path, or say it wasn't run.
