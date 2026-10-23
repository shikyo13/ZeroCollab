# Attached review: validation and fix plan

Date: 2026-10-23 (America/Chicago). Scope: source validation and proposed fixes only.

## Conclusion

**Updated owner direction, 2026-10-23:** repository structure, accumulated product scope and motion extraction are primary work in the plan. Motion should be a standalone toolset that can operate without the ZeroCollab application; a CLI with an optional MCP adapter is the recommended starting shape. This is an architectural/product decision, not a claim that a performance regression has been measured.

The initial assessment treated extraction too narrowly as an optional remedy requiring runtime contention evidence. That framing is superseded. Keep the factual corrections to individual security/reliability claims, while prioritizing repository organization and the standalone motion boundary. Crash-lifetime, legacy-launcher and persistence investigations remain separate follow-ups, not prerequisites for this architecture work.

This is a static assessment of the exact reviewed commit, `68bde70316edb256dae039e34e8469b490704f3f` (desktop 0.1.6, 2026-10-16). Local HEAD matched that revision and the checkout was clean before this report. This does not establish the revision or behavior of any installed/running copy.

No new security exploit is confirmed by the supplied claims under established lower-trust preconditions. That is a statement about these claims and this evidence, not a security clearance of the application.

## Question, evidence and method

**Raw question:** validate the attached Google review and plan fixes.

**Reported observation:** a pasted review combines archive inspection snippets with security, reliability and architectural conclusions. The supplied material includes no exploit reproduction, latency trace, crash-process inventory or frontend flicker capture.

**Refined question:** for each reported concern, can an input reach the claimed consequence through the actual controls at this commit, and what is the smallest useful next change or check?

Three explanations were considered: (1) a real missing control; (2) an existing control or intentional capability missed by partial inspection; (3) a plausible failure mechanism whose runtime impact has not been measured. The decisive check is the source-to-consequence path, including authorization and recovery, rather than the presence of a risky API or a large file.

The research hub was synchronized first. Its index, relevant tooling/kernel topics and experiment/finding/open-question indexes were searched. No prior validation of this particular review was found in that search. No benchmark or provider run was repeated.

Evidence labels below distinguish **observed source**, **derived mechanism**, **reported impact** and **not tested**. Existing tests were inspected as contract evidence only; none were executed. No application, provider, exploit, build, deployment or live-state mutation was run.

No root or applicable scoped SECURITY.md was returned by the security-policy resolver. Security judgments therefore use the documented local product capabilities and explicit code contracts. A missing policy is a limitation, not evidence that a claim is valid or invalid.

## Claim ledger

