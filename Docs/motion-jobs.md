# Shared heavy media admission

Design r2 for task 19055e0e. Proposal only, no implementation or installed claim.
Base: installed 0.12.47 source `27bf6ed140ed148ea5ccee3085c5154b98d38b68`.
Systems retains audio internals and `motion_studio.py`; Media owns this contract
and `motion_jobs.py`. Backend wires dispatch; Desktop wires recording.

## One admission authority

One host-owned `MediaJobs` object, shared by project runtimes and browser instances
on the room server's event loop. Capacity is one, with `MAX_WAITING = 32` and
`MAX_OWNER_WAITING = 4` across projects for one submitting agent. Refusal includes
the applicable limit and that owner's existing queue positions. Owner limits
bound queue monopolization; admitted jobs are not preempted for later arrivals.
No daemon, renderer, worker pool or independent execution scheduler is added.
Existing Exporter and recorder tasks run their own work and await admission.
FIFO order is submission order; a full queue refuses before allocating scratch
or spawning anything. New arrivals never bypass an older waiting ticket.

Each ticket references an existing job record: id, generation, project, kind,
owner, phase and creation time. Queue metadata adds position and waiting reason.
MediaJobs allocates 12-lowercase-hex ids for frame, inspect, capability and TTS.
Exporter and Desktop may retain their existing ids, registering them before
submission; live/retained id collisions are refused or regenerated. One registry
serves status/cancel for every kind. Generation belongs to each registered job.
The gate does not store scenes, commands, credentials or file paths.
It grants one explicit lease to the top-level job. That job holds it through
probes, TTS, audio decoding/mixing, frame capture, encode and owned teardown.
Nested stages receive the same lease/check_cancel; they never enqueue again.
VoiceCache's pin leases and synthesis lock remain cache machinery, not heavy-job
admission. An internal duplicate acquisition is an error, not an implicit wait.

Proposed gate seam: submit(job), wait(ticket, check_cancel), release(lease),
cancel(id, generation, project, actor), snapshot(project), close(). Admission transitions and
queue removal are atomic between awaits. A released or stale lease cannot
release a successor's slot. No lease is released while owned work is alive.

## Export and other callers

Exporter stays the export lifecycle authority. It keeps one active `current`
and a bounded table of waiting job records/tasks. Submission creates a stable
id/generation and returns phase `queued` immediately. The existing export runner
waits for its ticket before creating scratch or probing codecs. Queuing export B
must not invalidate export A: cancellation compares each registered job's own
id/generation and project, not the newest submission counter or `current` alone.
Scene bytes/options/composition, project identity and project directory are pinned
at submission and rechecked
after admission and every existing publication fence. Final files remain published
only after the requested frames and encode succeed.

Preview `frame`, inspect and any standalone TTS prepare use the same gate.
Remove `INSPECT_SLOTS` as an independent heavy-operation semaphore. Authored
audio inside export is already under the export lease and does not submit TTS
again. Read/save/validate/import and cached capability reads need no heavy lease.
Every inspect takes a lease: cached analysis can still lead to FFmpeg sheet work.

Frame and inspect become owned jobs before waiting. Set
`SYNC_ADMISSION_WAIT_SECONDS = 1` and `SYNC_REPLY_WAIT_SECONDS = 10` (total time
from submission). If still waiting after the admission budget, return
`{job_id, generation, phase: 'queued', position, waiting_reason}`. If admitted,
return the existing result shape plus job_id/generation when it finishes inside
the reply budget; otherwise return a starting/running handle. Waiting for these
budgets never cancels the owned job. `job_status` later returns its phase and
terminal result. Current MCP waits (frame 60 s, inspect 600 s, others 15 s) stay
unchanged. Backend owns these wrappers; Systems owns frame lifecycle integration.

Retain terminal frame/inspect/TTS/capability results for
`RESULT_TTL_SECONDS = 1800`, at most `MAX_TERMINAL_JOBS = 64` per host, evicting
oldest terminal records first. `MAX_RESULT_BYTES = 262144` bounds serialized JSON
per result, validated before publication; oversize results fail explicitly.
Results contain bounded tool answers/URLs, not PNG/video bytes, process handles
or scene inputs. Project-filtered status returns result/error for done/failed/
cancelled, and an explicit unavailable/expired answer after eviction. Active
and queued records are never evicted. TTL cleanup is lazy on registry operations,
with no maintenance daemon. Eviction removes in-memory answers only; existing
export metadata and published room artifacts retain their own lifecycle.

Cold `require_tools` / `encoder_check` / `probe_profiles` and recording
`probe_transport` count as work. Export probes run under its lease. Recording
probes run under its preparation grant. The scenes GET uses a deduplicated
capability ticket on a cold miss, rather than spawning probes ahead of queued
jobs; cached reads remain immediate. The HTTP reply uses the same bounded wait,
reporting capability preparation pending plus a job handle when necessary,
without falsely reporting an encoder unavailable. Cancellation joins the probe thread and
its bounded subprocesses before releasing. Probe launch priority and ownership
must use Desktop's accepted below-normal process authority.

## Recording admission

