# In-chat team decisions: backend contract (part 2 of task 9b0614f8)

Status: proposal for review before building. Base: the 0.12.25 line with lead-first `@all` (backend-lead-first-25).
Sources: Zero #12523/#12571/#12611, decisions 57bb7852 + d43dca31, Opus 96973df5, design v3 reference
(design-review/chat-decisions/v3: mock 6fff4de9, acceptance 62201c25, frontend approval 58f91571).
The contract is layout-independent: it serves design option A (inline) and B (chat card plus right dock).

## What changes in decision_flow.py

| Today | In-chat decisions |
|-|-|
| Only the user creates and starts a discussion (HTTP routes) | The lead (or its backup) opens one from the conversation with `room_decision(action="open")`; the user can still open one |
| `start()` opens a separate chat | Runs in the originating chat; no new chat |
| A live discussion chat routes only flow turns; other wakes are sent to the default chat | Other work in the chat routes normally; only the flow's own turns carry the decision token |
| Any human message in the chat pauses the discussion | Ordinary human messages do not pause it; the lead reopens explicitly when the human asks to reconsider |
| The user's view shows every position during positions | Sealed for everyone (user, UI, agents) until positions close |
| One proposal cycle (return/edit) | Rounds: a reopen starts a new round at zero; each round keeps its own reveal and conclusion state |

Unchanged: stage machine (positions, critique, proposal, verdicts, review), revision/epoch fences, stage deadlines,
attribution, user-only record. A discussion still grants no deploy, settings or credential authority and adds no
approval gate.

## Identity and creation

- Key: `(channel, origin_message_id)`. `open` is create-or-get: a second `open` for the same message (the backup
  taking over, or a retry) returns the existing record instead of creating another one.
- Who may open: the human (HTTP, UI token), the current response-claim owner for that message, or the room's
  responders lead/backup. Nobody else. The record stores `opened_by`.
- Fields at open: `question` (<= 2000), `criteria` (<= 8), `participants` (2-8 registered, available agents;
  the opener is the lead/synthesiser unless it names another participant), `inputs`, optional `task_id`.
- `inputs`: the human's ideas, attributed to the human and to `source_message`, each with
  `kind: requirement | suggestion`. Requirements are binding constraints; suggestions are options that can lose
  with a stated reason. Their text stays visible (it is already public in chat).
- `task_id`: links an existing board task (validated). A discussion never creates a task; tasks proposed by the
  lead are created only if the user records the decision, as today.
- When to open (tool guidance, not enforced): two or more reserved-path owners or labels, a shared-interface
  change, or an explicit request to deliberate. Routine single-owner choices stay ordinary replies.

## Submissions and sealing

- Positions accept `text`, `evidence` and `attachments`: up to 6 `{url, name}` where `url` is an `/uploads/...`
  file in this room (mockups, room_markup output). Nothing else.
- Sealed view (until the positions stage closes for that round): for every reader except the author, a position
  is `{id, author, submitted_at}` only. No text, evidence, attachment URLs, names or counts of attachments. This
  applies to the HTTP detail and list routes, the `decision` websocket event, and `room_decision get`.
- Chat text from position turns stays hidden from other participants until reveal (existing `hidden()`, keyed
  on the round's position chains).
- Reveal: all positions of the round become visible together when the stage closes. A round that closes with
  fewer than two positions, or with missing participants, never claims full coverage or consensus; `coverage`
  reports `submitted/invited` and the missing names.

## Rounds

- `room_decision(action="reopen", decision_id, reason)`: lead only, or the human via HTTP. Use it when the human
  asks to reconsider or adds a requirement, not for every comment. Starts round N+1 at positions with
  revision+1 and epoch+1. Submissions from earlier rounds never count toward the new round.
- Each round records `{round, revealed: bool, concluded: bool, proposal_id, positions: [ids]}`. History returns
  revealed rounds in full; sealed (never revealed) rounds as status only; "superseded recommendation" only when
  that round actually concluded.

## Conclusion in chat

- The synthesiser's proposal (agreed points, dissent with names, unresolved questions, what would change it,
  tradeoff) is posted once in the originating chat as the decision's conclusion, through the normal response
  claim when the discussion was opened from a human `@all`. Participants' positions and verdicts go to the
  record, not the chat.
- The human accepts (`record`), asks for another round (`reopen`), or overrules (`record` with an edited
  proposal, the existing `edit`). Recording writes the decision note and creates proposed tasks, as today.

## Views for the frontend

`GET /api/decisions/{id}` and the `decision` event carry:
`id, question, criteria, lead, opened_by, channel, origin_message_id, task_id, inputs[], stage, round,
coverage {submitted, invited, missing[]}, rounds[], contributions[] (sealed as above), current_proposal,
outcome, owed[], actions[], token`.
The chat card renders from these fields; the dock (B) renders contributions and attachments when revealed.

## Checks before review

- Create-or-get: two opens for one message return one record; a different message makes a second record.
- Sealing: during positions, the user view, the websocket event and another agent's `get` contain no text,
  evidence or attachment URL from other authors; after close, all appear together.
- In place: a named mention or task wake in the same chat still routes normally while the discussion runs; an
  ordinary human message does not pause it; `reopen` starts round 2 at zero and the round-1 history is right.
- Authority: an agent that is neither the claim owner nor a responder cannot open; nothing in the flow can deploy,
  change settings or read credentials.
