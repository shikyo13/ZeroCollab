# Architecture addendum: failure semantics, streaming, security, platforms and targets

This addendum answers Zero's independent review (#17832) of architecture decision c1c50d2f047c. The architecture itself is unchanged. These sections specify the guarantees that the review found missing. Each one names what is guaranteed, what is explicitly not guaranteed, and the check that will prove it.

**Status: specification only.** Nothing here has been implemented or measured. Every guarantee is unproven until its named check passes on an installed release. Thresholds are proposals until QA records matched baselines on today's Python.

Assembled by opus (task a2a36a47) from three reviewed source files, copied verbatim with headings shifted one level down:

| Section | Authors | Source file and sha256 | Review |
|-|-|-|-|
| (a) Failure matrix, (b) streaming | harness, systems | harness `core-contract/addendum-25a609c2.md` r4, `0897204f...d0b5` | systems dc421377, co-author re-review (after fff80ca6 and 540427ff) |
| (c) Security, (e) data and upgrade safety | backend, desktop | backend `architecture-addendum/backend-c-e.md` r4, `4bd6bac5...6a36` | systems 2c0f80a5, non-author (after e99bccb6) |
| (d) Cross-platform feasibility and targets | systems, qa | systems `architecture-addendum/systems/section-d.md` r2, `dd33da8b...840c` | desktop 89e0d43c, non-author |

Reviews are specification reviews. Neither consensus nor review is empirical proof.

## SQLite durability (adopted from systems, decision 25a609c2)

1. A critical record (an intent, a launch attempt, a confirmed launch, an exit, an acknowledged input) is acknowledged only after a successful SQLite commit in WAL mode with `synchronous=FULL`. A separate file flush is not a substitute. Disposable progress may use NORMAL.
2. Checks that kill a process prove crash recovery. None is claimed as proof of surviving a power loss.

## Known limits, stated plainly

- **G1:** agents run with the user's full rights (Codex full access, Claude bypassPermissions, per `runtime.automatic_tool_approvals`). The room's authorization does not confine what an agent can do on the machine. This is Zero's existing setting, recorded here so nobody assumes otherwise.
- **G2:** on 0.12.64, any web page in the browser can trigger a server-side rename of a live room agent through `/api/register`, though the browser stops the page from reading the token it creates. Non-browser local programs can register and receive a token. The approved source fix 7bcc440 (6be90c9a, review ec378349) is selected for 0.12.65; until it is installed and backend's live check passes, the running room is still exposed. The local-program half is task 319c3cdf.
- **macOS:** no Mac is available, so nothing here is proven on macOS. Hosted macOS runners or automated three-OS CI would need Zero's OK under decision 900d929d.

---

## Addendum to the architecture proposal: execution failure matrix and streaming contract

