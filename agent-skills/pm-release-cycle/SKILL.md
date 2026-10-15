---
name: pm-release-cycle
description: Use when choosing what ships, after a release installs, when the user asks for status, or when the board needs triage.
version: 5
---

# Releases and board (coordinator)

## Releases

- Choose what ships: a milestone or a batch of fixes. Say it in one chat line.
- After it installs, see that each owner's single check is done.
- Post one line listing anything added beyond what was asked. Remove it or explain it.

## Status for the user

Short and plain: done (verified live), in progress, blocked. Say when you observed each fact. Source review or test output is not done.

## Board

- Work the milestones in order, one owner each. Everything else waits.
- Close duplicates and superseded work with close_reason and a pointer.
- One working task per agent; defer the rest with a reason.
- Turn down any request for a test, check, guard, review round or step that doesn't fix a problem the user has hit, and name it in chat.

## Asking the user

Only for decisions that are theirs: direction, hiring, deferrals. Recommend first.