| ID | Review claim / remedy | Verdict and decisive evidence | Disposition |
|---|---|---|---|
| R1 | File preview/open-path needs a resolved project-root check to stop symlink escape. | **Preview claim contradicted.** [file_preview.resolve](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/file_preview.py#L117-L175) rejects parent traversal and finishes with a resolved containment/private-path check, including basename lookup. Absolute references to files tracked in another local repository are an explicit capability. [open-path](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/app.py#L1595-L1648) is a different authenticated local file-manager operation, not a project-confined read API. | Preserve preview checks. Do not impose a blanket workspace restriction on local file-manager or UNC references. This does not assess filesystem replacement races or every file API. |
| R2 | Windows batch launch permits command injection. | **Conditional risk; exploit unestablished.** [Legacy launch](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/wrapper.py#L507-L512) accepts operator configuration/argv, resolves PATH, then [Popen](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/wrapper_windows.py#L410-L427) can invoke a batch shim. The general [managed resolver](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/native_adapters.py#L54-L81) already avoids .cmd/.bat and provides native/node paths; Antigravity has a separate resolver branch. No lower-trust text-to-legacy-argv path was established. | Trace one supported legacy case. Prefer native/node launch over custom caret escaping. Do not label ordinary operator-controlled command execution a demonstrated remote injection. |
| R3 | Default network binding and origins allow an unauthenticated WebSocket/DNS-rebinding attack. | **Material claim contradicted.** [run.py](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/run.py#L196-L214) creates a random UI credential; [binding](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/run.py#L273-L297) defaults to loopback and requires an explicit network opt-in. [WebSocket auth](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/app.py#L879-L895) requires a valid token OR exact room host, origin and valid cookie. [HTTP](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/app.py#L258-L305) also checks credentials/origin. | No proposed default-binding fix is needed. The credential-based WebSocket branch is not a universal Origin check; explicit plaintext LAN operation remains a documented separate deployment risk. |
| R4 | A Team Architect can inject malicious instructions through drafted backgrounds; strip role delimiters and add a schema. | **Trust concern real; bypass/exfiltration unproven.** Drafted text becomes instructions after installation, but a [member schema and stage/revision checks](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/team_builder.py#L245-L270) already exist. [Approval and Add](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/team_builder.py#L543-L575) are separate user actions. The [agent tool](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/room_runtime.py#L2298-L2311) permits only get/submit/suggest, and [run tokens](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/app.py#L183-L190) cannot reach approval routes. | Treat agent drafts as untrusted proposed configuration. Delimiter removal cannot establish semantic safety. Any hardening should protect exact-content approval and actual tool/credential authority, rather than claiming text sanitization solves prompt injection. |
| R5 | Hashing/git/JSON/disk work blocks the event loop for 100–300 ms and causes disconnects. | **Partly supported mechanism; impact unmeasured.** [Task dispatch](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/room_runtime.py#L2383-L2397) enters synchronous mutations; [ProjectRoom.save](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/project_room.py#L438-L447) and [SQLite commit](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/roomdb.py#L375-L386) block until done. However, [git diffs](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/room_runtime.py#L1908-L1927), [file previews](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/app.py#L1667-L1695) and [network-path classification](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/app.py#L1607-L1614) are already offloaded. Fingerprints hash assembled context strings, not proof of a directory-hashing hotspot. | Measure the remaining synchronous span. Neither the quoted timings nor disconnect causation were validated. Do not wrap mutable ProjectRoom operations indiscriminately in to_thread. |
| R6 | roomdb uses WAL and SQLite's default timeout is zero; configure 5,000 ms. | **False at this revision.** [roomdb.connect](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/roomdb.py#L153-L172) explicitly sets timeout from BUSY_TIMEOUT_MS=5000 and PRAGMA busy_timeout; it selects journal_mode=DELETE and synchronous=EXTRA. | No change. Do not switch journal modes as a supposed missing-timeout repair. |
| R7 | Abrupt server death can leave child agents/compilers running. | **Source-level lifetime gap; runtime survival not tested.** [Spawn](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/native_adapters.py#L84-L100) uses pipes and process groups/session flags; [normal cleanup](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/native_adapters.py#L225-L252) and [supervisor stop](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/supervisor.py#L336-L356) explicitly kill owned trees. No OS kill-on-owner-death binding was found in those paths. Direct CLI exit on stdin EOF is a serious alternative explanation. | First follow-up candidate. Reproduce only with disposable owned processes; then add the narrow lifetime control if descendants survive. |
| R8 | A stopped/crashed reviewer leaves tasks permanently stuck; timeout and reassign to astra. | **Overstated.** [Continuity](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/work_continuity.py#L493-L519) already raises deduplicated stalls for blocked/login-required agents with pending reviews. [Cancellation](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/coordination_records.py#L514-L528) lets the owner/requester/user cancel and request a different reviewer. [Stopped/archived agents stay quiet](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/work_continuity.py#L493-L505) intentionally, and [wake gates](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/ready_wakes.py#L87-L98) preserve that choice. A pending review does hold owner continuity, so it can wait indefinitely without intervention. | Optional explicit hold/reassign UI if users need it. Do not undo Stop or hardcode astra. Runtime crash-to-status behavior was not tested. |
| R9 | Votes are regex-parsed chat and need a structured cast_decision_vote tool. | **False for decision_flow.** [Submit](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/decision_flow.py#L586-L598) checks stage/revision/epoch and owed participant; [verdict payload](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/decision_flow.py#L652-L664) requires current proposal_id and agree/dissent. Contributions are structured through room_decision. | Preserve the existing tool and stale/duplicate protections. Do not invent a replacement voting API. |
| R10 | Integrated Motion Studio should become a standalone toolset. | **Accepted architectural direction.** The owner explicitly prioritizes scope and maintenance boundaries. Current admission controls do not answer that concern: [room startup](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/room_runtime.py#L3570-L3605) installs media jobs and motion routes directly, and Motion's exporter holds RoomRuntime. Runtime contention remains unmeasured and is not a prerequisite for extraction. | Make an independent motion engine/CLI the primary extraction target; optional MCP and a thin artifact handoff. No always-on microservice is required. |
| R11 | Board REST responses race with WebSocket deltas; add seq_id. | **Proposed missing mechanism already exists.** [projectStateStore](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/frontend/src/lib/projectStateStore.ts#L5-L112) has rev, stream retirement, generation/epoch fencing and gap recovery; [applyProjectDelta](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/frontend/src/lib/projectDelta.ts#L25-L32) ignores old revisions and refuses mismatched bases. [Follow](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/frontend/src/follow/followStore.ts#L631-L637) subscribes to that canonical store. | No second sequence scheme. A specific flicker report needs its own event trace; these guards do not prove every rendering behavior is correct. |
| R12 | Migrate the hybrid frontend and retire its legacy static implementation. | **Included in repository cleanup, with staged parity.** [Vite](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/frontend/vite.config.ts#L1-L14) already emits into static/modern. [React startup](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/frontend/src/main.tsx#L1-L15) calls prepareLegacy; [legacy.ts](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/frontend/src/legacy.ts#L1-L44) moves existing chat/control DOM. That dependency establishes migration work, rather than a reason to retain two layers indefinitely. | Put the app frontend under one clear source/build owner; replace remaining legacy features in bounded slices before deleting their actual dependencies. |
| R13 | Move loose Python modules into a coherent package layout. | **Accepted primary cleanup direction.** Root-level coordination, host, provider and media modules mix product responsibilities. Launchers, imports, packaging and update manifests make the move a compatibility change requiring a migration plan. Namespace shape alone is not a runtime defect, but maintainability is an independent objective. | Organize by responsibility, establish one authoritative source per component, and move in bounded slices after defining the app/tool boundary. |
| R14 | Consolidate desktop packaging and avoid duplicated/staged source ownership. | **Included in structural cleanup, with factual correction.** The six tracked desktop host/controller modules differ from the [ignored staged backend](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/desktop-installer/.gitignore#L1-L6). Some overlap remains a maintenance concern even without a demonstrated installed mismatch. | Give host-specific adapters one clear home, generate payloads from canonical source, and keep build outputs outside source trees. Do not silently replace distinct host behavior while consolidating. |

Additional factual corrections: [the frontend manifest](https://github.com/shikyo13/ZeroCollab/blob/68bde70316edb256dae039e34e8469b490704f3f/frontend/package.json#L1-L9) declares React 19.2, not React 18. wrapper_windows is direct subprocess plus Win32 console injection, not the asserted ConPTY implementation. Strong release-pipeline safety claims were not independently audited by this task.

## Revised architecture and migration plan

This plan supersedes the earlier ordering. This section records the planning-stage recommendation. The owner subsequently authorized implementation and deprecated the browser frontend in favor of Electron. [Implementation evidence](2026-10-23-desktop-motion-extraction.md) records the resulting source layout and checks; no deployment was authorized.

### Product boundary

**ZeroCollab owns:** chat, board, team, projects/repositories, agent lifecycle, approvals, coordination, and generic artifact references/playback.

**The standalone motion tool owns:** scene schemas and validation, scene edits/templates, asset inspection, the TypeScript renderer, headless rendering browsers, frame export, encoding/transcoding, audio/voice processing, media-job admission/cancellation and its own temporary/output files.

**Integration:** agents invoke the motion CLI or optional MCP adapter as an external tool. The room receives completed artifact references using its ordinary file/chat/board features. A later UI shortcut may open the separate tool. The core app does not need an embedded Motion Studio, renderer queue or video-production service to start, run or update.

This concerns the media-production pipeline. Ordinary UI animation and accessibility behavior in the app remain app behavior.

### Recommended packaging

Start with **one independently runnable motion package, a CLI, and an optional stdio MCP adapter** over the same engine. The adapter is not a second renderer or queue authority. No new mandatory daemon, network service, extension framework or cloud account is implied.

Proposed target layout (names are a recommendation, not an implemented filesystem state):

```text
apps/
  desktop/                 shell, host adapters, installer source
    renderer/              desktop UI and its remaining static chat modules
src/
  zerocollab/
    api/
    coordination/
    runtime/
    storage/
    providers/
    integrations/          thin external-tool/artifact adapters, when needed
tools/
  motion/
    src/zerocollab_motion/ engine, CLI, optional MCP adapter
    renderer/              TypeScript renderer and optional standalone preview UI
    assets/                templates, fonts, starter media and licenses
tests/                     tests beside their owning component during the move
Docs/                      app and tool documentation with explicit ownership
```

Generated bundles, installer payloads, candidate copies, captures and scratch files belong in designated ignored build/work/output locations. Runtime data and existing worktrees remain outside source and are not deleted as cleanup. One canonical source builds each shipped component.

### 1. Define the extraction seam from the current coupling

The source shows several dependencies that a directory move alone will not fix:

- `room_runtime.install` creates `host.media_jobs` and installs `motion_studio`; `room_motion` dispatches directly into the in-process implementation.
- `motion_studio.Exporter` holds RoomRuntime, obtains its media gate, reads project state and publishes room Live events. Replace those dependencies with explicit tool inputs/outputs.
- `frontend/src/lib/motionCore.ts` reaches directly into `../../../motion/core/index`. Move production-renderer consumers with the tool; keep or extract only any genuinely shared lightweight UI primitives.
- `desktop-installer/electron-builder.json` bundles an encoder, and `backend_host.py` supplies its path. The motion tool should own its own encoder/browser/voice discovery and installation.
- Browser recording is a material boundary: `browser_recording_coordinator.py` imports `motion_studio.require_tools/project_dir`, and `browser_recording.py` invokes the encoder. Keep live browser control/capture ownership with the browser tool, and route recording encoding/postprocessing through the standalone media engine. Do not retain a hidden rendering/encoding pipeline in the app after removing Motion Studio.
- Host handover and shared-browser code refer to `host.media_jobs`. Separate the app's generic operation lifecycle from motion-specific queue authority; a rename alone would leave the dependency intact.

The input contract should carry an explicit workspace, scene/assets or resolved read-only asset references, options, caller/job identity, and output directory. Results should carry actual artifact paths, media metadata, job identity/status and errors. Do not pass RoomRuntime, room UI tokens, browser-control handles or mutable board state into the engine. Existing scene JSON and artifacts remain readable.

### 2. Make motion work independently, before switching the app

Move the engine, rendering/audio helpers, assets and existing relevant tests under the tool's authoritative source. Replace room-aware lookups with explicit adapters at the edge. Provide a minimal CLI for validate, preview/render and export/inspect; expose the same operations through MCP only where useful.

Prove the boundary with a small existing fixture: with the room stopped and without importing its app/runtime modules, validate a scene, render a frame, export a short video, inspect the output and cancel an owned job. Use existing scene/encoder checks; add only a focused case for the new process/interface boundary. Missing optional tool dependencies should produce a tool error, not prevent ZeroCollab from starting.

### 3. Switch callers and remove the embedded pipeline

Agents use the standalone tool directly. If a temporary `room_motion` compatibility adapter is necessary, it only forwards to the external tool and converts returned artifacts into existing room attachments; it owns no renderer, synthesis worker or media queue and has an explicit retirement endpoint in the migration plan.

Move the Motion page/preview tooling to the standalone package or remove its built-in navigation once the replacement is usable. Preserve generic video playback and publishing. Adapt browser recording encoding and its completion/cancel ownership through the same external media boundary before removing the app's encoder dependency.

Then remove motion startup hooks, internal routes/tool definitions that are no longer needed, heavy dependencies and packaged media-production resources from the core app. Avoid an indefinite second implementation.

**Acceptance:** ZeroCollab launches and supports its normal coordination flows with the motion package absent. Motion operates with ZeroCollab absent/stopped. A produced artifact can be referenced and played in chat/board. A motion crash/cancel does not stop the room, leak owned workers, or mutate a switched project's records. The room does not probe or launch a motion renderer/encoder during ordinary startup.

### 4. Consolidate the repository around the resulting ownership

Move the remaining Python modules into the package by domain, and collect desktop/web sources under their app owners. Update entrypoints, imports, tests, build inputs and packaging manifests in the same bounded slice as each move. Keep host-specific behavior explicit rather than copying entire source trees.

Migrate the hybrid frontend feature by feature into one maintained UI source tree. Its current dependency on static modules is a reason for a staged migration, not an argument against cleanup. Delete each obsolete implementation only after its replacement handles the affected chat, drafts, selection, streaming and approval behavior.

Use existing startup/build checks and the smallest affected tests per slice. Keep the migration distinct from a broader rewrite, feature additions or data cleanup. The historical structure-plan summary was consulted, but its source was not located in the bounded paths checked here; this proposal does not silently revive its unverified phase names or authorization assumptions.

### Separate follow-ups retained from the review

The crash-lifetime fixture, legacy Windows argument-provenance check and remaining persistence measurements remain valid bounded follow-ups. They are not gates for accepting the standalone-motion/product-scope direction. Profile approval hardening and a clearer stopped-reviewer UI also remain separate; preserve explicit user approval and Stop throughout.

The existing corrections to preview containment, authentication, SQLite timeout, structured votes and revision fencing still stand. Accepting the structural critique does not require treating those different factual claims as confirmed.

## Evidence and limits

- Primary evidence: source files at the immutable commit linked above. The raw attached review remains at its original PC attachment path; it was read as evidence, never executed.
- Security intake: five claims in input order (path boundary, batch launch, unauthenticated WebSocket, team background, abrupt child lifetime). Static verdicts are two not_actionable and three needs_review; no confirmed vulnerability and no runtime reproduction.
- Machine-readable triage: [retained JSON](C:/Users/Zero/.codex/state/plugins/codex-security/scans/ZeroCollab/artifacts-a12dcffc11eb4945e588e74b6d1f8e619f264f3846964138006da8ce3c46850a/artifacts/2026-10-23-attached-review-triage.json). It is a standalone triage artifact, not a completed security scan.
- Current Python 3.11 documentation, reached after Context7 library resolution, confirms the [Windows batch-shell caveat](https://docs.python.org/3.11/library/subprocess.html#security-considerations), [C-runtime argument conversion](https://docs.python.org/3.11/library/subprocess.html#converting-an-argument-sequence-to-a-string-on-windows) and the [default five-second sqlite3 timeout](https://docs.python.org/3.11/library/sqlite3.html#sqlite3.connect). Context7's retrieved snippets did not settle the two details, so the relevant official passages were opened directly.
- The Linux manual documents [PR_SET_PDEATHSIG](https://man7.org/linux/man-pages/man2/PR_SET_PDEATHSIG.2const.html) as Linux-specific, including its fork and setup-race limitations. The review's Linux/macOS prescription is not portable as written.
- No performance timings, process-survival outcome, semantic prompt-injection success, installed-app acceptance or broad security coverage is claimed. Proposed checks are not executed results.