Decision 25a609c2 (Zero's review #17832, items 2 and 3), for proposal fca926f084 and contract task 811139cd.

**r4.** Harness, co-authored with systems (follow-up 91d9894a, reviews fff80ca6 and 540427ff). It folds in:
- frontend's snapshot and replay boundary (d64409bf);
- design's three-axis state requirement (reference 6f1fec26);
- motion-design's job classes and screen replay rule (#17839);
- QA's named crash points (#17840).

r3 fixes systems' six findings on r2:
1. Terminal states need proven teardown of the whole owned tree.
2. A missing record in a restored database never proves non-execution.
3. Control events are separated from disposable output, and recovery is capability-specific.
4. Input is a durable broker state machine, with no automatic resend without proven provider dedup.
5. Repeatable retries need verified teardown, a new attempt and a new scratch path.
6. Quit and Stop persist an intentional-stop policy, and install reconciles every uncertain attempt.

It also answers media's and frontend's revision 2 dissents.

r4 fixes the three remaining points from systems' re-review 540427ff:
- Row 12 drops an unsafe proof example: absence from a restored or current-epoch journal is never proof.
- Raw frames are non-disposable until coordination's adapter has durably classified them, with an explicit
  overload policy.
- The remaining unconditional recovery wording is qualified to retained and verifiable data.

Specification only: nothing here is built. Every "check" is the acceptance a later slice must pass, not a claim
about today, and every "guaranteed" is a requirement on that slice, not demonstrated behaviour. Process-kill
checks prove crash recovery, never power-loss survival (decision 25a609c2, amendment 2).

Terms:
- **Coordination:** the Python room service (room.db).
- **Broker:** the long-lived execution process (runs.db).
- **Provider:** a CLI agent process (Claude Code `--print --input-format stream-json`, Codex `app-server --stdio`).
- **Intent:** coordination's durable request to run one turn.
- **Attempt:** one launch of one provider process for that intent.

### Grounding: what today's Python does

Read from the installed 0.12.64 source. The matrix below is written against these facts.

- There is no broker yet. The room process owns each provider's stdin and stdout pipes (`native_adapters.spawn`).
- Providers start in their own process group (`CREATE_NEW_PROCESS_GROUP` on Windows, `start_new_session` on
  POSIX), and **not** in a job object, so a provider can outlive the room process. Termination is `taskkill /T /F`
  or `killpg(SIGTERM)`, then `kill` (`native_adapters.terminate`).
- Both providers keep their own durable session: Claude via `--resume <session_id>`, Codex via `thread/resume`
  on the thread id. Today's restart recovery is a **new process resuming the provider session**, not a
  reattach. Codex refuses a resume while an orphan still holds the thread (`thread_busy` raises `ThreadBusy`).
- Input already has provider-level identity:
  - **Claude:** steers carry a `uuid`; a duplicate uuid is skipped and re-acknowledged, and delivery is the
    `isReplay` echo. Approvals answer a `request_id`. Interrupt is a `control_request` with subtype `interrupt`.
  - **Codex:** steers use `turn/steer` with `expectedTurnId`. Interrupt is `turn/interrupt`. Approvals are
    JSON-RPC responses to server requests by id.
- Codex history can be read back by thread with a cursor (the `threadId`/`limit`/`cursor` read in
  `native_adapters`). Claude writes its session as JSONL under the CLI's projects folder.

### (a) Execution failure matrix

#### Three axes, never one label

Every run reports three independent facts. The UI shows each one separately, and never derives one from another
(design 6f1fec26):

| Axis | Values |
|-|-|
| Execution state (broker, runs.db) | accepted, launch_attempted, running, exited, interrupted, unresolved |
| Control (who can reach the process) | connected, controller_lost, control_unavailable (broker unreachable) |
| Output completeness | complete to cursor `k`, gap `[a, b]`, history not retained |

Cancellation is **orthogonal** to execution state. `stop_requested` is a durable flag with its own timestamp,
and "Stopping" is shown until a terminal state is committed. "Stopped" is never shown before that.

#### Execution states (broker, persisted in runs.db)

| State | Meaning | Committed when |
|-|-|-|
| accepted | The broker holds intent `I`. No spawn has been attempted | Before the acknowledgement to coordination |
| launch_attempted | Attempt `n` of `I` is about to start, with its launch marker | Before the OS call that creates the process |
| running | Process identity **and** transport (pipes) verified and durably recorded | After create, before any input is written |
| exited | The main process ended **and** the broker proved the attempt's whole owned tree is gone (containment empty) | After exit, drained output and a verified-empty containment |
| interrupted | The broker lost the transport, then proved complete teardown of the attempt's whole owned tree | After the restart sweep verifies the containment is empty |
| unresolved | The broker cannot prove what happened to attempt `n`, or cannot prove its owned tree is gone | On broker restart, or whenever teardown can't be proven, per the rules below |

**Terminal means the whole tree, not the main process (systems, finding 1).**
- A grandchild can outlive the main process with its stdio closed, so the main process exiting is never enough
  for `exited` or `interrupted`.
- Both terminal states require proof that the attempt's containment holds no live member:
  - Windows: the job object reports zero active processes.
  - POSIX: the mechanism from open point 1. Ordinary process-group membership can't establish closed descendant
    containment, because a descendant can leave its group.
- Without that proof the attempt stays `unresolved`. It keeps holding its admission slot and any media lease.

Process identity is not a PID alone. It is PID plus process creation time (Windows `GetProcessTimes`, Linux
`/proc/<pid>/stat` start time, macOS `proc_pidinfo`), plus the containment the broker itself created: the job
object handle on Windows, or the open point 1 mechanism on POSIX.

The **launch marker** is an environment variable carrying `I`, `n` and the broker epoch, set on the provider
process. Windows also names the job object after it. The macOS marker is open point 2.

**A marker or a scanned PID is a discovery hint, never authority.** The broker terminates or tears down a
process only after verifying its process instance (PID plus creation time) **and** its membership of a
containment this broker epoch, or a recorded earlier epoch, created. A match on the marker alone allows reporting
only.

**Durability:** accepted, launch_attempted, running, exited, interrupted, stop_requested and unresolved are
acknowledged only after a successful SQLite commit with WAL and `synchronous=FULL` (decision 25a609c2,
amendment 1). A separate file flush is not a substitute. Output progress is not in this list; see (b).

**Launch authority (fencing):**
- Only one broker instance may launch. A broker takes an OS-level single-instance lock for its whole lifetime
  (a named mutex on Windows, `flock` on a lock file on POSIX), then increments `broker_epoch` in runs.db.
- Every launch_attempted row and every launch marker carries that epoch.
- An accepted-but-never-attempted intent is dispatched after a restart only by the broker that holds the lock and
  the current epoch. The SQLite writer lock alone cannot exclude an old launcher that is still alive (systems).

**Idempotent accept:**
- The same intent id with the same payload hash returns the original run and its current state.
- The same intent id with a different payload hash is rejected with `intent_conflict`.

#### Job classes (motion-design)

Each job type declares a class:

| Class | Examples | Uncertain launch |
|-|-|-|
| effectful | agent turns (file edits, git, browsing, messages) | Held as unresolved. Never relaunched automatically |
| repeatable | frame render, encode, probe, audio preparation into a per-attempt scratch folder | A retry needs all of: verified full teardown of attempt `n`, a new attempt `n+1`, and a new scratch path named after `(I, n+1)`. Until teardown is proven, the attempt is unresolved and keeps the lease |

**Replay safety is declared per operation, not per job (systems, finding 5).**
- Writing frames or audio into the attempt's own scratch folder is repeatable.
- Publishing an artifact, starting or finalising a recording, and posting a result are not repeatable, unless
  they are idempotent by an artifact id that coordination already holds.
- Repeatable jobs get no exemption from the journal or the ownership rules. They differ only in that a
  proven-clean retry is allowed.

Media keeps its single admission gate, and an attempt whose ownership is uncertain keeps its lease.

#### Three guarantees kept separate

1. **No duplicate launch:** at most one live provider process per effectful intent at any time. The broker never
   starts attempt `n+1` on its own while attempt `n` is launch_attempted, running or unresolved.
2. **Recoverable execution:** the user's work can continue after a failure, and each case below says how. A
   "continued" claim requires all three: the same process, its output retained up to the cursor, and input
   control restored (systems). Otherwise the most the room can say is "contained".
3. **No repeated external side effects:** **not guaranteed by this architecture.** A tool call that was in
   flight when its process died may have completed or not, and a resumed session may repeat it. The contract
   records that an attempt ended abnormally so a person or the owner can check. It never claims exactly-once
   side effects.

#### Failure cases

Each case lists what is guaranteed, what is not, and the check that proves it.

**1. Coordination dies, broker lives (crash point C1).**
- **Guaranteed:**
  - The provider keeps running with its pipes held by the broker, and output is spooled (b). No duplicate launch.
  - Execution is recoverable as the same process, **within declared retention and verified adapter capability**:
    on reconnect, coordination re-reads state, resumes output from its cursor, and regains each input
    capability it can verify (b, Recovery).
  - Anything beyond that is reported as an explicit gap or as `control_unavailable`, and Stop always stays
    available.
- **Not guaranteed:**
  - Complete continuation if the outage outlasted retention or entered overload (b).
  - Timely answers to approvals asked while coordination was down. The provider waits, and may time out by its
    own rules.
- **Check:** kill coordination mid-turn on a real provider, for less time than the declared retention, then
  restart it. Expect the same run id and process identity, output resuming with no gap or repeat, and Stop still
  ending the process. A longer outage is checked under (b) Overload instead.

**2. Broker dies while a run is `running` (crash point B4).**
- **Guaranteed:** no duplicate launch.
  - On Windows, the job object's kill-on-close ends the provider tree.
  - On POSIX there is no kill-on-close, so the mechanism (watchdog, `PR_SET_PDEATHSIG` on Linux, restart sweep)
    is open point 1. The requirement is that no attempt member survives the broker on any OS.
  - When the restarted broker proves complete teardown, the attempt is `interrupted`. If it can't prove it,
    the attempt stays `unresolved`.
- **Not guaranteed:** recovery as the same process. Ordinary stdio pipes cannot be reattached after their owner
  dies, by PID or by any other means, so **kill-on-close gives termination, not recovery**. Continuing means a
  new attempt that resumes the provider session, started only by coordination's explicit decision. In-flight
  side effects may repeat on that attempt.
- **Check:** kill the broker mid-turn. On Windows, no member survives (job check). On Linux, none survives the
  mechanism chosen for open point 1. The run reads `interrupted` or `unresolved`, never `running` or
  `exited/success`, and no new process starts until coordination asks.

**3. Broker dies after `launch_attempted`, before `running` (crash point B3, Zero's example).**
- **Guaranteed:** no automatic relaunch, even when no PID or receipt was saved. On restart, the broker searches
  for the launch marker (the job name on Windows, the environment marker in the process table on POSIX).
  - If found, the match is only a hint.
  - The broker tears the attempt down only after verifying the process instance and its membership of the
    containment that this attempt's epoch created.
  - Proven teardown of the whole containment makes the attempt `interrupted`. Anything less leaves it
    `unresolved`, holding its admission slot.
  - If not found, the attempt is `unresolved: launch_outcome_unknown`, held for coordination. The UI shows
    "Launch outcome unknown" with the last confirmed evidence.
- **Not guaranteed:** knowing whether the process ever ran, if it exited before the sweep. The provider may have
  started a turn and written to its session.
- **Check:** at crash point B3, with the process spawned and without it. Both cases end with zero live members,
  a state that names the uncertainty, and no second process.

**4. Broker dies after exit, before the `exited` commit (crash point B5).**
- **Guaranteed:** the outcome is never inferred from an absent PID.
  - If the main process is gone and the attempt's containment is proven empty, the run becomes
    `exited/status_unknown`.
  - If the containment can't be proven empty (a grandchild may survive), the run stays `unresolved` and keeps
    its admission slot.
- **Not guaranteed:** the exit status itself.
- **Check:** at crash point B5, once with a surviving grandchild and once without. The run never reads as
  completed successfully. With the grandchild alive it reads `unresolved`.

**5. Acknowledgement lost, coordination to broker (crash points B1, B2).**
- **Guaranteed:** coordination retries `run(I)` with the same intent id and payload. The broker returns the
  stored result for `I` and does not launch again. A retry that arrives after the run finished returns its
  terminal state.
- **Not guaranteed:** nothing beyond the retry rules above.
- **Check:** drop the acknowledgement after `accepted`, then retry. Exactly one process, and both replies name
  the same run.

**6. Acknowledgement lost, broker to coordination (crash point C2).**
- **Guaranteed:** every event has a per-run sequence number. Coordination stores the last one it applied in
  the same room.db transaction as its projection, and after reconnect it asks for events after that number.
  No **retained** event is lost or applied twice.
- **Not guaranteed:** events that were not retained, such as the tail lost in a broker crash (b, Output). Those
  are an explicit gap, partly reconstructable only where (b) Recovery can verify it, and never "filled" by
  assumption.
- **Check:** drop the connection after the broker sends event `k` and before coordination commits it. After
  reconnect, event `k` is applied exactly once.

**7. Controller restart during input delivery (crash point C3).**
- **Guaranteed:** input commands keep their ids and states (b). Coordination never resends a `sent` or
  `delivered` command by itself. Pending delivery stays shown as pending until reconciled.
- **Not guaranteed:** delivery of a command in `write_attempted` or `sent` with no confirmation; it becomes
  `unknown`. Nothing is resent automatically unless the provider deduplicates that command is established
  ((b), Input).
- **Check:** restart coordination between the pipe write and the provider's echo. The steer appears once in the
  provider transcript, and the room shows it as delivered or unknown, never as two messages.

**8. Stop.**
- **Guaranteed:** `stop_requested` is committed first. The broker sends the provider's interrupt, waits a
  bounded grace, then terminates the attempt's whole containment. The run ends `exited/stopped` only once the
  containment is proven empty; until then it shows "Stopping". Stop reaches a
  run whose controller restarted, because the broker owns it. If the broker is unreachable, Stop stays durable
  and pending, and the UI shows "Stopping, control unavailable", never "Stopped".
- **Not guaranteed:** that the current tool call finishes cleanly before the forced kill.
- **Check:** Stop during a long tool call on each provider. No member remains after the grace plus the kill
  timeout. With the broker down, the run never reads "Stopped".

**9. Quit (desktop).**
- **Guaranteed:** Quit is an intentional shutdown, and the intent to stop is persisted (systems, finding 6).
  - Coordination commits a `stopped_by: quit` record on every active intent, then tells the broker to stop every
    run, as Stop. The broker exits after that.
  - On the next launch, runs stopped by Quit or by Stop are **never** resumed automatically. The room's
    interrupted-task resume rule applies only to runs interrupted by an install or a failure, never to an
    intentional stop.
  - Failure recovery never restarts anything implicitly. Each new attempt is an explicit decision under that rule.
- **Not guaranteed:** survival of running turns. Quit is not a crash.
- **Check:** Quit with a turn running, then relaunch. No member survived, the run reads `exited/stopped`, and
  no new attempt starts for it on relaunch.

**10. Window close.**
- **Guaranteed:** closing a window disconnects one UI client. Coordination and the broker keep running.
- **Not guaranteed:** nothing about the run changes.
- **Check:** close the window mid-turn and reopen it. Same run, and output continues from the snapshot cursor (b).

**11. Install.**
- **Guaranteed:**
  - Coordination drains (today's 30 s, then interrupt), and the broker receives Stop for any remaining runs.
  - The broker is replaced only after **every** active or uncertain attempt is reconciled to a proven terminal
    state: launch_attempted, running, stop_requested and unresolved alike. If any attempt can't be reconciled,
    the install is refused or deferred under section (e). The old broker is never replaced while it may still
    own live work.
  - The new broker reads runs.db by declared schema version and takes a new epoch.
  - Resuming afterwards follows the existing install policy: today, an interrupted turn on the owner's own task
    resumes once, unless that task is finished, the agent is stopped or the user pressed Stop. It is never an
    unconditional new attempt.
- **Not guaranteed:** live runs surviving an install.
- **Check:** install with a turn running, and again with an `unresolved` attempt present.
  - In the first case, the old run ends `exited/stopped`, at most one new attempt follows the policy, and no
    orphan remains.
  - In the second, the install is refused or deferred, and the broker is not replaced.

**12. room.db and runs.db restored to different generations.**
- **Guaranteed:** each database records a generation id, and each side records the other's last-seen generation.
  On a mismatch, the broker suspends new dispatch, and coordination shows dispatch as suspended. Coordination
  then reconciles:
  - Runs that coordination doesn't know are imported as `unresolved`.
  - An intent with no runs.db row stays `unresolved` with dispatch suspended. If runs.db is the older copy, its
    missing row may describe an agent that already ran and changed files, so a missing record never proves
    non-execution (systems, finding 2).
  - An intent becomes `never_dispatched` only on positive proof that no attempt began. Absence from a restored
    runs.db, or from the current epoch's journal, is never proof. An intent may have been accepted and launched
    after the backup was taken, before the restore and the new epoch. The only acceptable proofs are:
    - retained authoritative evidence covering the whole original dispatch interval. For example, coordination's
      own room.db outbound log shows the intent was never sent to any broker, and that log is not itself the
      restored copy; or
    - the intent was first created after dispatch was frozen for this recovery.

    Otherwise it stays `unresolved`.
  - Detailed reconciliation and rollback are section (e).
- **Not guaranteed:** recovering runs that one side never recorded.
- **Check:** restore runs.db to a backup older than an intent that did run. That intent reads `unresolved`, and
  nothing is dispatched for it until a person or its owner decides.

**Named crash points.** QA #17840 asked for these in test builds, so faults are injected at a named step
rather than on a timer:
- **B1:** before accepted.
- **B2:** after accepted, before launch_attempted.
- **B3:** after launch_attempted, before running.
- **B4:** while running.
- **B5:** after exit, before the exited commit.
- **C1:** coordination dies mid-run.
- **C2:** after event send, before coordination's commit.
- **C3:** during input delivery.

Each crash point's allowed outcomes are the rows above.

### (b) Bidirectional streaming contract

#### Snapshot and subscription boundary (frontend d64409bf)

- **Snapshot cursor:** a snapshot names its authoritative revision, `(stream_id, generation, cursor)`.
  `stream_id` identifies the run's output stream, and `generation` increments whenever its broker or
  connection is replaced.
- **Subscription:** a subscription resumes **strictly after** that cursor. If the cursor is no longer
  retained, the reply is `resync_required`, never a silent jump.
- **Stale events:** events carry `(stream_id, generation, seq)`. A receiver rejects any event from an older
  generation, so a delayed event from a replaced connection can't overwrite current state.
- **Same rules everywhere:** the same boundary applies between broker and coordination, and between
  coordination and UI clients.
- **Not guaranteed:** that a client sees intermediate states it was away for. It sees the snapshot plus later
  events.
- **Check:** change a run while its snapshot is loading, then deliver an old connection's event. The UI
  converges to the current authoritative state without losing the change or reverting it.

#### Output

- **Where it is kept while coordination is down:**
  - The broker appends each raw provider frame, unchanged, to a per-run spool with a monotonic sequence number.
    The broker is the only writer.
  - The spool is split into segment files, and runs.db records each run's last durable sequence number.
  - Writes are group-committed: an fsync at most every 100 ms or 64 KB, both named constants. A broker crash can
    lose the unsynced tail, and that tail is reported as an explicit gap, never assumed recovered.
- **Who classifies, and when a frame may be evicted (systems, finding 3, r4):**
  - The broker does not understand provider semantics. Adapters stay in coordination, so CLI changes never need
    a broker rebuild.
  - So **every raw frame is non-disposable until coordination's adapter has durably classified it.**
  - Coordination acknowledges in two parts:
    - `classified_to k`: up to sequence `k`, it has stored its projection and adapter snapshot in room.db.
    - A set of disposable ranges: text and partial-message deltas.
  - Only frames inside an acknowledged disposable range may be evicted. Control and execution frames are
    retained in the spool, under the same caps, until `classified_to` covers them and coordination has stored
    them. They cover approval and permission requests, tool-call starts and ends, session id, turn start and
    result, errors and exit.
  - While coordination is down, nothing is classified, so nothing is evicted.
- **Retention and replay window:**
  - An acknowledged-disposable segment is kept for a short replay window, 10 minutes, so that reconnecting UI
    clients can catch up.
  - Every other frame is kept until classified, and at most 24 hours after the run exits.
  - Caps are 64 MB per run and 512 MB per broker, and every frame counts toward them, including control
    frames. They are named constants, reported in the run state, and set from QA's matched baseline (open
    point 3).
- **Overload: the explicit bounded policy when a cap is reached with unclassified frames.** "Never block stdout",
  "never drop unclassified frames" and "finite storage" cannot all hold indefinitely, so the broker chooses in
  this declared order:
  1. It evicts acknowledged-disposable segments first, writing an overflow record `(gap_from, gap_to)` for any
     range a client had not yet read.
  2. If the cap is still reached, it enters **overload**. It stops reading that run's stdout, which pauses the
     provider at its next write, and the control axis reads `overload: output paused`. Nothing unclassified is
     dropped, and Stop stays available.
  3. If coordination does not return and classify within `OVERLOAD_GRACE` (15 minutes, a named constant), the
     broker Stops the run, following row 8. The run ends `exited/stopped: overload` once its containment is
     proven empty. Its retained frames stay, and the user decides about a new attempt.

  The policy sacrifices liveness, never the record.
- **Recovery after a gap is capability-specific and verified, never assumed.** A gap can only be the lost
  unsynced tail of a broker crash, or a range a client had not read before its window expired, because
  unclassified frames are never evicted.
  - Coordination that meets a gap stops replaying, then attempts resync: it rebuilds what it can from the
    retained control frames plus the provider's own durable session (the Claude session JSONL, or the Codex
    thread read by cursor).
  - A provider transcript need not hold every protocol message or map exactly onto spool cursors. So each
    capability (transcript text, open tool calls, pending approvals, the current turn id for steering) is restored
    only if the rebuilt state can be verified against the retained control frames.
  - What can't be verified is reported as such. Output completeness reads "gap `[a, b]`, not recoverable" or
    "summarised from provider session". The control axis reads `control_unavailable` for the affected
    capability, and input that depends on it is refused: no approval answer for an unverified request, no steer
    against an unverified turn.
  - **Stop always stays available**, because it acts on the containment, not on adapter state.
  - The transcript is never presented as a complete recovery, and no event-loss guarantee is made from the
    transcript alone (frontend's revision 2 dissent).
- **Bounded buffering:** a single provider line or frame larger than the adapter's stream limit (today 4 MiB,
  `stream_limit=2**22`, and Codex's `CODEX_MESSAGE_LIMIT`) is recorded as a framing error. It is never buffered
  without bound. A partial line at the spool's tail counts toward the same limit.
- **Adapter state on a partial stream:**
  - Adapters are deterministic functions of (snapshot, events after it). The adapter snapshot holds the open
    message id, partial text, open tool calls, pending approvals and the steer uuids sent.
  - Coordination stores that snapshot in room.db with the sequence number it covers. Replay starts from the
    latest snapshot.
  - A partial line at the spool's tail is ignored until it is complete.
- **Screens (motion-design #17839):**
  - Replay keeps the record complete, but the UI does not play it back. After a gap, Board, Live and Follow
    show the snapshot plus a summary ("while disconnected: 3 tasks moved, 412 output lines").
  - Only events from the last few seconds animate.
  - Missing history is labelled with its time range, for example "output between 14:02 and 14:20 not kept".
  - `unresolved` never looks like `running`.
- **Not guaranteed:**
  - A recovered transcript for a gap. One may not exist.
  - Byte-identical streaming text across a gap.
  - Liveness under overload: the run pauses, then stops.
  - Output from a provider that crashed before flushing its own stdout.
- **Checks:**
  - **Within retention:** hold coordination down for less than the window, while a real provider writes less
    than the cap. After reconnect, sequence numbers are complete, with no gap or repeat.
  - **Beyond the cap:** hold coordination down while the provider writes more than the cap.
    - The broker enters overload and pauses output, and it evicts no unclassified frame.
    - Stop works throughout.
    - After `OVERLOAD_GRACE`, the run ends `exited/stopped: overload` with its containment proven empty.
    - After reconnect, the room shows **honest incomplete recovery**: labelled gaps where data was not retained,
      `control_unavailable` for unverified capabilities and refused dependent input, with no replay animation.
      It does not require a complete transcript.
  - **Broker crash between fsyncs:** the lost tail appears as a labelled gap, never as continuous output.
  - **Random coordination kills during replay:** the applied sequence numbers are strictly increasing, with no
    repeats.

#### Input

- **Commands:** stdin message, approval answer, steer, interrupt and close. Each has a command id from
  coordination, committed to room.db before it is sent.
- **Ordering:** the broker writes commands for one run in the order it accepts them, one at a time. An approval
  answer goes to its `request_id` regardless of order. Interrupt jumps the queue, and is the only command that
  may.
- **A durable broker state machine (systems, finding 4).** Each command is a runs.db row keyed by command id,
  and every transition below is committed with `synchronous=FULL` before the effect it guards:

  | State | Committed | Meaning |
  |-|-|-|
  | `accepted` | Before the acknowledgement to coordination | The broker holds the command |
  | `write_attempted` | Before the first byte goes to the pipe | A write may have begun |
  | `sent` | After the write returned with every byte written | The provider may have read it |
  | `delivered` | When the provider's own confirmation arrives | Confirmed: the Claude `isReplay` echo of the uuid, a Codex `turn/steer` reply with a matching `turnId`, or the reply to an approval's `request_id` |
  | `rejected` | When the provider refuses it | For example a steer after the turn ended, or a Codex `expectedTurnId` mismatch |
  | `unknown` | On any ambiguous outcome | A zero-byte or partial write, a write error, or `write_attempted`/`sent` with no confirmation when the process or the connection ended |

  - **One write per command:** a command id that has reached `write_attempted` is never written again by the
    broker. A second accept of the same id with the same payload returns its current state. The same id with a
    different payload is rejected as `command_conflict`.
  - **No final `sent` without evidence:** where a provider gives no confirmation for a command type, `sent` is
    its last state, and the contract lists those types per provider. Delivery stays shown as pending until an
    authoritative state arrives (design 6f1fec26).
- **Disconnect during delivery:**
  - Coordination re-reads each pending command's state by id. It never resends on its own.
  - A broker restart turns every `write_attempted` or `sent` command for a lost transport into `unknown`.
  - **No automatic resend of an `unknown` command.** A resend is allowed only for a command type whose provider
    deduplication is established by an executable compatibility case, for the exact CLI version in use. Today
    that may cover a Claude steer with its original uuid (the adapter relies on it at `native_adapters.py`'s
    steer), but an echo alone is not proof. Until such a case passes, Claude steers are not resent automatically
    either.
  - An approval whose process is gone is void. The new attempt asks again.
- **Orderly close:** close is the last command. The broker closes stdin, waits the exit grace, drains stdout to
  the spool (as `terminate(grace)` does today) and verifies the containment is empty, then commits `exited`.
- **Backpressure:** at most 32 queued commands per run. Beyond that, coordination is told `busy`, and nothing is
  silently dropped.
- **Not guaranteed:** that a steer lands in the provider turn it was written for (providers choose turn
  boundaries), or that an interrupt arrives before the current tool call finishes.
- **Checks:**
  - Send a steer, then a duplicate accept with the same command id, on each provider. It is written once.
  - Answer an approval after a coordination restart. The provider receives exactly one answer.
  - Kill the broker at `write_attempted` and again between `sent` and `delivered`. Both read `unknown`, and
    nothing is resent automatically.
  - The provider-dedup compatibility case: write a Claude steer twice with the same uuid on the pinned CLI
    version. Only if it appears once in the session may that command type become resendable.

### Open points

1. **POSIX no-survivor mechanism:** a broker watchdog, `PR_SET_PDEATHSIG` on Linux, or a restart sweep only. The
   requirement is fixed above. The mechanism is decided by the Linux spike in section (d), together with how
   descendants that leave their process group are found.
2. **Per-OS identity and marker:** the process identity fields per OS, and whether macOS needs the launch marker
   in arguments as well as the environment, given what other processes can read.
3. **Spool limits:** the caps and the 24 h retention, to be set from QA's matched baseline.

---

## Architecture addendum, sections (c) and (e)

Author: backend. Requested by opus in follow-up f871f926 for Zero's review #17832 (decision 25a609c2).
Specification only: nothing here is built. Code references are to installed 0.12.64 (617a24a).
Revision 4: systems' review e99bccb6 corrections to e1 (no relaunch while ownership is unresolved; no same-ID re-dispatch inferred from a missing row) and G2 (a web page can't read the minted token), and the c5 agent row qualified. Revision 3 integrated desktop's task 2c640e16 text (`desktop-architecture-amendment/backend-c-e-suggestions.md`, sha256 4f6220e8…390b) and harness's corrections (follow-up bf29703e). The one item still marked **[astra]** needs that owner's confirmation before the addendum is final.

---

### (c) Security and authorization boundary

#### What exists today (read from 0.12.64)

| Control | Where | What it does |
|-|-|-|
| UI token | `run.py:202`, `ui_sessions.py:127` | 32-byte random per start, handed to the room by the supervisor over stdin, never written to disk |
| Token-free web page | `run.py:259` | the web room's `/` carries no token; browsers sign in through a one-time `/launch#code` |
| Browser session cookie | `app.py:362` | HttpOnly, SameSite=Strict, valid only at the exact room host, never for the routes that mint launch codes |
| Token comparison | `app.py:178` | `hmac.compare_digest` |
| Host allowlist | `app.py` TrustedHostMiddleware, after `:300` | loopback names plus configured `allowed_hosts`, no wildcards; blocks DNS rebinding for HTTP and WebSocket |
| Origin allowlist | `app.py:202-261` | any Origin outside the room's own is refused; cookie requests also need `Sec-Fetch-Site: same-origin` and an Origin on writes |
| WebSocket | `app.py:871-886` | token, or cookie plus Origin equal to the room origin; no CORS anywhere |
| Run capability token | `native_adapters.py:107-110`, `app.py:183` | per turn, reaches only that run's own coordination route and image upload, only while the run is active |
| Provider environment | `native_adapters.py:16,35-50` | agent CLIs get an allowlisted environment; API key variables are always stripped |
| API keys | `provider_keys.py:1-12` | kept in the OS credential store only; agents use them through the relay with a per-turn relay token (`app.py:248`) |
| Agent registration | `app.py:238`, `:1363` | loopback address only, no token (see gap G2) |

#### c1. Authenticated local clients

**Room API (HTTP and WebSocket).**
- **Guarantee:** every API and WebSocket call is made by one of these:
  - a holder of the UI token;
  - a browser holding a valid session cookie at the exact room host, from the room's own origin;
  - a running turn's capability token, on that turn's own two routes only.
- **Not guaranteed:** protection from a process running as the same OS user. Such a process can read other processes' environment and memory, the browser's cookie store and the room's files. Agents are such processes (see c4).
- **Check:** one request-matrix test against the real middleware (FastAPI TestClient). Missing, wrong and query-string tokens, a foreign Host, a foreign Origin, a cross-site `Sec-Fetch-Site`, a cookie at the wrong host, and a run token on another route must all return 403; each allowed path must return 2xx. There's no such test today. Then a live curl of three refusals on the installed package.

**Broker command channel (new).**
- **Guarantee:** only the coordination server can send execution commands. Transport:
  - **Windows:** a named pipe whose DACL grants only the current user;
  - **macOS and Linux:** a Unix socket in a `0700` directory;
  - **never** a TCP port.
  
  On connect, coordination presents a per-boot secret. The process that starts both hands it over on stdin, as the UI token is today. Every command carries an intent ID and coordination's boot ID. A broker reconnect credential is tied to its room and broker generation, so restarting coordination alone doesn't invalidate a surviving broker's reconnect path; revocation and generation changes are explicit.

  **Shell authority (desktop, 2c640e16).** In desktop mode the Electron main process may start coordination and the broker. Only coordination may authorize execution and send launch or provider-input commands. A separate shell credential permits status and a scoped shutdown request for this room when coordination is unavailable. It can't launch, approve tools, edit policy, migrate databases or install a replacement broker.
  - A deliberate menu or tray Quit may request admission fencing and Stop of this room's owned work. Status distinguishes requested, stopping, confirmed exit and unresolved. The shell never reports completion just because coordination disappeared or a deadline elapsed.
  - Broker and rescue credentials stay in the privileged main or launcher process. They are never exposed through preload, a renderer global, guest browser views, provider output, the agents' inherited environment, command-line arguments or logs.
  - The application renderer is an authenticated UI client that reaches execution only through coordination. Privileged IPC validates the sender WebContents, the main frame, the exact trusted document and a closed set of operations. Remote or agent-controlled browser content has no privileged preload bridge. contextIsolation, sandboxing and disabled Node don't by themselves make a compromised application renderer harmless.
  - **Check:** wrong room or instance, a missing or stale credential, and the shell credential used for launch, input, approval or install are refused before any effect. With coordination disconnected, an explicit Quit's Stop reaches the same broker-owned run; unresolved teardown is tested separately. An untrusted frame, popup, guest view or navigated document can't invoke shell operations or obtain shell or broker credentials.
- **Not guaranteed:** protection from a same-user process with debugging rights over coordination's memory.
- **Check:** each of these is refused:
  - a connection without the secret;
  - the secret replayed from a previous boot;
  - a command without an intent ID;
  - the shell credential trying to launch;
  - a different OS user on the Linux VM QA has (WSL2).

#### c2. Cross-origin WebSocket and CSRF

- **Guarantee:** a page from another origin can't change room state or open the event stream.
  - **Cookie path:** SameSite=Strict, plus `Sec-Fetch-Site: same-origin`, plus an Origin on every write and on the WebSocket.
  - **Token path:** a custom header a cross-origin page can't send without CORS, which the room never grants.
- **Not guaranteed:** hiding that the room is listening. Connection timing to the port is observable.
- **Check:** covered by the c1 matrix, plus two WebSocket cases: a foreign Origin with a valid cookie, and no token. Both must close with 4003.

#### c3. Which components may submit execution

| Component | May do |
|-|-|
| Coordination server | the only sender of launch, input, interrupt and stop commands, after its own checks (Stop latch, admission, budgets, policy) |
| UI (browser or desktop) | asks coordination; never talks to the broker |
| Agents | reach the room only through coordination with their run token; never the broker |
| Desktop shell (Electron main) | starts coordination and the broker; status and a scoped shutdown request for this room when coordination is unavailable; never launch, input, approval, policy, migration or broker install |
| Media workers | report through MediaJobs; never submit agent runs |

- **Guarantee:** a launch exists only if coordination recorded its intent first.
- **Check:** the broker's journal has no run without an intent ID, and the shell credential's launch attempt is refused (c1 check).

#### c4. Provider processes: paths, secrets and MCP, at least privilege

- **Guarantee (today, kept):**
  - agents get an allowlisted environment;
  - API keys never enter an agent's environment, files or chat;
  - an agent's room tools act only as that agent, on its own run route, while its turn runs;
  - the working directory is the agent's own worktree.
  
  In the new design (confirmed by harness, bf29703e), coordination mints the run and relay tokens and passes them in a separate `secrets` field of the launch command. That field is excluded from the intent-payload hash, so a retry with re-minted tokens is not an `intent_conflict`. The broker never journals or logs it and places it only in the child's environment. Because the broker keeps no secrets, an intent accepted but not launched before a broker restart waits for coordination's retry to supply them again. The broker never launches without them.
- **Not guaranteed, stated plainly:** an agent is not confined to its worktree at the OS level.
  - With automatic tool approvals, Codex runs with `approval_policy="never"` and `sandbox_mode="danger-full-access"` (`native_adapters.py:456`), and Claude with `bypassPermissions` (`:688`).
  - So an agent can read and change anything the user can: other agents' worktrees, the room's data folder, the installed room code and the CLI homes.
  - The room's authorization limits what an agent does *through the room*, not on the machine.
- **Check:** a test that the launch environment contains only allowlisted names plus the run's own tokens, with no API key variable. An existing helper (`agent_env`) makes this a unit test.

#### c5. What a malicious local web page or another local app can and can't do

| Actor | Can't | Can |
|-|-|-|
| Web page from any other origin | read the token (the web page has none), pass the Host or Origin checks, use the cookie, open the WebSocket, read API responses (no CORS), rebind DNS onto the room | see that something listens on the port; display an `/uploads/` image if it already knows the random file name; **on 0.12.64 only: register a room agent's ID and rename the live agent (gap G2), closed by `7bcc440`** |
| Local app, different OS user | use the API without a token (403), open the broker pipe or socket | **register a wrapper agent and post chat messages (gap G2)** |
| Local app, same OS user | (none; outside the boundary) | anything the user can, including reading room files and agents' environments |
| Agent process | through its assigned capability and the supported interface: call the broker, or use another agent's run route | anything the user can on the machine (gap G1), including reading other credentials. This row describes the room's interface, not OS isolation (c4) |

#### Gaps against today's code

| Id | Gap | Severity | Proposed fix |
|-|-|-|-|
| G1 | Agents run with full user rights (c4) | Highest. It's a product choice, so it goes to Zero | options to weigh: Codex `workspace-write` instead of full access, Claude permission allowlists, or a separate OS account for agents. Each one costs capability |
| G2 | `/api/register`, deregister and heartbeat return at the loopback branch (`app.py:238-246`) before the Origin check (`:253`), and `register_agent` parses the body whatever its Content-Type. Every room agent's ID is a registrable base (`registry.py:194`). So **any web page** in the user's browser can send a no-cors text/plain POST that makes the server rename a live room agent (backend to backend-1) and migrate its sender history. The response is opaque to a cross-origin page without CORS, so the page can't read the bearer token the server mints (systems, e99bccb6). A non-browser local process, including another OS user's, can do the same and also read that token, then post to `/api/messages` and `/api/send` as the new instance. Evidence: an isolated request-path reproduction in a TestClient on the 0.12.64 code. It is not a live browser or installed-release test | Critical (server-side rename from a web page); high (local token minting) | task 6be90c9a. Patch 1 (`7bcc440`): refuse any Origin on these routes and refuse registering a room agent's ID. Patch 2 (next): a launcher secret in a current-user-only file, so an unauthenticated local process can't register at all |
| G3 | Outside the web room, HTTP still accepts `?token=` (`app.py:288-290`); the WebSocket takes the token in its query (`:875`); accepted desktop64 exposes the UI token to its trusted renderer through `preload.cjs` | Low (local logs and renderer exposure) | target, not yet built (desktop): header-only bearer tokens for HTTP; for the application WebSocket, a room-scoped HttpOnly session cookie with exact Host and Origin checks, which the shell establishes through a narrowly scoped bootstrap. The reusable UI token goes in no URL or renderer global. A subprotocol alone doesn't prevent logging. **Check:** valid same-origin clients work; query-token-only HTTP and WebSocket, a foreign Origin, the wrong host or session, and an unauthenticated WebSocket are refused; URL and log fixtures show no credential |
| G4 | No broker channel exists yet | none until built | as c1 |
| G5 | No middleware tests | process gap | the c1 matrix test |

---

### (e) Data and upgrade safety

#### e1. When room.db and runs.db disagree, and rebuilding projections

- **Authority:**
  - `room.db` decides intent and policy: what should run, and whether it's allowed.
  - `runs.db` decides execution facts: accepted, launch attempted, launch confirmed, exited, and the exit outcome.
  
  Neither side reads the other's file; they talk only through the contract.
- **Projections:** every execution state coordination shows (a task's running chip, its execution_state) is a projection stored in `room.db` with the last broker event sequence it applied. It's disposable: it can be rebuilt from `room.db` intents plus a broker snapshot, never the other way round.
- **Reconciliation, on coordination start or broker reconnect:** coordination asks for events after its cursor. If the broker no longer holds them, it asks for a snapshot covering every intent coordination thinks is in flight. Then:

| room.db intent | runs.db | Result |
|-|-|-|
| sent | no record | `launch outcome unknown`. A missing row, especially in a restored runs.db, never proves the launch didn't happen. Dispatching the **same** intent ID again is allowed only with authoritative non-dispatch evidence covering the original send interval (coordination's own non-restored outbound log), or for an intent created under the recovery dispatch freeze, as harness's (a) r4 row 12 requires |
| sent | launch attempted, not confirmed | unresolved: shown, and its ownership reservation is held. It allows reconciliation and Stop. A relaunch is a new, explicitly authorized attempt that's only possible after verified full teardown of the earlier attempt's tree, never a way around the ownership proof |
| sent | confirmed or running | projection updated |
| sent | exited | projection updated with the outcome |
| none | any record (orphan) | reported to the lead; the broker keeps containing it, and Stop works |

- **Guarantee:** no automatic second launch from a disagreement, and every disagreement is visible.
- **Not guaranteed:** atomic transactions across the two files, or exactly-once external side effects (systems' section (a)).
- **Check:** a unit test per row above against the reconciliation function. Then QA's named crash points on the installed package, labelled crash recovery, not power-loss proof (systems' correction).

#### e2. Schema migrations

- **Rules:**
  - Each file records `PRAGMA user_version` (its schema version) and a `min_reader` value: the oldest release that may still open it.
  - Only the file's owner migrates it, at startup, in one transaction, after an online backup made with SQLite's backup API (named by from and to versions, kept until the next successful install).
  - Releases **expand** (add tables, nullable columns or record kinds) before they **contract** (rename, drop, change types) in a later release, once nothing reads the old shape.
  - `min_reader` records a **verified** compatibility promise: the oldest release proven to represent and preserve every current record kind, field and value. It is not inferred from the absence of a dropped column, because an older reader may reject a new event kind, rebuild a record without its unknown fields, or misread a new value (desktop, 2c640e16).
  - Each record keeps its full JSON document beside its indexed columns, and code preserves keys it doesn't know. Today's dict-based records already behave this way, and a test keeps it so. That helps compatibility but doesn't by itself prove it.
- **Guarantee:** a migration either completes or leaves the file as it was. A release refuses to open a file whose `min_reader` is newer than itself, and says so, rather than misreading it.
- **Not guaranteed:** that adding unknown keys makes every older package lossless, or that every contract migration can roll back without an export (e3).
- **Check:** migrate a copy of the live data in an exact-package throwaway room. Every ID and record count must be equal, and an older build must refuse a raised `min_reader` with that message.

#### e3. Rollback that keeps newer records, with writers switched over safely

- **Single writer at every moment:** each file has exactly one writing process. Writers switch only after the current writer is fenced: for room.db, inside an install's writes-blocked window after admission closes and turns drain (`release_maintenance.py:93`). A room-writes block alone doesn't quiesce a surviving broker writer, so runs.db ownership and the active broker's compatibility are confirmed separately (e4).
- **Before rollback**, the target package must be shown to represent and preserve current record kinds, fields, IDs, cursors and unresolved execution states. A target that can't refuses the rollback without changing current data.
- **Rolling back within SQLite releases:** installing the previous package is allowed only when its version is at or above the verified `min_reader`.
- **Rolling back across the cutover (SQLite back to JSON) or a contract migration:** the *newer* release exports the current committed data in the older format after its writer is fenced, then ownership switches once and the older package is installed. It is never a restore of the pre-migration backup, so writes made after the cutover survive. Deliberately unsupported data causes a refusal, never field deletion.
- **Guarantee:** a rollback either preserves every record written after the cutover or is refused with current data unchanged.
- **Not guaranteed:** that rollback undoes anything outside the room (git pushes, files agents changed, messages sent).
- **Check:** in a throwaway room, a populated round trip:
  1. install N and write records of every kind, including new kinds and fields and unknown or unresolved states;
  2. export and install N-1, then compare identities and complete record contents;
  3. reinstall N and check nothing was lost;
  4. seed deliberately unsupported data and confirm the rollback is refused, not lossy.

  **[astra]** Confirm this fits the release tools' existing rollback path with no new machinery (follow-up 772b1a75, open).

#### e4. Broker lifecycle and upgrades

This section is desktop's text for task 2c640e16, integrated here, with harness's corrections (bf29703e).

**Authority and lifetime.**
- The web launcher or supervisor, or Electron main in desktop mode, starts the service lifecycle. Closing or hiding a window is not service Quit. Coordination may restart on its own while a compatible broker stays alive. A broker or launcher failure is a separate failure case under (a); shell ownership doesn't promise survival of a shell crash.
- The broker alone owns provider handles, pipes and its run journal. The lifecycle manager alone may replace its executable, after authorized maintenance.
- Payloads live in immutable versioned directories, recording the selected binary's digest and the broker generation. The active Windows executable is never overwritten, and a payload is never deleted while an owned process still uses it.
- **Guarantee:** admission authority, the endpoint and journal writer ownership have exactly one active generation, and a second broker refuses before launching anything.
- **Not guaranteed:** that a SQLite lock, a PID or an expired heartbeat alone proves an old launcher is fenced.
- **Check:** concurrent startup, a stale generation and a failed shutdown can't produce a second launcher or writer.

**Compatibility negotiation.**
- An authenticated handshake exchanges broker generation, protocol ranges, capabilities and journal and spool schema compatibility. No execution or migration starts without a supported intersection.
- A separately versioned, authenticated maintenance and status path stays available to the lifecycle owner, with its supported installed pairs written down. If it can't control an incompatible old broker, replacement is refused and a visible recovery condition is kept. No frozen command is claimed to control every future version.
- An unchanged, compatible broker may stay alive through a coordination-only update. Its retained output and input reconciliation follow (b); protocol compatibility alone doesn't prove gap-free replay.

**Replacement sequence.**
1. Record the authorized maintenance operation, and fence launch admission at both coordination and the broker. Queued intents are kept durably with their original IDs, never dropped or delivered to two generations.
2. Drain the old broker. Natural completion is the default. If the authorized maintenance explicitly permits interruption, request Stop through the old broker. An elapsed drain deadline is a refusal or hold, not evidence of termination or authority to start a replacement.
3. Get a drained receipt only once all owned processes and descendants are confirmed exited, in-flight input outcomes are resolved or explicitly unknown, output is retained with its final cursors and gaps, and critical journal records are committed at the agreed durability setting. A turn is recorded as `interrupted by install` only after proven teardown of its whole owned tree (Windows job object empty; the POSIX mechanism from section (d) open point 1). Otherwise it stays `unresolved`, keeps the ownership reservation and blocks replacement.
4. Stop the old broker, verify the process actually ended, and release its endpoint and journal ownership. The lifecycle manager records the cutover and activates only the verified selected payload. The new broker takes ownership with a new generation before enabling admission.
5. Reconcile coordination intents with execution facts before accepting launches. A new attempt after an interruption is separate from retrying a command delivery. It follows the contract's explicit authorization and unresolved-side-effect rules, inside today's install-resume policy (the owner's own task, not finished, the agent not stopped, the user didn't press Stop). That policy is a filter, not evidence that automatic provider-session replay is safe. Runs stopped by Quit or Stop are never resumed. Resuming uses the provider's saved session (`native_adapters.py:505` and `:695`) as a new attempt.

- **Guarantee:** a successful upgrade leaves one broker authority and never automatically relaunches an uncertain attempt.
- **Not guaranteed:** that ordinary stdio survives broker replacement, that resuming a provider session reattaches to the old process, or that tool side effects happen exactly once. No broker-changing upgrade claims uninterrupted execution.
- **Check:**
  1. A coordination-only restart keeps the same broker generation, run and control, with stream recovery under (b).
  2. Broker replacement with active work fences new launches, waits for confirmed cleanup, and changes generation exactly once.
  3. A stuck descendant, a lost drained acknowledgement, an incompatible protocol or an expired deadline leaves replacement blocked and visible.
  4. Kill the updater before and after the old broker exits, and before and after the new one takes ownership. Recovery from the recorded cutover launches no unresolved work.

  These are crash-recovery checks, not power-loss proof.
- **Platform limits, owned by (a) and (d), not solved by a language choice:** Windows kill-on-close applies only when the last job-object handle closes, so the design must prevent unintended inherited or duplicated handles and breakaway paths before claiming teardown on broker death. POSIX process groups can't cover every daemonized descendant (`setsid`).

---

## (d) Cross-platform feasibility and measurable targets

Draft r2 for Zero's review #17832, decision 25a609c2, task 07bd2379.
Authors: systems, incorporating QA follow-up 404f8be5. Specification only: no checks below
have been run for this addendum. Numbers are proposed acceptance targets, not
achieved results or existing product promises. Sections (a), (b), (c) and (e)
remain authoritative for lifecycle, streams, authorization and upgrades.

### Sequence and evidence boundaries

Freeze the comparison criteria and workload below before the broker spike.
Check the native ownership mechanism on Windows and Linux before selecting an
implementation. A Linux VM or WSL2 can establish Linux process behavior; record
the kernel, distro and WSL version. It cannot establish Linux desktop packaging,
accessibility or display integration. WSL1 is not interchangeable evidence.

Run the same minimal broker checks on macOS as soon as a Mac is available,
before expanding the broker beyond the first provider path. If hardware is
unavailable, label macOS feasibility unproved and the language choice provisional
for macOS. Do not silently waive that condition or advertise support. Full native
packaging and desktop acceptance can follow later on each OS. No hosted runner,
new CI pipeline, machine rental or installation is authorized by this document.

Existing checks and fixtures should be extended only where necessary to exercise
the named failure. Manual evidence is sufficient at this stage. A stand-in proves
the mechanism; the first real provider must repeat applicable checks before that
path is enabled by default. No paid provider use is implied.

### Minimal native feasibility matrix

| Item | Required result | Explicitly not claimed | Smallest check |
|---|---|---|---|
| Startup and ownership | Exactly one broker generation may launch for the room. A stale controller or concurrent second broker is refused before an effect. | A database writer lock alone fences every possible old launcher. | Start two fixture controllers; race broker acquisition; restart the controller while its broker survives. Observe run identity and launches. |
| Launch boundary | Durable attempt precedes process creation; child ownership is established before uncontrolled work can execute. | SQLite and OS process creation form one atomic transaction. | Interrupt at named points before attempt commit, before creation, after creation and before identity commit; apply section (a)'s allowed outcomes. |
| Windows containment | Owned descendants remain in the intended Job Object; Stop reaches that job; broker death follows the declared kill-on-close policy. Verify complete teardown before release. | Finding two PIDs proves complete containment, or inspection-handle closure kills the job. | Fixture child creates a grandchild, attempts breakaway and inherits only intended handles; inspect membership, terminate broker and observe all members exiting. Include launch failure cleanup. |
| Linux/macOS containment | Record process/session identity; Stop cleans the supported owned tree, or reports unresolved and keeps its reservation. | A process group is a security boundary or necessarily contains every later descendant. | Ordinary child/grandchild plus a child attempting new session/group, double fork and ignored graceful signal. Check declared handling of escaped members; no guessed-PID kills. |
| Escape limitation | An escaped descendant cannot produce a false "all exited" or free admission while it may survive. The adapter must either enforce ownership for the supported launch path or refuse that stronger guarantee. | A passing ordinary process-group kill covers daemonized or indirectly launched processes. | Deliberately escape the group; demonstrate containment, proven cleanup, or explicit unresolved/refusal. A known escape fails support for a provider that requires that launch path. |
| Pipes and controller loss | With broker alive, the same run retains its transport and recovers output and input control under section (b). | Broker death preserves ordinary stdio, or process survival alone proves continuation. | Disconnect coordination for a bounded interval, replay output, reconcile an in-flight input and then Stop the same run. Kill the broker separately and apply (a). |
| Permissions and endpoint | An ordinary user can run the supported broker; endpoint permissions and peer authorization match section (c). Failures are visible before an unauthorized launch. | Administrator/root requirements are acceptable merely because a fixture can run elevated; same-user hostile software is excluded automatically. | Run without elevation; check denied execution, unwritable state directory, wrong peer and stale endpoint without touching real credentials. |
| Cleanup and quit | Explicit Stop/Quit drains or terminates owned work within the deadline; unresolved work stays visible; owned sockets, handles and scratch resources are released. | Closing a window equals Quit; a timeout proves exit. | Repeat start/Stop, parent-exits-first, broker crash, explicit Quit and failed startup in a disposable room; compare owned process/handle/file inventories. |

The OS adapter must document the actual containment mechanism and its limits.
If process groups cannot meet a required descendant guarantee, investigate the
smallest available OS mechanism or explicitly block that provider/platform path.
Do not hide the limitation with a looser check or convert uncertainty to exit.

### Neutral implementation comparison

Python and Rust have equal standing. Reuse existing code only where its behavior
matches the contract; existing prototype size, popularity and earlier votes earn
no credit. Neither language receives a presumed reliability or speed advantage.

Compare concrete sketches for the same accepted/attempted/confirmed/unresolved
states, bidirectional transport, durable acknowledgement and OS ownership rules.
Do not build two complete production brokers. Use a bounded feasibility probe
only to answer an otherwise unresolved native question, with the same pass
conditions for either candidate.

Choose in this order, recording evidence and remaining uncertainty:

1. **Correctness feasibility:** can the candidate implement the same identity,
   containment, cancellation, retry and recovery contract on each target OS?
   A failing implementation is not by itself a language limitation.
2. **Maintenance cost:** compare platform-specific/native glue, unsafe or FFI
   surface, dependency upkeep, debugging visibility, and the code paths changed
   by a provider protocol revision. Line count is descriptive, not a score.
3. **Delivery cost:** estimate the smallest integrated provider path, migration
   and removal of its predecessor. An extractor's existing behavior is useful
   evidence; an unintegrated prototype is not completed delivery.
4. **Packaging:** enumerate artifacts, native libraries, interpreter requirements
   and signing work per OS. Count native signing objects, not every data file.
   Retained Python coordination/audio means a Rust broker does not remove Python.
5. **Measured behavior:** apply the same workload and thresholds below. Reject
   unexplained regressions; do not trade correctness for a smaller binary.

If both meet the contract, select the lower evidenced maintenance/delivery cost.
If the comparison is inconclusive, record that result and resolve the named
uncertainty; do not silently fall back to an expected Rust winner. Missing macOS
evidence remains a qualification, whichever language is chosen.

### Matched workload and proposed acceptance thresholds

QA records a baseline before implementation and freezes final thresholds before
seeing candidate results. This table proposes the initial bars; QA's baseline
record ratifies them or names an exception before implementation. Use the exact
installed package copied to a disposable room, never the live room. Use the same
machine, OS, room snapshot, provider/stand-in,
dependency versions, power mode and workload. Record cold startup separately from
warmed operation. Alternate baseline/candidate order; at least three runs each.
Record the five largest background CPU consumers so unrelated load is visible.
Use runtime_events.jsonl and stand-in request logs with aligned monotonic timing,
plus one-second CPU/memory samples using existing tools. Report per-run counts,
median, p95 and maximum; do not pool unlike platforms or
compare the old 505 MB fixture with the newer 663 MB fixture as one baseline.

The initial capacity envelope is 1, 4 and 8 concurrent stand-in agents. At 8,
repeat a 30-minute workload and a separate 2-hour stability workload after a
10-minute warmup. Each worker
emits a fixed 1 KiB frame ten times per second; vary scheduling and cancellation
with a fixed recorded sequence. That is a bounded 80 KiB/s aggregate at 8 agents.
Use the contract's actual retention/rotation limits; never accumulate an unlimited
2-hour transcript. Script a 200 ms first output and fixed reply size for the
latency fixture, identical before and after. Real-provider measurements are
separate because external model
latency, tool execution and rate limits are not broker performance.

| Metric | Proposed target | Explicitly not claimed | Check |
|---|---|---|---|
| Concurrent agents | 8 stand-ins for 30 minutes with zero duplicate launches, lost acknowledged intents or falsely completed/unresolved runs. At capacity, reject or queue visibly within declared limits. | Unlimited agents or eight arbitrary models fit this hardware. | Count intent/run/process identities and terminal records at 1/4/8 concurrency, with cancellation and controller disconnects. |
| Submit latency | p95 <= 500 ms from local API receipt to durable acceptance or explicit bounded-queue/rejection result. | Queue wait and provider execution complete in 500 ms. | At least 100 requests per run, report accepted, queued and rejected distributions separately. |
| First output | With a slot available, stand-in submit-to-first-output p95 <= min(matched baseline + 100 ms, 500 ms), measured from API receipt to first output visible at coordination. | Remote model first-token latency or a saturated queue is bounded by 500 ms. | Use fixed 200 ms stand-in response delay; report queue wait, spawn and stream overhead separately; do not subtract provider delay from one side only. |
| Cancellation intent | p95 <= 500 ms from local Stop receipt to durable cancellation acknowledgement while broker is reachable. | Acknowledged cancellation means exited; an unreachable broker has met the target. | At least 100 cancellations per run; report unreachable control separately as a failure of availability. |
| Confirmed cancellation | Every supported owned fixture tree exits within 15 s of Stop. Existing provider grace policy is not shortened to meet this target. | Timeout alone proves exit or authorizes a replacement. | Include graceful exit, ignored graceful stop, grandchildren and escape cases; report unresolved as failure of cleanup, not successful exit. |
| Reconnect control and replay | <= 2 s from authenticated endpoint availability to restored control of the same run; zero lost sequence numbers within retention after a 60-second disconnect; explicit overflow outside retention. | Coordinator startup time, arbitrary replay completion latency, output beyond retention, or lost broker stdio is covered. | Verify cursors, byte completeness, pending-input status and Stop. Record endpoint-restoration and replay catch-up times separately. Test overflow under (b). |
| Sustained memory | After 10-minute warmup, no more than 10% growth across the following 2 hours for each measured tree; compare medians of the first and last 10-minute windows. | These values are measured savings, a total-memory cap, or a universal OS-independent RSS equivalence. | Sample once per second with one OS-native metric consistently. Report room, broker, workers and whole app separately, plus absolute baseline/candidate footprint and stream disk usage. |
| Event-loop responsiveness | p95 room event-loop stall <= 100 ms at the prescribed workload, with maximum also recorded. | A p95 passing result excludes individual long stalls. | Use the same existing timing instrumentation before and after; report absent instrumentation as a measurement gap rather than inventing a pass. |
| Long-session stability | Two measured hours with zero lost acknowledged records, duplicate launches, duplicate input delivery or silent stream gaps; every crash check ends in its allowed state. No monotonic owned process/handle/descriptor growth across completed cycles. | Two hours proves indefinite reliability, arbitrary provider deduplication or power-loss safety. | At least 100 run cycles, periodic Stop and controller reconnect; compare post-cleanup inventory to baseline and verify retention caps. Unknown input outcomes remain unknown and are not blindly resent. |

All thresholds above are proposed engineering acceptance values using QA's
input, not inferred from the old single-run figures. If the matched baseline
already misses a numeric target, record the miss and agree an explicit exception
or revised scope before implementation; do not retune after seeing the candidate.
CPU time and event-loop stalls are also reported for the matched workload. Safety
failures are never averaged away by p95 or accepted because performance improved.

### Sources and current evidence

- [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects): descendant association, breakaway and last-handle kill-on-close semantics.
- [POSIX setsid](https://man7.org/linux/man-pages/man3/setsid.3p.html): a process can create a new session/process group under the specified conditions; group membership is not universal descendant containment.
- [Microsoft WSL](https://learn.microsoft.com/en-us/windows/wsl/about): WSL2 uses a Linux kernel in a lightweight VM. This supports the limited process-feasibility claim, not desktop parity.
- QA #17840 reports WSL2/Ubuntu 24.04 available and macOS hardware unavailable. This is QA's observation, not a new systems inspection.
- Systems native-proof/README.md describes an isolated Windows fixture only; it excludes real-provider, persisted-recovery and macOS evidence. No Linux/macOS result is asserted here.
