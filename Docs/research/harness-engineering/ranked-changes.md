# Harness-engineering research applied to ZeroCollab: ranked changes

Task 439c5ae2. Source: Zero's research repository github.com/shikyo13/harness-engineering at commit
b12a4aec0f37020cd4bdb5c0efddeefa03c527d5 (2026-10-14), read from a shallow clone. ZeroCollab seams were read in the
installed 0.12.71 release (package 81b03cc91ed3, under `data/deploy/releases/81b03cc91ed3/payload`) and measured in
the room's own logs. Nothing here is implemented; each adopted item becomes its own task.

Research paths below are relative to that repository: `T/` = `studies/2026-10-14-subsystems/`,
`S/` = `surveys/2026-10-13-harness-survey/`, `L/` = `literature/`.

## Evidence labels

- **measured**: a controlled comparison with numbers, in the research or in our logs (window and method stated).
- **source**: read from source code at a pinned commit (the research's own file:line, or ours).
- **claimed**: vendor-internal, a user report, or unreplicated.
- The research's own marks are kept: [V] checked against the primary source, [A] fetched by a research agent only.

## Ranked list

| Rank | Change | Evidence | ZeroCollab seam | Owner |
|-|-|-|-|-|
| 1 | Re-send the full room context after a native session compacts | measured (research [V] and our logs) + source | `project_room.py` context delivery; `native_adapters.py` stream readers | backend, harness specifies |
| 2 | Turn profile, prompt-block sizes and tool-argument digests in runtime events | source + measured gap in our logs | `project_room.py` prompt assembly; `room_runtime.py` runtime events | backend, harness specifies |
| 3 | Harness-side stall rule that steers before the tool budget stops | measured [A] + source | `room_runtime.py` tool budget and steering | harness with backend; needs 2 |
| 4 | Broker effects carry a replay policy and settle to a recorded outcome | source (pi-durable, oh-my-pi, opencode) | first-provider plan 45bc9f9a; `execution_outbox.claim` | harness + systems; contract change |
| 5 | Expose only the room tools an agent's role uses, for Codex agents | claimed [A] + measured size | `project_room.py` tool definitions; `room_mcp.py` | backend |

Already aligned (no change), and one conflict, are listed after the items.

### 1. Re-send the full room context after a native session compacts

- **Research claim.** "Compaction erases constraints; pinning restores them": policy violations were 0% with the
  policy in context, 30% after compaction (up to 59%), and 0% again when constraints were pinned outside the
  compacted region (`L/2026-10-14-proven-advances.md` section 3, "Governance Decay", arXiv 2606.22528, [V], measured
  on its own 1,323-episode benchmark; single-author preprint). Codex persists `ContextCompacted` as a rollout item so
  replay matches what the model saw (`T/context-engine.md` section 1, codex `compact.rs`, source). The execution
  frontier is what repeated compaction loses (`L/2026-10-14-proven-advances.md` section 3, openai/codex#34095, claimed).
- **Our seam (source).** `ProjectRoom.context` sends each block (workspace, board, notes, spec, peer patches) as
  `[Unchanged; already in your native session. Pull details with room_read if needed.]` whenever its fingerprint
  matches the one saved for that native session (`project_room.py:1471-1475`, saved by `complete` at `:577-586`,
  loaded by `previous_session` at `:563-569`). Nothing clears those fingerprints when the native session compacts: no
  adapter parses a compaction signal (`grep compact` in `native_adapters.py` and `room_runtime.py` finds none).
  After a compaction the agent can be told the decisions and the board are "already in your session" when its
  summary has dropped them.
- **Our measurement.** In the last 7 days of native session logs under the user's `~/.claude` and `~/.codex` homes:
  room Claude sessions (project folder under the room worktrees) had 33 `compact_boundary` records in 13 of 72
  sessions. Room Codex sessions (`session_meta.cwd` containing zerocollab) had 199 `compacted` records in 31 of 61
  sessions. Method: count of records by exact type, files modified in the window.
- **Change.** When an adapter sees the provider's compaction record (Claude stream-json `system` with subtype
  `compact_boundary`; Codex's compaction notification or item, to be confirmed against one captured frame), clear
  the saved context fingerprints for that session. The next turn then sends every block in full. Keep the existing
  fingerprint delivery otherwise; it is what keeps prompts small.
- **Expected effect.** Decisions, the board and the workspace come back into the native context after each
  compaction, the room's version of pinning.
- **Smallest check.** A fixture that feeds one compaction frame through each adapter, then shows the next prompt
  has full blocks instead of the Unchanged pointer. Live: the first prompt after a real compaction in one agent's
  session carries full blocks.

### 2. Turn profile, prompt-block sizes and tool-argument digests in runtime events

- **Research claim.** Codex accumulates per-turn phase durations (before first sampling, sampling, compaction,
  tool blocking) and counts, shipped as a `TurnProfile` fact (`T/observability.md` section 1, codex
  `core/src/turn_timing.rs:57-70`, source). A per-component prompt-token breakdown is named the prerequisite for
  reading any context-budget result: "without attribution, portfolio budget decisions are unfalsifiable"
  (`T/context-engine.md` section 3.4; `T/observability.md` section 3.2, proposal).
- **Our seam (source).** The turn prompt is already built from named, individually capped blocks: participants,
  workspace (2,400 characters), live context, conversation, spec, board, notes and peer patches (12% of the
  maximum) (`project_room.py:1477-1494`). Only the total is kept: `run.input_chars = len(prompt)` (`:1497`), and it
  is never emitted. `runtime_end` carries only `{run, agent, outcome}`.
- **Our measurement.** Window 2026-10-14 17:01 to 21:56 CDT, `data/runtime_events.jsonl`, 7,752 events. All 204
  `runtime_end` events carry only run, agent and outcome: no duration, input size or tool count. Turn durations had
  to be rebuilt from start/end pairs: median 75 s, p90 288 s, max 1,388 s; median 9 tool starts per turn, max 130.
  Claude `runtime_tool` start events record `"input": {}`, because the input streams in later. A first pass found
  "50 of 211 runs with 3 or more identical consecutive calls", but all 294 identical consecutive Bash pairs were
  empty-input artifacts, so repeated-call loops cannot be measured from today's log.
- **Change.** On `runtime_end`, add the turn's wall time, time to first output, time inside tools, tool count and
  the per-block character counts that make up `input_chars`. On each completed tool event, add a sha256 of the
  canonical arguments (sorted keys), not the arguments.
- **Expected effect.** Health snapshots measure prompt growth per block (the role's "prompts don't grow" bar) and
  where turn time goes, and item 3 becomes measurable.
- **Smallest check.** One live turn's `runtime_end` has block counts that sum to its `input_chars`, and one
  completed Claude tool event has a digest.

### 3. Harness-side stall rule that steers before the tool budget stops

- **Research claim.** Rule-based stall and loop detection that calls an advisor only when a rule fires gained +9.9
  points on average on SWE-bench Verified and Pro, at +$0.08 per task (LivePlan, arXiv 2608.06701, [A], measured).
  An adaptive monitor over a weaker worker gained +25.33 and +15.67 points (Cadence, arXiv 2610.12269, [V] headline).
  "Weak primaries do not know when to escalate, so triggers should be harness-side rules"
  (`L/2026-10-14-proven-advances.md` section 6). Loop detection must recover, not abort (`S/05-AGENT_ENGINEERING_FINDINGS.md`
  F5). oh-my-pi canonicalizes tool arguments, counts identical consecutive tool turns and answers with a redirect
  (`T/agent-kernel.md` section 1, `tool-call-loop-guard.ts:40,67-98`, source, verified by the study).
- **Our seam (source).** At its tool budget the room stops the turn (`room_runtime.py:2297`, `runtime_tool_budget`,
  plan D10). Steering already exists (189 `runtime_steering` events in the window above).
- **Change.** Using item 2's digests: when a turn repeats the same tool call with the same arguments N times in a
  row, or reaches a fraction of its budget with no board, chat or file change, send one steer naming what repeated.
  Stop only at the budget, as today.
- **Expected effect.** A looping turn recovers instead of ending at the budget. The research's gains are for
  weaker models; our frontier agents may gain less.
- **Smallest check.** Replay the rule over one day of events once digests exist and count how many turns it fires
  on, then check those turns by hand. Adopt only if most of those turns were real loops.
- **Status.** Not measurable today (item 2). Depends on item 2.

### 4. Broker effects carry a replay policy and settle to a recorded outcome

- **Research claim.** The pi-durable tool task commits its intent with `replay: "safe"|"unsafe"` before running;
  after a restart it re-executes only when both stored and current policies are safe, and otherwise settles as
  interrupted from durable partial output: "re-run iff safe, settle interrupted from durable partials, never guess"
  (`T/agent-kernel.md` sections 1 and 2, pi `docs/spec.md:4075-4107`, source, verified by the study). oh-my-pi pairs
  every pending call with a synthetic "outcome is unknown" result so history never dangles
  (`exit-diagnostics.ts:251-291`, source). opencode settles orphaned tool calls as interrupted with an `executed`
  flag before any provider call (`runner/llm.ts:332-385`, source).
- **Our seam (source).** My first-provider plan 45bc9f9a section 2 makes adapter effects durable intents. Systems'
  `execution_outbox.claim` (e33da6a, approved in review 1b6284e9) records `dispatch_attempted` and never makes it
  pending again. After a broker restart an attempted effect stays `dispatch_attempted` with no outcome record.
- **Change.** Each effect payload names `replay: safe|unsafe`, set by the reducer: reads and idempotent records are
  safe; chat posts, provider answers and stdin writes are unsafe. On recovery, re-dispatch a safe effect once, and
  settle every unsafe attempted effect with a durable `outcome_unknown` record that the agent sees on its next turn.
- **Expected effect.** No effect left with no outcome after a broker restart, and no duplicate chat posts or
  provider answers.
- **Smallest check.** One reducer fixture: kill between claim and confirm. The safe effect re-dispatches exactly
  once; the unsafe one settles `outcome_unknown` with zero re-sends.
- **Conflict.** Contract section 3 says a command left unknown is never resent. That rule stays for every
  provider-bound command. Re-dispatching a safe room-side effect is a deliberate exception, so it needs systems'
  agreement and a contract edit before any build.

### 5. Expose only the room tools an agent's role uses, for Codex agents

- **Research claim.** Deferred tool loading cut definitions from 77K to 8.7K tokens and raised MCP eval accuracy from
  49% to 74% (Anthropic, claimed [A]). Cursor's lazy MCP loading cut 46.9% of tokens in MCP sessions (claimed [A]).
  "Tool descriptions matter, but more text is not free": full augmentation raised success by a median 5.85 points
  but added 67% more steps (arXiv 2602.14878, [A]) (`L/2026-10-14-proven-advances.md` section 4). Five of the
  strongest loops gate MCP behind search or deferral (`S/05-AGENT_ENGINEERING_FINDINGS.md` section 2).
- **Our seam and measurement.** `project_room.py` defines 11 room tools in 18,282 source characters. The largest are
  `room_motion` 4,562, `room_task` 3,891, `room_release` 3,056 and `room_review` 2,097 (Python `ast` measurement of
  the `definition(...)` calls; `room_mcp.py` adds the browser and image tools). Claude Code already defers MCP tools
  behind its tool search, so this cost lands on Codex agents.
- **Change.** Offer `room_motion` only to agents whose role uses motion, and `room_release` only to release roles,
  keeping the shared coordination tools for everyone.
- **Expected effect.** Several thousand fewer characters in every Codex request for most agents. The research
  evidence for this is vendor-claimed, so treat the effect as small until measured.
- **Smallest check.** Count the tool-definition characters in one Codex agent's request before and after, and
  check that a motion agent still lists `room_motion`.

## Already aligned (no change proposed)

| Research finding | ZeroCollab today |
|-|-|
| Derive owed work from durable records instead of a status flag (`T/autonomy.md` section 2.4, qwen `hosted-wake-intake.ts:31-59`, source) | `RoomRuntime.silent_turn` lists unanswered reviews, follow-ups and direct messages from the records (`room_runtime.py:2300-2340`), and work continuity wakes the owner for them |
| A blank child reply must not read as a clean review (`T/multi-agent.md` sections 1 and 3, Zero Code `EMPTY_REPLY`) | A silent turn posts "finished without replying" with what is still owed, and the pending review stays pending |
| One writer, many readers; declaring write scope before writing raised pair pass from 23% to 50% (`L/2026-10-14-proven-advances.md` section 7, arXiv 2608.00947, [A], measured) | Board path reservations refuse overlapping claims |
| Zero-token waiting with fenced, coalesced wakes (`T/autonomy.md` section 2.1, kilo `goal/link.ts:141-193`, source) | `wait_on` and waiting tasks are woken once when the named records are met; nothing polls |
| Check completion against the world, not self-report (`L/2026-10-14-proven-advances.md` section 5, measured [A]) | Peer review on exact revisions and owner live checks on the installed release |

## Conflict with an approved decision (not adopted)

- **Research:** role-prose multi-agent is "mostly cosmetic or unproven" and "costs prefix-cache bytes on every turn";
  adopt capability profiles, reject role prose as a quality mechanism (`S/05-AGENT_ENGINEERING_FINDINGS.md` section 2).
- **ZeroCollab:** role cores and skills are user-approved profiles. No change is proposed. Item 2's block sizes would
  show what share of each prompt they take, if that question comes up.

## Licensing

Nothing here copies code. Items 3 and 4 adopt designs described in the studies. The studies' sources include AGPL
projects, so any build is a clean-room implementation from the described behaviour, as the research repository itself
notes.
