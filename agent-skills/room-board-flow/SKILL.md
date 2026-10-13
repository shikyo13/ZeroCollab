---
name: room-board-flow
description: Use when taking, updating, handing off, deferring or closing a board task, or when a turn starts or ends with work you own.
version: 3
---

# Board flow

The board is the team's only backlog and the user's view of the work. Keep it true.

## Start of a turn

1. Read `room_read(section="inbox")`: transfers, reviews and follow-ups waiting on you.
2. Check for an active room-wide hold decision. Under a hold, start nothing and end the turn.
3. Pick the work in the user's priority order, not arrival order.

## Owning work

- Claim the task and its file paths with `room_task` before editing. Respect other reservations; if scopes overlap, agree a split or a transfer first.
- **One main task.** Any other owned task waits with `next_action_state: deferred` and the reason in its next action. It does not stay "working".
- Check-ins are substantive: what changed, the evidence, blockers, what remains. No heartbeats.

## Ending a turn with owned work

Do exactly one of these:
- keep working;
- yield with the reason recorded on the task (waiting on a named person, review or event), using `wait` when it's the user;
- release or transfer the task.

A closing "I'll..." in chat is not a next step. Put it in `next_action`.

## Closing

- **Delivered work** closes as `done` only after its owner's live check passes on the installed release. Record `evidence.installation` and `evidence.acceptance` in the same turn the check passes.
- **Duplicate, superseded, parked or won't-do work** closes with `close_reason` and `close_pointer` (the task or decision that carries it), available from backend series 7. It claims no delivery. Before that is installed, defer the task with the reason and pointer in its next action.
- Never invent evidence to get past a gate. If a gate blocks an honest close, say why on the task and tell the coordinator.

## Handing off and asking

- A delegation names the deliverable, the acceptance criteria, the smallest meaningful check and the existing code to read first.
- Use `room_followup` for a tracked ask and `room_review` for a verdict. Both wake the recipient unless `notify=false`, so don't repeat them with an @mention.
- When one result answers several follow-ups, resolve them in one call with `followup_ids`, or post the result once and point the others to it.
- An @handle in a final chat reply wakes that agent. Mention only agents who must act.

## Asking the user

Anything you need from the user (an approval, a decision, an answer or an action) is a `room_followup` with `assigned_to: "user"`, linked to the task it blocks. It appears in the user's attention bell. A question only in chat doesn't count. Set the task's `wait` to that follow-up, and resolve or cancel it as soon as it's answered or no longer needed.
