# Decision flow: backend model (v2, for review)

Task 78896864. Design spec: design's `design-review/decision-flow/acceptance.md` (sha 2480c179). v1 was a board check-in; v2 answers opus review ff07a10f. No code yet.

## Storage

`<project>/decisions.json` holds a list of discussions:

- `id`, `question`, `criteria[]`, `participants[]`, `lead` (writes the proposal; chosen by the user, default first participant), `channel`, `status`, `stage`, `revision`, `epoch`, `returns_used` (max 2), `edits_used` (max 2), `deadline` (epoch seconds for the open stage).
- `contributions[]`, append-only, never edited:
  `{id, author, stage, kind, revision, epoch, text, evidence[], responds_to[], context, created}`.
  `kind` is one of `position`, `challenge`, `concession`, `proposal`, `edit`, `verdict`, `missing`.
  `context` is `fresh` or `prior` (see Fresh sessions).
- `record`: `{key, note_id, tasks: [{title, id | error}]}` for idempotent recording.

The current proposal is the newest `proposal` or `edit` contribution at the current revision. The current verdicts are the `verdict` contributions whose `proposal_id` equals that contribution's id. Earlier contributions stay visible and attributed; nothing is a single mutable proposal.

## Stages

`draft` -> `positions` -> `critique` -> `proposal` -> `verdicts` -> `review` -> `recorded`, plus `stopped`.

- **draft**: question, criteria and at least two available participants. Saved; nothing queued.
- **positions**: every participant is queued at once (running in parallel within `parallel_agents`) and submits one `position` through the tool. Positions are hidden until the stage closes (see Anti-anchoring).
- **critique**: one round. Each participant submits its challenges and concessions together, each with `responds_to` naming the position ids it targets.
- **proposal**: the lead submits `{agreed[], dissent[{point, by}], unresolved[], would_change[], text}` with `responds_to` naming the challenge ids it answers.
- **verdicts**: each participant submits `{proposal_id, agree | dissent, reason}`.
- **review**: the user chooses one:
  - **Record decision**: every participant agreed.
  - **Record with dissent**: everyone answered, some dissent.
  - **Record unresolved outcome**: a verdict is missing; absent participants are named.
  - **Edit**: the user's text becomes an `edit` contribution at revision N+1, attributed to the user; verdicts reset; verdicts are re-queued. Max 2.
  - **Return with a question**: the lead revises at N+1 answering that question, then verdicts again. Max 2.

## Tokens, epochs and stale submissions

Every queued turn carries a routing token `{decision, stage, revision, epoch}`, the same mechanism as team_builder's `team_builder` token. Admission drops a request whose token is no longer current. The tool refuses a submission with a clear reason, and appends nothing, when:

- the token does not match the current `(stage, revision, epoch)`;
- the author is not a participant who still owes that stage (or not the lead, for `proposal`);
- a verdict's `proposal_id` is not the current proposal.

`epoch` is bumped by Stop, Edit, Return, and a human message in the discussion chat. A human message mid-stage closes the open stage, moves to review with the note "New direction from you", and the user chooses Continue (re-queues the open stage at the new epoch) or Return.

## Anti-anchoring and fresh sessions

Each discussion has its own chat. Native sessions are saved per `agent:channel`, so a participant's first turn there starts with no prior session and its position is labelled `fresh`. A participant that already has a session in that chat (for example after a retry or edit) is labelled `prior`, shown as "Prior debate in context".

During positions, nothing another participant wrote reaches a participant:

- Positions are submitted through the tool, not as chat text, and `room_decision(action="get")` returns other participants' positions only after the stage closes.
- Each stage has its own chain id. `project_room.context` and `room_read(section="messages")` hide, from every participant except the author, messages in the discussion chat that carry the positions chain while the stage is open. Final chat answers from position turns are covered this way.
- Discussions are not on the board and write no notes until recorded, so the Board, notes and peer-patch blocks carry nothing about them.

The stage closes when every participant has submitted or the deadline passes. Then all positions are revealed together and critique turns are queued.

