---
name: room-review
description: Use when requesting, answering or re-checking a room_review, or deciding whether a change needs one.
version: 3
---

# Peer review

## Does it need a review?

- Code changes: yes, an exact-revision review before handoff.
- Docs-only or comment-only patches of about 10 lines or fewer: no separate request. Record them on the task; the release composer checks them.
- A material change after approval needs a new revision and a focused re-review of the delta only.

## Requesting

1. **Freeze first.** Commit, export the patch, record its sha256, the base revision and the expected result identity. Fixing a hash or receipt after asking wastes a review round.
2. **Pick the right reviewer.** The owner of the seam the change touches. Spread the load: design and demo-marketing take UI and copy, motion-design takes video output; don't default to the busiest engineers.
3. **State the scope**: files, what changed and why, the evidence you have, and the specific questions you want answered.
4. **Merge danger, one line.** Say whether it's a one-way door (hard to undo: deleted data, a migrated record, a user-visible promise) or a two-way door, and name the blast radius in a word or two (billing, deploy ordering, every agent's prompt).
5. **Visual and motion evidence.** For UI changes, include the before mock or markup and an after screenshot of the actual component. Label scripted data, and say whether the evidence is from an isolated preview or the installed release; a drawn mock is design evidence, not runtime proof. For motion, name the frames or video actually inspected.

## Answering

1. Re-hash the artifact yourself and name the exact revision in the verdict. A mismatch is a refusal, not a guess.
2. Read the actual bytes, plus the callers and seams the change touches at the same base.
3. Say what you ran and what you only read.
4. **Report two axes, separately.** **Spec:** against the task and the request. What was asked for and is missing or partial, what was added that nobody asked for, and what looks done but is wrong. Quote the line you checked it against. **Standards:** against engineering-standards and the seam's conventions. Keep the two lists apart and don't rerank across them: a change can pass one and fail the other.
5. **Blocking findings** give a concrete failure scenario (inputs or state, then the wrong result) and a fix. **Non-blocking notes** are labelled as such.
6. Verdict: `approved` or `changes_requested`, on that exact revision.

## Known blind spot

A source review can approve every piece and still miss the integration: the fileref renderer gap passed three reviews because nobody loaded a real recording into a real render page. When a change crosses a seam (schema to renderer, server to page), ask for or run the smallest end-to-end check through the real path, or say plainly that it wasn't run.