Record start joins the same FIFO as a preparation/admission ticket, returning
its stable recording id with phase `queued`. Desktop separates the pending
descriptor from allocation/start: no spool writer, capture stream or deadline
timer until admission. Cancel/close/project change while waiting removes it.
Allow only one outstanding take (queued, capturing or finalizing) per host,
in addition to existing per-browser checks, to bound retained encode spools.

After its preparation probes, the grant becomes an exclusive capture
reservation, not a heavy encode lease. It occupies the same capacity until the
take ends, so no export, inspect, TTS or cold probe can start during live capture.
Interactive browser operation continues. Capture never requests another slot
mid-take; its existing time, byte and queue limits still apply. This deliberately
serializes live capture with heavy jobs rather than predicting available CPU.

Record preparation reserves one future encode queue entry within the host and
owner limits. Other arrivals cannot consume that entitlement during capture;
stop cannot fail because the queue filled. The reservation is not a second
execution slot or a priority bypass.
On stop: detach capture, drain/join its spool writer, then atomically enqueue an
encode ticket at the FIFO tail and release the capture reservation. Older jobs
run first. The take reports phase `queued`, stage `encode`, with its captured
duration retained; no encoder starts before admission. Only then start the
existing finish encoder. Its encode timeout starts on admission, not in the
queue. Spool cleanup, encoder join and publication precede lease release.
Failed preparation/start, page/instance closure and shutdown release whichever
reservation or ticket they actually own, exactly once. Existing record_stop is
idempotent; for a queued start it cancels that pending start instead of capturing.

## Visibility, cancellation and shutdown

Backend adds a project-filtered `media_jobs` list to export_status, including
queued and active frame/inspect/TTS jobs, without exposing another project's
paths or results. Existing export ids remain valid for export_status/cancel;
Backend should provide job_status/job_cancel for other Motion kinds using the
same registry and existing permission checks. Recorder status/stop use its
existing recording id and Desktop's lifecycle cancellation. No second id system.
All cancellation surfaces, including existing export_cancel and recording stop/
cancel, require the same project plus the submitting agent's authoritative
identity or an authenticated user action. Other agents cannot cancel those jobs.
Actor identity comes from the runtime/tool caller, never a supplied argument.

Queued cancellation removes the exact ticket and immediately updates positions.
Active cancellation sets the job's existing cancellation flag and wakes waiters;
the owner stops/joins its owned workers and scratch cleanup before releasing.
`asyncio.to_thread` cancellation alone does not stop inspect or codec subprocesses:
the wrapper must join that work, suppress publication after cancellation, and
retain the slot until it ends. QA agreement 06140973 pins the inspect stop hook:
keyword-only `stop=None` (a threading.Event) on analyze/contact_sheet/dense_sheet/
frames/motion_chart/shot_sheet, propagated to every spawning helper. The worker
thread owns Popen and its pipes; no register(proc) callback or external process
handle is shared. It checks stop before spawn and polls communicate with
`POLL_S = 0.2`, passing input only on the first attempt. Stop or the unchanged
180 s deadline kills and reaps the process before raising video_analysis.Cancelled
or TimeoutExpired respectively; WORKER_PRIORITY stays below-normal on Windows.
Cancellation cannot populate an incomplete analysis cache. Backend signals stop,
joins the thread despite repeated cancellation and fences publication. Track
newly created derived files for cancelled-job cleanup; never unlink a reused
content-hash artifact belonging to earlier results. QA authors video_analysis donor r4;
Backend owns threading.Event propagation and result/publication fences.
Repeated cancellation
must not detach cleanup. Preserve Systems' approved `file_io` join successor.

Project change invalidates that project's queued work and cancels its active job.
Host shutdown rejects admission, cancels queued jobs, asks active owners to stop,
and joins all owned tasks before closing the gate. The queue is in-memory; no
automatic replay or resume after restart is introduced.

## Review and smallest acceptance

Before code: Systems reviews audio/Exporter generation and teardown boundaries;
Backend reviews job visibility, cold probes and inspect cancellation; Desktop
reviews queued recording start, capture reservation, stop and encode handoff.
These callers ship together; a lease module alone does not mitigate load.
Systems' r1 approval 96abe3d2 assigns its bounded motion_studio.py delta:
queued Exporter tasks, all HTTP/frame admissions, per-job cancellation, scratch
allocation only after grant, frame sheet publication under ownership, and cold
probes including the synchronous start fallback. No audio cache rewrite.

QA's installed proof on 4060e163 submits two exports and a recording concurrently,
observes FIFO phases (including the queued take), no overlap between jobs, no
mid-take capacity wait, and no deadlock. Cancel one queued job and one active job;
verify subsequent work starts only after teardown, recording frames are retained,
and no owned process or scratch survives. Record priority and compare response
latency against QA's 12:05 baseline. Capacity one can lengthen completion time;
responsiveness improvement remains unclaimed until that measurement.
Also call frame while an export owns the slot: it returns a queued handle within
the admission budget; job_status later returns its frames. Confirm an agent
cannot cancel a different agent's job and inspect cancellation stops/joins its
worker before the next job is admitted.