## Deadlines and missing responses

Each open stage has a deadline (default: `limits.turn_max_seconds` if set, otherwise 15 minutes). A turn that ends without a submission (including a silent completion after harness's 15f6b581) marks that participant "No response yet". The user sees Retry for that participant (re-queued within the same epoch) and Stop. At the deadline the stage closes, a `missing` contribution "No response from X" is appended, and the flow continues. Fewer than two positions skips to review as unresolved.

## Recording

Recording writes one room `decision` note linking the discussion id and proposal contribution id, and creates the previewed tasks unowned. The record key is `discussion id + proposal id + outcome`, so a double click or retry returns the existing record. A failed task is retried alone. Recording grants no deploy, restart, settings or credential authority.

## Hooks outside the new module

| File | Hook |
|-|-|
| `decision_flow.py` (new) | storage, stages, tokens, deadlines, REST routes, the `room_decision` tool handler |
| `room_runtime.py` | load, admission `accepts`, `turn_finished`, interrupt on pause or project change, tool dispatch: the same 5 places team_builder uses |
| `room_mcp.py` | one tool, `room_decision(action, decision_id, stage, revision, epoch, payload)` |
| `project_room.py` | positions-chain filter in `context()` and in `read(messages)` |
| `app.py` | a human message in a discussion chat calls `decision_flow.human_message()` instead of the normal routing |

## Open questions

1. Lead: user-chosen with a default (proposed) or a rotating synthesizer?
2. Placement: a sidebar chat per discussion, or only Board > Decisions with the transcript inside (design to answer)?
3. Should Edit and Return share one cap instead of 2 each?

## As built (decision_flow.py, on 0.12.10)

Changes from v2, taking opus's f93e3350 build notes:

- **Deadline**: one stage clock of `turn_max_seconds` (or 15 minutes) times `ceil(owed / parallel slots)`, so queued participants are not marked missing before they could run. A turn that ends, or is never admitted, without a submission shows "No response yet" at once; Retry re-queues only that participant.
- **Close stage now** (`close_stage`, shown as "Continue without X"): an addition, so one silent participant does not hold a stage until the deadline. Missing participants are recorded the same way. Design to confirm the wording.
- **No routing in a live discussion chat**: app.py stops normal routing for that chat. Agent text wakes nobody; a human message pauses the open stage as new direction.
- **Continue after new direction**: everyone is re-queued at the new epoch with the message id in the prompt. Earlier submissions in that stage stay visible, marked `before_new_direction`, and no longer count.
- **Challenge coverage**: the view lists `unanswered_challenges`, shown as "Not answered by the lead".
- **User-only actions**: create, start, edit, return, stop, retry, continue, close_stage and record are REST routes (`POST /api/decisions`, `POST /api/decisions/{id}/{action}`) that need the UI token. Agents only `get` and `submit` through `room_decision`, and the run-scoped token (a1eff69) does not reach the REST routes.
- **Limit of enforcement (opus 57260ef8)**: hidden positions and user-only actions hold for normal agent tool use, not against an agent that sets out to bypass them. The UI token is embedded in the room's `/` page, and `decisions.json` is readable on disk, so any process running as the same user, agents included, could read every position or call the user routes. Treat this as a protocol for honest participants, not a security boundary.
- **Other wakes**: review, follow-up and delegation wakes aimed at a live discussion chat are queued in the project's default chat instead, so only the flow's own turns run in a discussion chat.
- **Authority**: the recorded note is authored by the user and states it grants no deploy, restart, settings or credential authority. Participants keep their normal tool permissions during discussion turns; the prompt asks them not to act, which is an instruction, not enforcement.
- **Caps**: Edit and Return stay separate, 2 each.
- **Storage**: `decisions.json` beside the board, atomic writes, the newest 40 discussions kept (closed ones are dropped first).
- **Placement**: each started discussion gets its own chat titled `Decision <id>: <question>`, which uses one of the project's 24 chat slots. Design's follow-up 810e6c69 is still open.
