# In-chat team decisions: backend contract (part 2 of task 9b0614f8)

Status: built on backend-lead-first-25, revised for frontend 8407ede8 (F1, F2) and security 365efc14 (D1-D4).
Base: the 0.12.25 line with lead-first `@all`.
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
- Origin (D4): the message must be in this chat and come from the user. An agent's or system message is refused.
- Fields at open: `question` (<= 2000), `criteria` (<= 8), `participants` (2-8 registered, available agents;
  the opener is the lead/synthesiser unless it names another participant), `inputs`, optional `task_id`.
- `inputs` (D3): at most 8 `{quote, kind, message_id, author}`. `quote` must be copied word for word from that
  user message (a paraphrase is refused). `kind: requirement | suggestion`: requirements are binding, suggestions
  can lose with a stated reason. The card shows the kind and the user can change it (`input_kind`, user only,
  stored with `set_by: "user"`).
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
- Sealed turns (D1): while a run carries a position token for a sealed round, its chat posts are held (no chat
  line), its streaming text, live run text and tool-call details are blanked, and its Activity notes read
  "Sealed: ..." instead of the text. `room_markup` from that turn is never posted to chat or a task (attach the
  upload to the position instead). Board, chat and review changes are refused until the reveal.
- Chat text from position turns stays hidden from other participants while its round is sealed (`hidden()`,
  keyed on `chain_rounds`, so an unrevealed earlier round stays hidden after a reopen).
- Token (D2): the flow token is only in an agent's own `get`/`submit` result, never in the user view or the
  `decision` websocket event.
- Reveal: all positions of the round become visible together when the stage closes. A round that closes with
  fewer than two positions, or with missing participants, never claims full coverage or consensus; `coverage`
  reports `submitted/invited` and the missing names.

## Rounds

- `room_decision(action="reopen", decision_id, reason)`: lead only, or the human via HTTP. Use it when the human
  asks to reconsider or adds a requirement, not for every comment. Starts round N+1 at positions with
  revision+1 and epoch+1. Submissions from earlier rounds never count toward the new round.
- `rounds[]` holds each finished round as status (F1): `{round, revealed, concluded, outcome, proposal_id,
  coverage, reason, by, ended}`. Contributions carry `round`; a round reopened before its reveal stays sealed.
  `outcome` is set only when that round actually concluded.

## Conclusion in chat

- F2: a proposal before verdicts is only a candidate (`phase: candidate`) and posts nothing. When the round reaches
  review, one conclusion is posted in the originating chat, unrouted, by the lead: the real outcome
  (agreed / dissent / unresolved), positions submitted of invited and who is missing, the proposal, agreed,
  unresolved, what would change it, and dissent with names. Once per round; an interrupted round posts nothing.
  It also gives verdicts submitted of invited (for the current proposal and epoch) with who is missing, and each
  dissent verdict with its author and reason, separate from dissent the lead noted in the proposal.
  With no proposal it is a system line saying nothing was concluded, and that round's history keeps
  `concluded: false` and `proposal_id: ""`. Positions and verdicts stay in the record.
- The human accepts (`record`), asks for another round (`reopen`), or overrules (`record` with an edited
  proposal, the existing `edit`). Recording writes the decision note and creates proposed tasks, as today.

## Views for the frontend

`GET /api/decisions/{id}` and the `decision` event carry:
every existing field, plus `in_place, origin [channel, message_id], opened_by, task_id, inputs[], round,
rounds[], coverage {submitted, invited, missing[]}, phase (draft | discussing | candidate | review_ready |
recorded | stopped)`. `current_proposal` stays an id. Sealed contributions are
`{id, author, kind, stage, round, revision, epoch, created, sealed: true}`. No `token` (D2).
User actions on an in-place decision add `reopen {reason}` and `input_kind {index, kind}`; the user opens one with
`POST /api/decisions {message_id, channel, question, criteria, participants, lead?, inputs?, task_id?}`.
The chat card renders from these fields; the dock (B) renders contributions and attachments when revealed.

## Checks before review

- Create-or-get: two opens for one message return one record; a different message makes a second record.
- Sealing: during positions, the user view, the websocket event and another agent's `get` contain no text,
  evidence or attachment URL from other authors; after close, all appear together.
- In place: a named mention or task wake in the same chat still routes normally while the discussion runs; an
  ordinary human message does not pause it; `reopen` starts round 2 at zero and the round-1 history is right.
- Authority: an agent that is neither the claim owner nor a responder cannot open; nothing in the flow can deploy,
  change settings or read credentials.
