# File and module index

Current source layout: Python modules moved by domain into `src/zerocollab/`; desktop shell/host/UI live under `apps/desktop/`; independent media source/tests/docs live under `tools/motion/`. Basenames below describe those modules. [README ownership map](../README.md#source-layout) and [motion README](../tools/motion/README.md) are the current setup/entry-point references. Browser-product launch instructions in older design notes are retired.

Every source file, with its line count and the purpose its own header states. The Python and frontend tables are generated from the module docstrings and file headers, so a file without a header shows "-". Regenerate them after adding or renaming files (see "Keeping these docs current" in [INDEX.md](INDEX.md)).

## Repository root

| Path | What it is |
|-|-|
| `run.py` | Server entry point (web UI, REST, WebSocket and both MCP servers in one process) |
| `supervisor.py` | Owns the server process; executes approved deployments; not part of any release |
| `Start-Room.ps1` / `Start-Room.cmd`, `Stop-Room.ps1` / `Stop-Room.cmd` | Start and stop the managed workspace through the supervisor |
| `Login-Gemini.cmd` | Opens the official Gemini sign-in (`login_gemini.py`) |
| `config.toml` | Agents, ports, routing, runtime limits |
| `config.local.toml.example` | Template for local extra agents (only `[agents]` is merged) |
| `requirements.txt` | Python dependencies |
| `VERSION` | Release version string |
| `COLLABORATION.md` | Collaboration rules appended to every managed agent's instructions |
| `README.md`, `ROOM-QUICKSTART.md` | User documentation |
| `windows/`, `macos-linux/` | Launchers: `_launch.bat` / `_launch.sh` shared steps plus one `start_*` shim per agent |
| `apps/desktop/renderer/` | React workspace source (Vite + TypeScript), built into `apps/desktop/renderer/static/modern/` |
| `apps/desktop/renderer/static/` | Chat client served as-is, plus the built workspace bundle |
| `tests/` | Python tests (`tests/browser/` holds browser-driven checks) |
| `Docs/` | This documentation library |

## Data directory (`server.data_dir`, default `./data`)

| Path | Written by | Holds |
|-|-|-|
| `zerocollab_log.jsonl` | `store.py` | The chat message log |
| `settings.json` | `app.py` | Room settings: channels, title, subtitle, user name, font, agent-hop limit |
| `registry.json`, `renames.json` | `registry.py` | Live agent identities and rename history |
| `<agent>_queue.jsonl` | `agents.py`, `queue_file.py` | Trigger queues read by launcher-driven wrappers |
| `workspace.json` | `room_runtime.py` | Selected project, paused state, per-agent worktree summary |
| `runtime_events.jsonl` | `room_runtime.py` | Managed-runtime audit events |
| `room_limits.json` | `project_room.py` | Execution limits and budgets |
| `provider_settings.json` | `provider_controls.py` | Model and effort choices per agent |
| `projects/<id>/project.json` | `project_registry.py` | Project name, chat sessions, primary path, `repositories`, `repository_history`, `initial_repository_key`, pending `repo_request` |
| `projects/<id>/coordination.json` | `project_room.py` | Board: spec, tasks, notes, reviews, follow-ups, provider sessions |
| `projects/<id>/agents.json` | `agent_profiles.py` | The project's agent profiles |
| `projects/<id>/goal.json` | `room_goals.py` | The shared goal |
| `worktrees/<id>/<repo-key>/<agent>` | `room_runtime.py` | Each agent's Git worktree per attached repository (older projects may keep `worktrees/<id>/<agent>`) |
| `conversations/<id>/<agent>`, `chat/<agent>` | `room_runtime.py` | Scratch folders for projects without a repository |
| `deploy/` | `supervisor.py`, `room_deploy.py` | Release spool: releases, requests, receipts, journal, backups, `current.json` |

## Python modules

<!-- generated:python-files -->
| Module | Lines | Purpose (module docstring) |
|-|-|-|
| `agent_profiles.py` | 252 | Project-local expert identities, independent of provider subscriptions. |
| `agent_skills.py` | 227 | Role cores and on-demand skills (Docs/agent-profiles.md r3, task b8bc48b6). |
| `agents.py` | 70 | Agent trigger - writes to queue files picked up by visible worker terminals. |
| `app.py` | 1724 | ZeroCollab - FastAPI web UI + agent auto-trigger. |
| `archive.py` | 331 | Export/import archive for project history (chat messages). |
| `browser_recording.py` | 359 | Owned browser takes: timestamped JPEG spool, fixed WebM and content-free input. |
| `browser_recording_coordinator.py` | 160 | One recorder owner across host admission, capture, encode and joined teardown. |
| `browser_recording_transport.py` | 91 | Fixed same-authority JPEG/timestamp transport for browser recordings. |
| `build_release.py` | 16 | Retired ZIP entrypoint; use release_pack.py for managed release packages. |
| `cli_homes.py` | 74 | Paths-only CLI home identity shared by web and desktop launchers. |
| `config_loader.py` | 164 | Shared config loader - merges config.toml + config.local.toml. |
| `coordination_queue.py` | 121 | Coalesce pending wake-ups; resolve record references immediately before delivery. |
| `coordination_records.py` | 626 | Durable peer reviews and follow-ups shared by the board and agent tools. |
| `coordination_state.py` | 73 | Task execution facts. Waiting is derived from records, never from elapsed time. |
| `decision_flow.py` | 1055 | Team decisions: a bounded, attributed discussion that ends in a user-recorded decision. |
| `durable.py` | 24 | Atomic app-side text persistence; callers own transaction ordering and locks. |
| `file_preview.py` | 264 | Read-only views of files referenced in chat messages. |
| `git_preview.py` | 82 | Bounded retained output for read-only Git previews (called off the event loop). |
| `host_handover.py` | 357 | One pinned app-owned host handover; never calls user Stop or Resume. |
| `live_feed.py` | 282 | Live view feed (task b1af7a3a, Zero #14152): who is working with whom, on what, with which artifact. |
| `local_files.py` | 140 | Files referenced in place by their own path (Zero #14949: no copies). A pasted local path, a UNC path or a file:// URI resolves to an existing regular file that the room only reads; nothing is copied into uploads/. |
| `local_protocol.py` | 40 | Classify local model output and validate structured calls before execution. |
| `login_gemini.py` | 12 | Open the official Gemini sign-in UI without inheriting API-key billing settings. |
| `manager_wakes.py` | 778 | Opt-in manager audits. No timer or API is installed by importing this module. |
| `mcp_bridge.py` | 625 | MCP server for agent chat tools - runs alongside the web server. |
| `mcp_proxy.py` | 336 | Per-instance MCP identity proxy. |
| `motion_agent.py` | 727 | room_motion actions on the agent side (tasks 78f77506, 31dcc7da). motion_studio.py holds the renderer and exporter (systems, S3); this module never edits it, only calls its catalogue, validation and save. |
| `motion_audio.py` | 195 | Prepare a shared-core sound plan once and keep narration pinned through export. |
| `motion_audio_cache.py` | 167 | Bounded immutable narration cache. A lease pins a WAV until export finishes. |
| `motion_audio_media.py` | 129 | Bounded audio decode through the Exporter's exact encoder authority. |
| `motion_audio_mix.py` | 215 | Blockwise PCM mixing on the composition clock. |
| `motion_audio_process.py` | 72 | Owned, cancellable audio subprocesses; reuse the desktop Windows job authority. |
| `motion_audio_voice.py` | 157 | Offline voice worker entry point, run in a separate owned Python process. |
| `motion_encode.py` | 136 | Explicit S3 output profiles. Discovery stays in motion_studio.encoder_check(). |
| `motion_jobs.py` | 408 | One host-owned FIFO admission gate, used by existing media lifecycle owners. |
| `motion_render.py` | 425 | Bounded ordered frame capture and content-addressed receipts for Motion S3. |
| `motion_scene.py` | 951 | Motion scene v2: data-only scenes shared by live animation, the Studio and frame-accurate export (task 6ff4919d; Docs/motion-core.md slice S2; field reference in Docs/motion-scene-v2.md). |
| `motion_studio.py` | 1147 | Motion studio: saved synthetic scenes, an in-app preview page and local video export. |
| `motion_studio_jobs.py` | 53 | Caller tasks for MediaJobs. No queue or worker authority lives here. |
| `native_adapters.py` | 892 | Official, locally authenticated CLI protocols. No credential handling. |
| `process_enforcement.py` | 431 | E3, the release train as a room action (Docs/process-enforcement.md, task a7da2f10, Zero #16116). |
| `profile_bell.py` | 184 | Role profile approvals in the user's bell (task b0eaf89e, Zero f14bfb67). |
| `project_registry.py` | 834 | Index of every project this room has worked in: board, team, goal, chats and repository. |
| `project_room.py` | 1866 | Durable project coordination and bounded room context, without model calls. |
| `provider_controls.py` | 200 | Room-local model/effort preferences and official, read-only provider catalogs. |
| `provider_keys.py` | 480 | API keys for OpenAI-compatible servers (Docs/providers.md, slice S5a). |
| `provider_prices.py` | 100 | Built-in API prices for Anthropic and OpenAI key connections (Docs/providers.md, S5b). |
| `provider_relay.py` | 436 | Room-side relay for Claude Code and Codex on the user's Anthropic and OpenAI API keys (Docs/providers.md, slice S5b; security d807412d). |
| `provider_setup.py` | 510 | Provider setup for Team > Providers (Docs/providers.md, slices S1 and S2). |
| `provider_store.py` | 831 | In-app provider connections (Docs/providers.md, slice S3). |
| `queue_file.py` | 75 | Coordinate JSONL queue producers, consumers and explicit clears across processes. |
| `ready_wakes.py` | 251 | Ready-task wakes: wake an idle owner once for authorised, actionable work it already owns. |
| `registry.py` | 824 | Runtime Registry - single source of truth for all live agent state. |
| `release_agent.py` | 149 | Server-side release checks. These functions do not grant authority or queue deploys. |
| `release_completion.py` | 115 | Terminal receipt adapter to the runtime's existing durable release wake queue. |
| `release_control.py` | 164 | Host-wide manual Stop state, independent of any project or rollback snapshot. |
| `release_intake.py` | 323 | E4 host-owned donor checks and handoff dispositions. |
| `release_interface.py` | 85 | Strict public arguments for room_release; authorization stays in the host core. |
| `release_maintenance.py` | 196 | App half of agent deployment drain; reuses the existing maintenance record. |
| `release_manager.py` | 445 | Room release core: bounded reads and receipt-bound installation verification. |
| `release_metadata.py` | 83 | Bound stage metadata to the package fingerprint without changing the host packer. |
| `release_pack.py` | 218 | Package a committed revision as a release the supervisor can deploy. |
| `release_rehearse.py` | 186 | Fixed host rehearsal: exact package, empty room, private homes and loopback. |
| `release_request.py` | 295 | Agent admission policy for the existing deployment queue, not a second engine. |
| `release_stage.py` | 253 | Private, owned release_pack worker; callers never supply paths or commands. |
| `response_claims.py` | 220 | The lead answers untagged human messages (Zero #12950; first built for @all, Zero #12480). |
| `room_deploy.py` | 439 | App side of the supervisor protocol. This module never stops or replaces the server. |
| `room_goals.py` | 164 | Explicit shared goals. Event-driven continuation, never a background model poll. |
| `room_markup.py` | 96 | Bounded raster markup shared by agent tools; no network or chat side effects. |
| `room_mcp.py` | 439 | Run-scoped local coordination tools for official native CLIs. |
| `room_runtime.py` | 3464 | Managed agents for the ZeroCollab persistent workspace. |
| `room_writer.py` | 301 | Windows writer ownership shared by direct rooms and their supervisor. |
| `router.py` | 121 | Message routing based on @mentions with per-channel loop guard. |
| `run.py` | 327 | Entry point - starts MCP server (port 8200) + web UI (port 8300). |
| `shared_browser.py` | 1192 | Disposable project-scoped Chromium instances, independently controlled by each agent and viewable by the user. Pages are opened explicitly; viewing frames never holds the action lock. |
| `steering_delivery.py` | 90 | Forward same-channel input once; provider acknowledgment owns delivery. |
| `store.py` | 239 | JSONL message persistence for the chat room with observer callbacks. |
| `supervisor.py` | 1044 | Room supervisor: owns the room server process and executes approved deployments. |
| `team_builder.py` | 591 | User-approved team proposals, drafted by the room's connected native agents. |
| `ui_composing.py` | 122 | Whether any open room tab may be mid-edit, for the deployment typing guard (frontend 7cfd8ed6, Astra 26a91642, design 7712dc49). Tabs send booleans only: no text, drafts, ids or times. |
| `ui_sessions.py` | 148 | Browser sign-in for the web room, so the page never carries the UI token (security c6e72a34, design Docs/security/ui-session-proposal.md r2, approved e1776689). |
| `video_analysis.py` | 347 | Video analysis for agents (Zero #14935, task 53e691c7): measure a video so an agent can judge its own export against a reference instead of guessing from a few stills. |
| `work_continuity.py` | 638 | Work continuity: the room keeps work moving itself (Docs/work-continuity.md r3, task c54e453b, Zero #16289). |
| `wrapper.py` | 836 | Agent wrapper - runs the real interactive CLI with auto-trigger on @mentions. |
| `wrapper_api.py` | 350 | API agent wrapper - bridges the chat room to an OpenAI-compatible endpoint. |
| `wrapper_unix.py` | 280 | Mac/Linux agent injection - pastes the prompt into the agent CLI via tmux. |
| `wrapper_windows.py` | 438 | Windows agent injection - uses Win32 WriteConsoleInput to type into the agent CLI. |
<!-- /generated -->

## Frontend (`apps/desktop/renderer/src`)

Built by `npm run build` in `apps/desktop/renderer/` into `apps/desktop/renderer/static/modern/workspace.js` and `workspace.css`; never edit the built files.

<!-- generated:frontend-files -->
| File | Lines | Purpose (file header) |
|-|-|-|
| `annotate/annotate.css` | 49 | Annotation dialog. Uses the shared palette tokens; --font-ui keeps a fallback for pages without the shell. |
| `annotate/AnnotationEditor.tsx` | 310 | Image annotation: numbered boxes, arrows and pins with a note per mark. |
| `annotate/ImageViewer.tsx` | 78 | - |
| `annotate/index.tsx` | 66 | Open the annotation dialog for any same-origin image and upload the result. Used by Board attachments and exposed as window.ZeroAnnotate so the legacy chat lightbox can offer the same tool without importing React code. |
| `annotate/viewer.css` | 24 | - |
| `App.tsx` | 62 | - |
| `board/ActivityFeed.tsx` | 134 | Live team activity: who is running right now, then the newest board events (check-ins, ownership/status moves, notes, unlinked reviews and follow-ups). |
| `board/Attachments.tsx` | 248 | Task images: thumbnails on cards, a gallery in task detail with upload by picker, paste or drop, and annotation into a new derived image. The task's list is replaced as a whole through POST /api/runtime/tasks. |
| `board/board.css` | 329 | Board styles. Scoped to .bd- and built on the shared palette tokens (static/style.css :root, overridden by the workspace shell). Every token used here is defined at :root; only --font-ui keeps a fallback for pages without the shell. |
| `board/Board.tsx` | 308 | Project board: kanban of owned tasks plus reviews/follow-ups, spec, memory and execution controls. Replaces the legacy static/project.js panel and uses the same /api/runtime endpoints and project_state/runtime_state events. |
| `board/BoardToolbar.tsx` | 88 | Task list toolbar: text, owner and area filters, grouping, ordering, the feed toggle and New task. |
| `board/Coordination.tsx` | 447 | Peer reviews and follow-ups (POST /api/runtime/reviews/followups). The human acts as "user": may request/cancel any review, respond when assigned as reviewer, and create/resolve/cancel any follow-up. |
| `board/detail/EditTask.tsx` | 88 | Task editor: title, details, areas, reserved paths and dependencies. |
| `board/detail/ExecutionFacts.tsx` | 63 | What is happening with an unfinished task now: execution state, next action, priority and any pending ownership transfer. |
| `board/detail/TaskOverview.tsx` | 50 | Read-only task body: details, areas, reserved paths and dependency links. |
| `board/detail/TaskProgress.tsx` | 59 | Check-in form and the task's update history, newest first. |
| `board/detail/TaskRecords.tsx` | 59 | Evidence, reviews and follow-ups attached to one task. |
| `board/detail/useResizablePanel.ts` | 36 | Width of the task detail panel, resizable by pointer drag or arrow keys. The width is published as --detail-width on the enclosing .bd-tasks grid. |
| `board/Lane.tsx` | 69 | One swimlane: a status column per task state, with the done column trimmed to recent cards. |
| `board/lanes.ts` | 54 | Filtering, swimlane grouping and column ordering for the task board. |
| `board/ManagerAuditPanel.tsx` | 199 | One saved Project manager identity. The server chooses its coordination task, chat and project scope; the Team form saves identity and automatic checks together. |
| `board/NewTaskForm.tsx` | 152 | - |
| `board/ProjectPanels.tsx` | 300 | Optional spec, project memory, execution limits and conversation budgets. Same endpoints and limit ranges as the legacy static/project.js panel. |
| `board/shared.tsx` | 206 | - |
| `board/TaskCard.tsx` | 216 | - |
| `board/TaskDetail.tsx` | 205 | Task detail panel: status, ownership, reservations, dependencies, check-ins, reviews and follow-ups. All writes go through POST /api/runtime/tasks. The sections live in ./detail; this component owns status and ownership. |
| `board/useBoardMotion.ts` | 88 | - |
| `board/useBoardPrefs.ts` | 50 | Board view preferences remembered in localStorage: section tab, swimlane grouping, card order and whether the activity feed is open. |
| `board/useChangeHighlights.ts` | 33 | Ids of tasks that changed while the board is open, each kept for a short highlight. |
| `components/ActivityPage.tsx` | 11 | Server activity retains the existing runtime log and Changes controls. |
| `components/attention.css` | 12 | - |
| `components/AttentionBell.tsx` | 275 | Header bell: everything waiting on the user, with a count and a link to the exact place to act. |
| `components/backend-controls.css` | 22 | - |
| `components/BackendControls.tsx` | 121 | - |
| `components/brand.ts` | 9 | - |
| `components/ChatSearch.tsx` | 76 | Ctrl+K quick switcher: jump to any chat (open, closed or pre-project history), project or view by title. It searches titles, not message text. Opening a chat goes through channels.js. |
| `components/deployments.css` | 66 | - |
| `components/Deployments.tsx` | 331 | - |
| `components/ErrorBoundary.tsx` | 32 | - |
| `components/HeaderBar.tsx` | 172 | The single header row: where you are (chat or view, then project) on the left; the team roster and the room's stop/resume on the right. |
| `components/Help.tsx` | 25 | - |
| `components/Icon.tsx` | 27 | - |
| `components/leader.css` | 8 | - |
| `components/LegacySlot.tsx` | 10 | - |
| `components/media-attachment.css` | 5 | - |
| `components/MediaAttachment.tsx` | 32 | - |
| `components/releaseGrant.css` | 6 | - |
| `components/ReleaseGrant.tsx` | 32 | - |
| `components/releaseGrantApi.ts` | 21 | - |
| `components/server.css` | 20 | - |
| `components/ServerPage.tsx` | 41 | - |
| `components/SessionNotice.tsx` | 17 | - |
| `components/SettingsDialog.tsx` | 32 | Settings as a dialog. The fields are settings.js's own #settings-bar, moved here rather than re-implemented, so auto-save (blur/Enter for text, change for selects) and the sound rows keep working. |
| `components/speech-bubble.css` | 23 | Shared speech bubble (task bbd70aad). The box eases its width and height; the text keeps its size. |
| `components/SpeechBubble.tsx` | 114 | One speech bubble for Live and Board cards (task bbd70aad, Zero #14495: bubbles were cut off). - Never cuts mid-sentence: it grows with its text up to `maxLines`, then keeps the newest lines in view and fades the top edge. |
| `components/useReleaseGrant.ts` | 55 | - |
| `components/WorkControls.tsx` | 142 | - |
| `decisions/ChatDecision.tsx` | 239 | - |
| `decisions/DecisionDetail.tsx` | 123 | - |
| `decisions/decisions.css` | 147 | - |
| `decisions/Decisions.tsx` | 75 | - |
| `decisions/model.ts` | 80 | Existing decision_flow.py contract. No new stage transitions are inferred here. |
| `decisions/Setup.tsx` | 46 | - |
| `follow/follow.css` | 85 | Follow mode. Built on the shared palette tokens; motion is dropped under reduced motion. |
| `follow/Follow.tsx` | 258 | Follow the current executing task from the server, with explicit event links taking priority. |
| `follow/followStore.ts` | 477 | Follow mode: a live view of what the team is doing, built only from events the room already emits (runtime_start/delta/tool/end, project_state, browser_activity). |
| `follow/pip.css` | 29 | Reserved media lane: image inspection never covers an actionable task card. |
| `follow/Pip.tsx` | 140 | Existing Follow evidence, kept in a reserved lane. Saved images are immutable; browser frames update only while their own page is visible and not being read. |
| `legacy.ts` | 65 | - |
| `lib/api.ts` | 75 | Fetch helpers for the existing FastAPI routes. Web clients use the same-origin UI cookie; desktop clients also carry their preload-provided session token. |
| `lib/constants.ts` | 44 | Values shared across the workspace. Field limits mirror the server's caps (named alongside), so the browser stops typing where the server would truncate or reject the text. Change both sides together. |
| `lib/mediaAttachment.ts` | 8 | Only local uploaded media becomes an inline player. Never fetch arbitrary message URLs. |
| `lib/motionCore.ts` | 2 | - |
| `lib/motionStudio.ts` | 37 | - |
| `lib/responders.ts` | 14 | - |
| `lib/room.ts` | 286 | Bridge between React views and the legacy room client (static/core.js, static/store.js, static/chat.js). |
| `lib/storage.ts` | 18 | localStorage throws when storage is disabled or full (private windows, policy); view preferences are optional, so reads fall back and writes are dropped. |
| `lib/teamBuilder.ts` | 149 | Team builder API (/api/runtime/team-builder), contract agreed with the backend owner in follow-up 65ef16f7. Proposals are drafted and challenged by connected agents, saved server-side, and only "Add approved members" changes the roster. |
| `lib/types.ts` | 257 | Contract types for the existing backend (room_runtime.py, project_room.py, coordination_records.py). These mirror server payloads; they are not a new API. |
| `live/live.css` | 115 | Live view v2 (task b42437b9). Boxless people on an open stage, one watch session beside it. Colours come from the app theme; each person's own colour is --c. |
| `live/liveFeed.ts` | 449 | Live view data seam (task b42437b9). Live is about the team right now: who is working, with whom, on what, and what they last produced. The shape follows backend's pinned /api/live contract (b1af7a3a, follow-up ec632edd). |
| `live/liveLayout.ts` | 170 | Live stage layout (task b42437b9): pure functions, no DOM. |
| `live/LiveSession.tsx` | 129 | The watch session beside the stage (task b42437b9, design contract 9f2c917a). Follow this work: one task's current people, conversation and evidence, kept open through handoffs. |
| `live/LiveStage.tsx` | 343 | The Live stage (task b42437b9): the team as boxless people. |
| `live/LiveTeam.tsx` | 129 | Live view v2 (task b42437b9, Zero #14152). |
| `live/useLiveBrowser.ts` | 91 | - |
| `main.tsx` | 15 | - |
| `motion/motion.css` | 28 | - |
| `motion/MotionPage.tsx` | 176 | - |
| `shell.css` | 172 | Shell: Codex/Claude-style sidebar (Sidebar.tsx), one header row (HeaderBar.tsx) and the Ctrl+K switcher (ChatSearch.tsx). Loaded after styles.css. |
| `sidepanel/ChangesView.tsx` | 23 | - |
| `sidepanel/fileview.css` | 26 | - |
| `sidepanel/FileView.tsx` | 202 | File tab of the workspace side panel: a read-only view of a file referenced in chat, with the copy of the code and Git state it came from. |
| `sidepanel/NewPanelTab.tsx` | 81 | - |
| `sidepanel/ReviewFileView.tsx` | 38 | - |
| `sidepanel/sidepanel.css` | 80 | - |
| `sidepanel/SidePanel.tsx` | 234 | - |
| `sidepanel/usePanelTabs.ts` | 83 | - |
| `styles.css` | 166 | Shared workspace tokens; legacy feature styles consume the same palette. |
| `team/ApiKeys.tsx` | 449 | Team > Providers, API keys (Docs/providers.md S5a, provider_store.py key routes). Keys are for OpenAI-compatible servers the room calls itself, such as OpenRouter. |
| `team/builder/BriefStep.tsx` | 127 | Step 1: the brief, and which connected agents draft and challenge it. |
| `team/builder/Coverage.tsx` | 103 | Step 2: read-only matrix of which member leads or supports each need in the brief. |
| `team/builder/labels.ts` | 19 | Display labels and small formatters shared by the team builder steps. |
| `team/builder/MemberCard.tsx` | 249 | Step 2: one proposed member, with approve / edit / revise / decline decisions. |
| `team/builder/Summary.tsx` | 112 | Step 3: the proposed team at a glance, and adding the approved members. |
| `team/ProfileEditor.tsx` | 389 | Agent profile form, ported from static/profiles.js with the same validation hints, handle generation, model/effort catalogs and archive semantics. |
| `team/ProviderForm.tsx` | 225 | Add or edit a provider connection (Team > Providers, provider_store.py). Subscription connections have a name and color; a local OpenAI-compatible server also has its address, model and maximum response length. |
| `team/Providers.tsx` | 367 | Team > Providers: which provider apps are installed and signed in, and which local model servers are running (GET /api/runtime/providers/status, provider_setup.py). |
| `team/roleProfile.css` | 13 | - |
| `team/RoleProfile.tsx` | 58 | - |
| `team/roleProfileApi.ts` | 40 | - |
| `team/team.css` | 112 | Team page. Reuses the board primitives (.bd-btn, .bd-field, .bd-panel, tokens). |
| `team/Team.tsx` | 314 | Team page: named agent profiles (static/profiles.js) and their live runtime controls (static/runtime.js provider cards) in one place. |
| `team/teamBuilder.css` | 107 | Team builder (approved mockup final.html). Reuses board tokens and buttons. |
| `team/TeamBuilder.tsx` | 230 | Team builder: Brief > Review team > Add approved members. Implements the user-approved design (task ccc5fbdb, mockup final.html 5783b87f5e0f1949). |
| `team/useRoleProfile.ts` | 62 | - |
| `workspace/browser.css` | 43 | - |
| `workspace/Browser.tsx` | 336 | - |
| `workspace/NewProject.tsx` | 37 | - |
| `workspace/ProjectActions.tsx` | 61 | - |
| `workspace/projectData.ts` | 146 | Project index (project_registry.py): every repository or conversation this room has worked in, with its board counts, team, goal and channels. |
| `workspace/ProjectHistory.tsx` | 66 | Saved project history has its own state. Never mount the active room's composer here. |
| `workspace/projectNavigation.css` | 49 | - |
| `workspace/projects.css` | 95 | Projects page and sidebar switcher. Built on the board tokens (.bd-root). |
| `workspace/Projects.tsx` | 235 | Projects page: every repository or conversation the room has worked in, side by side. |
| `workspace/ProjectSwitcher.tsx` | 36 | Sidebar project picker: always shows which project is attached and jumps to any other. |
| `workspace/Repository.tsx` | 241 | Repository controls for the active project: the attached repositories (the primary one is each agent's working folder; additional ones get their own worktrees), changing, adding, promoting and removing them, a change in progress, an agent's room_repository request, and the repositories used before. Every change keeps the board, chats, team, goal and queued work; running turns finish first unless interrupted, and new turns wait for the worktrees. |
| `workspace/Sidebar.tsx` | 130 | Workspace sidebar, organized like the Codex and Claude desktop apps: new chat and search first, the views, then projects with the active project's chats nested under it. |
| `workspace/useWorkspaceRoute.ts` | 102 | The workspace view and focused project, kept in the URL (?view=, ?project=) so reloads, links and the browser Back button land on the same page. |
<!-- /generated -->

## Chat client (`apps/desktop/renderer/static/`)

Loaded by `apps/desktop/renderer/static/index.html` in this order (see [dependency-map.md](dependency-map.md)):

<!-- generated:scripts -->
1. `/static/vendor/marked.min.js`
2. `/static/core.js`
3. `/static/store.js`
4. `/static/prefs.js`
5. `/static/channels.js`
6. `/static/chat/fileReferences.js`
7. `/static/chat/composing.js`
8. `/static/chat.js`
9. `/static/chat/avatars.js`
10. `/static/chat/sounds.js`
11. `/static/chat/format.js`
12. `/static/chat/dates.js`
13. `/static/chat/transfer.js`
14. `/static/chat/settings.js`
15. `/static/chat/composer.js`
16. `/static/chat/menus.js`
17. `/static/chat/reply.js`
18. `/static/chat/delete.js`
19. `/static/chat/voice.js`
20. `/static/chat/modal.js`
21. `/static/runtime.js`
<!-- /generated -->

## Tests (`tests/`)

<!-- generated:tests -->
- `tests/browser/voice_typing.py`
- `tests/test_agent_identity.py`
- `tests/test_agent_skills.py`
- `tests/test_archive_feature.py`
- `tests/test_batch_resolve.py`
- `tests/test_broadcast_delivery.py`
- `tests/test_channel_fallback.py`
- `tests/test_chat_sessions.py`
- `tests/test_claude_result.py`
- `tests/test_cli_homes.py`
- `tests/test_config_overrides.py`
- `tests/test_coordination_state.py`
- `tests/test_deploy_candidates.py`
- `tests/test_diff_collection.py`
- `tests/test_event_log.py`
- `tests/test_git_preview.py`
- `tests/test_handoff_limit.py`
- `tests/test_handover_hooks.py`
- `tests/test_history_delivery.py`
- `tests/test_host_handover.py`
- `tests/test_identity_contract.py`
- `tests/test_inject_transport.py`
- `tests/test_live_context.py`
- `tests/test_local_files.py`
- `tests/test_manager_audit_native.py`
- `tests/test_manager_wakes.py`
- `tests/test_mcp_proxy.py`
- `tests/test_motion_encode.py`
- `tests/test_motion_s5.py`
- `tests/test_native_rpc.py`
- `tests/test_pending_recovery.py`
- `tests/test_process_enforcement.py`
- `tests/test_process_rules.py`
- `tests/test_profile_bell.py`
- `tests/test_profile_core.py`
- `tests/test_project_manager.py`
- `tests/test_queue_file.py`
- `tests/test_ready_wakes.py`
- `tests/test_registry_persistence.py`
- `tests/test_release_adapter.py`
- `tests/test_release_block_wait.py`
- `tests/test_release_checks.py`
- `tests/test_release_completion_filter.py`
- `tests/test_restored_chain.py`
- `tests/test_review_hashing.py`
- `tests/test_room_goals.py`
- `tests/test_room_markup.py`
- `tests/test_room_name.py`
- `tests/test_router.py`
- `tests/test_session_reclaim.py`
- `tests/test_settings_persistence.py`
- `tests/test_state_writers.py`
- `tests/test_steering_delivery.py`
- `tests/test_store_persistence.py`
- `tests/test_task_closure.py`
- `tests/test_team_builder.py`
- `tests/test_thread_busy.py`
- `tests/test_turn_context.py`
- `tests/test_upload_bounds.py`
- `tests/test_upload_files.py`
- `tests/test_video_analysis.py`
- `tests/test_wake_queue_bounds.py`
- `tests/test_work_continuity.py`
- `tests/test_workspace_state.py`
- `tests/test_wrapper_activity.py`
- `tests/test_wrapper_mcp_config.py`
- `tests/test_wrapper_prompts.py`
- `tests/test_wrapper_unix_inject.py`
<!-- /generated -->
