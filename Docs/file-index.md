# File and module index

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
| `frontend/` | React workspace source (Vite + TypeScript), built into `static/modern/` |
| `static/` | Chat client served as-is, plus the built workspace bundle |
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
| `agent_profiles.py` | 146 | Project-local expert identities, independent of provider subscriptions. |
| `agents.py` | 70 | Agent trigger - writes to queue files picked up by visible worker terminals. |
| `app.py` | 1437 | ZeroCollab - FastAPI web UI + agent auto-trigger. |
| `archive.py` | 331 | Export/import archive for project history (chat messages). |
| `build_release.py` | 16 | Retired ZIP entrypoint; use release_pack.py for managed release packages. |
| `config_loader.py` | 137 | Shared config loader - merges config.toml + config.local.toml. |
| `coordination_queue.py` | 117 | Coalesce pending wake-ups; resolve record references immediately before delivery. |
| `coordination_records.py` | 312 | Durable peer reviews and follow-ups shared by the board and agent tools. |
| `coordination_state.py` | 70 | Task execution facts. Waiting is derived from records, never from elapsed time. |
| `decision_flow.py` | 637 | Team decisions: a bounded, attributed discussion that ends in a user-recorded decision. |
| `durable.py` | 24 | Atomic app-side text persistence; callers own transaction ordering and locks. |
| `file_preview.py` | 264 | Read-only views of files referenced in chat messages. |
| `git_preview.py` | 82 | Bounded retained output for read-only Git previews (called off the event loop). |
| `local_protocol.py` | 40 | Classify local model output and validate structured calls before execution. |
| `login_gemini.py` | 12 | Open the official Gemini sign-in UI without inheriting API-key billing settings. |
| `mcp_bridge.py` | 618 | MCP server for agent chat tools - runs alongside the web server. |
| `mcp_proxy.py` | 336 | Per-instance MCP identity proxy. |
| `native_adapters.py` | 715 | Official, locally authenticated CLI protocols. No credential handling. |
| `project_registry.py` | 834 | Index of every project this room has worked in: board, team, goal, chats and repository. |
| `project_room.py` | 1181 | Durable project coordination and bounded room context, without model calls. |
| `provider_controls.py` | 200 | Room-local model/effort preferences and official, read-only provider catalogs. |
| `provider_keys.py` | 480 | API keys for OpenAI-compatible servers (Docs/providers.md, slice S5a). |
| `provider_prices.py` | 100 | Built-in API prices for Anthropic and OpenAI key connections (Docs/providers.md, S5b). |
| `provider_relay.py` | 436 | Room-side relay for Claude Code and Codex on the user's Anthropic and OpenAI API keys (Docs/providers.md, slice S5b; security d807412d). |
| `provider_setup.py` | 510 | Provider setup for Team > Providers (Docs/providers.md, slices S1 and S2). |
| `provider_store.py` | 827 | In-app provider connections (Docs/providers.md, slice S3). |
| `queue_file.py` | 75 | Coordinate JSONL queue producers, consumers and explicit clears across processes. |
| `ready_wakes.py` | 251 | Ready-task wakes: wake an idle owner once for authorised, actionable work it already owns. |
| `registry.py` | 824 | Runtime Registry - single source of truth for all live agent state. |
| `release_pack.py` | 218 | Package a committed revision as a release the supervisor can deploy. |
| `room_deploy.py` | 256 | App side of the supervisor protocol. This module never stops or replaces the server. |
| `room_goals.py` | 164 | Explicit shared goals. Event-driven continuation, never a background model poll. |
| `room_markup.py` | 96 | Bounded raster markup shared by agent tools; no network or chat side effects. |
| `room_mcp.py` | 217 | Run-scoped local coordination tools for official native CLIs. |
| `room_runtime.py` | 2159 | Managed agents for the ZeroCollab persistent workspace. |
| `router.py` | 108 | Message routing based on @mentions with per-channel loop guard. |
| `run.py` | 221 | Entry point - starts MCP server (port 8200) + web UI (port 8300). |
| `shared_browser.py` | 814 | One disposable, project-scoped Chromium browser shared by the user and room agents: one page by default, more only on an explicit new_page request. |
| `steering_delivery.py` | 90 | Forward same-channel input once; provider acknowledgment owns delivery. |
| `store.py` | 239 | JSONL message persistence for the chat room with observer callbacks. |
| `supervisor.py` | 848 | Room supervisor: owns the room server process and executes approved deployments. |
| `team_builder.py` | 530 | User-approved team proposals, drafted by the room's connected native agents. |
| `wrapper.py` | 836 | Agent wrapper - runs the real interactive CLI with auto-trigger on @mentions. |
| `wrapper_api.py` | 350 | API agent wrapper - bridges the chat room to an OpenAI-compatible endpoint. |
| `wrapper_unix.py` | 280 | Mac/Linux agent injection - pastes the prompt into the agent CLI via tmux. |
| `wrapper_windows.py` | 438 | Windows agent injection - uses Win32 WriteConsoleInput to type into the agent CLI. |
<!-- /generated -->

## Frontend (`frontend/src`)

Built by `npm run build` in `frontend/` into `static/modern/workspace.js` and `workspace.css`; never edit the built files.

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
| `board/Attachments.tsx` | 240 | Task images: thumbnails on cards, a gallery in task detail with upload by picker, paste or drop, and annotation into a new derived image. The task's list is replaced as a whole through POST /api/runtime/tasks. |
| `board/board.css` | 303 | Board styles. Scoped to .bd- and built on the shared palette tokens (static/style.css :root, overridden by the workspace shell). Every token used here is defined at :root; only --font-ui keeps a fallback for pages without the shell. |
| `board/Board.tsx` | 284 | Project board: kanban of owned tasks plus reviews/follow-ups, spec, memory and execution controls. Replaces the legacy static/project.js panel and uses the same /api/runtime endpoints and project_state/runtime_state events. |
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
| `board/NewTaskForm.tsx` | 152 | - |
| `board/ProjectPanels.tsx` | 276 | Optional spec, project memory, execution limits and conversation budgets. Same endpoints and limit ranges as the legacy static/project.js panel. |
| `board/shared.tsx` | 206 | - |
| `board/TaskCard.tsx` | 210 | - |
| `board/TaskDetail.tsx` | 205 | Task detail panel: status, ownership, reservations, dependencies, check-ins, reviews and follow-ups. All writes go through POST /api/runtime/tasks. The sections live in ./detail; this component owns status and ownership. |
| `board/useBoardPrefs.ts` | 50 | Board view preferences remembered in localStorage: section tab, swimlane grouping, card order and whether the activity feed is open. |
| `board/useChangeHighlights.ts` | 33 | Ids of tasks that changed while the board is open, each kept for a short highlight. |
| `components/ActivityPage.tsx` | 24 | Activity retains compact deployment results; approvals and full history live in Server. |
| `components/AttentionBell.tsx` | 132 | Header bell: everything waiting on the user, with a count and a link to the exact place to act. |
| `components/backend-controls.css` | 15 | - |
| `components/BackendControls.tsx` | 105 | - |
| `components/brand.ts` | 9 | - |
| `components/ChatSearch.tsx` | 76 | Ctrl+K quick switcher: jump to any chat (open, closed or pre-project history), project or view by title. It searches titles, not message text. Opening a chat goes through channels.js. |
| `components/deployments.css` | 40 | - |
| `components/Deployments.tsx` | 224 | - |
| `components/ErrorBoundary.tsx` | 32 | - |
| `components/HeaderBar.tsx` | 162 | The single header row: where you are (chat or view, then project) on the left; the team roster and the room's stop/resume on the right. |
| `components/Help.tsx` | 25 | - |
| `components/Icon.tsx` | 25 | - |
| `components/LegacySlot.tsx` | 10 | - |
| `components/server.css` | 20 | - |
| `components/ServerPage.tsx` | 37 | - |
| `components/SettingsDialog.tsx` | 32 | Settings as a dialog. The fields are settings.js's own #settings-bar, moved here rather than re-implemented, so auto-save (blur/Enter for text, change for selects) and the sound rows keep working. |
| `components/WorkControls.tsx` | 142 | - |
| `decisions/DecisionDetail.tsx` | 127 | - |
| `decisions/decisions.css` | 38 | - |
| `decisions/Decisions.tsx` | 81 | - |
| `decisions/model.ts` | 64 | Existing decision_flow.py contract. No new stage transitions are inferred here. |
| `decisions/Setup.tsx` | 46 | - |
| `follow/follow.css` | 84 | Follow mode. Built on the shared palette tokens; motion is dropped under reduced motion. |
| `follow/Follow.tsx` | 215 | Follow mode UI: the header toggle, the Board's banner and LIVE ticker, the per-card overlay, and the Space/Esc shortcuts. |
| `follow/followStore.ts` | 431 | Follow mode: a live view of what the team is doing, built only from events the room already emits (runtime_start/delta/tool/end, project_state, browser_activity). |
| `follow/pip.css` | 27 | Follow mode picture-in-picture (Pip.tsx). Fixed above the LIVE ticker, bottom right. |
| `follow/Pip.tsx` | 141 | Follow mode F2: a picture-in-picture of the latest finished artifact while following. - An annotation (room_markup) shows the completed image with its numbered legend. Markup is atomic, so this is the finished result, never a live drawing. |
| `legacy.ts` | 65 | - |
| `lib/api.ts` | 70 | Fetch helpers for the existing FastAPI routes. The server injects window.__SESSION_TOKEN__ into the page; every /api call must carry it. |
| `lib/constants.ts` | 44 | Values shared across the workspace. Field limits mirror the server's caps (named alongside), so the browser stops typing where the server would truncate or reject the text. Change both sides together. |
| `lib/room.ts` | 286 | Bridge between React views and the legacy room client (static/core.js, static/store.js, static/chat.js). |
| `lib/storage.ts` | 18 | localStorage throws when storage is disabled or full (private windows, policy); view preferences are optional, so reads fall back and writes are dropped. |
| `lib/teamBuilder.ts` | 145 | Team builder API (/api/runtime/team-builder), contract agreed with the backend owner in follow-up 65ef16f7. Proposals are drafted and challenged by connected agents, saved server-side, and only "Add approved members" changes the roster. |
| `lib/types.ts` | 245 | Contract types for the existing backend (room_runtime.py, project_room.py, coordination_records.py). These mirror server payloads; they are not a new API. |
| `live/live.css` | 79 | - |
| `live/LiveTeam.tsx` | 120 | - |
| `live/useLiveBrowser.ts` | 65 | - |
| `live/useLiveTeam.ts` | 174 | - |
| `main.tsx` | 15 | - |
| `shell.css` | 172 | Shell: Codex/Claude-style sidebar (Sidebar.tsx), one header row (HeaderBar.tsx) and the Ctrl+K switcher (ChatSearch.tsx). Loaded after styles.css. |
| `sidepanel/ChangesView.tsx` | 23 | - |
| `sidepanel/fileview.css` | 26 | - |
| `sidepanel/FileView.tsx` | 202 | File tab of the workspace side panel: a read-only view of a file referenced in chat, with the copy of the code and Git state it came from. |
| `sidepanel/NewPanelTab.tsx` | 81 | - |
| `sidepanel/ReviewFileView.tsx` | 38 | - |
| `sidepanel/sidepanel.css` | 80 | - |
| `sidepanel/SidePanel.tsx` | 216 | - |
| `sidepanel/usePanelTabs.ts` | 78 | - |
| `styles.css` | 166 | Shared workspace tokens; legacy feature styles consume the same palette. |
| `team/ApiKeys.tsx` | 449 | Team > Providers, API keys (Docs/providers.md S5a, provider_store.py key routes). Keys are for OpenAI-compatible servers the room calls itself, such as OpenRouter. |
| `team/builder/BriefStep.tsx` | 118 | Step 1: the brief, and which connected agents draft and challenge it. |
| `team/builder/Coverage.tsx` | 103 | Step 2: read-only matrix of which member leads or supports each need in the brief. |
| `team/builder/labels.ts` | 19 | Display labels and small formatters shared by the team builder steps. |
| `team/builder/MemberCard.tsx` | 249 | Step 2: one proposed member, with approve / edit / revise / decline decisions. |
| `team/builder/Summary.tsx` | 112 | Step 3: the proposed team at a glance, and adding the approved members. |
| `team/ProfileEditor.tsx` | 386 | Agent profile form, ported from static/profiles.js with the same validation hints, handle generation, model/effort catalogs and archive semantics. |
| `team/ProviderForm.tsx` | 225 | Add or edit a provider connection (Team > Providers, provider_store.py). Subscription connections have a name and color; a local OpenAI-compatible server also has its address, model and maximum response length. |
| `team/Providers.tsx` | 357 | Team > Providers: which provider apps are installed and signed in, and which local model servers are running (GET /api/runtime/providers/status, provider_setup.py). |
| `team/team.css` | 104 | Team page. Reuses the board primitives (.bd-btn, .bd-field, .bd-panel, tokens). |
| `team/Team.tsx` | 308 | Team page: named agent profiles (static/profiles.js) and their live runtime controls (static/runtime.js provider cards) in one place. |
| `team/teamBuilder.css` | 106 | Team builder (approved mockup final.html). Reuses board tokens and buttons. |
| `team/TeamBuilder.tsx` | 230 | Team builder: Brief > Review team > Add approved members. Implements the user-approved design (task ccc5fbdb, mockup final.html 5783b87f5e0f1949). |
| `workspace/browser.css` | 43 | - |
| `workspace/Browser.tsx` | 242 | - |
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
| `workspace/useWorkspaceRoute.ts` | 91 | The workspace view and focused project, kept in the URL (?view=, ?project=) so reloads, links and the browser Back button land on the same page. |
<!-- /generated -->

## Chat client (`static/`)

Loaded by `static/index.html` in this order (see [dependency-map.md](dependency-map.md)):

<!-- generated:scripts -->
1. `/static/vendor/marked.min.js`
2. `/static/core.js`
3. `/static/store.js`
4. `/static/prefs.js`
5. `/static/channels.js`
6. `/static/chat.js`
7. `/static/chat/avatars.js`
8. `/static/chat/sounds.js`
9. `/static/chat/format.js`
10. `/static/chat/dates.js`
11. `/static/chat/transfer.js`
12. `/static/chat/settings.js`
13. `/static/chat/composer.js`
14. `/static/chat/menus.js`
15. `/static/chat/reply.js`
16. `/static/chat/delete.js`
17. `/static/chat/voice.js`
18. `/static/chat/modal.js`
19. `/static/runtime.js`
<!-- /generated -->

## Tests (`tests/`)

<!-- generated:tests -->
- `tests/browser/voice_typing.py`
- `tests/test_agent_identity.py`
- `tests/test_archive_feature.py`
- `tests/test_broadcast_delivery.py`
- `tests/test_channel_fallback.py`
- `tests/test_chat_sessions.py`
- `tests/test_config_overrides.py`
- `tests/test_coordination_state.py`
- `tests/test_deploy_candidates.py`
- `tests/test_diff_collection.py`
- `tests/test_git_preview.py`
- `tests/test_handoff_limit.py`
- `tests/test_history_delivery.py`
- `tests/test_identity_contract.py`
- `tests/test_inject_transport.py`
- `tests/test_mcp_proxy.py`
- `tests/test_native_rpc.py`
- `tests/test_pending_recovery.py`
- `tests/test_queue_file.py`
- `tests/test_ready_wakes.py`
- `tests/test_registry_persistence.py`
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
- `tests/test_team_builder.py`
- `tests/test_turn_context.py`
- `tests/test_upload_bounds.py`
- `tests/test_wake_queue_bounds.py`
- `tests/test_workspace_state.py`
- `tests/test_wrapper_activity.py`
- `tests/test_wrapper_mcp_config.py`
- `tests/test_wrapper_prompts.py`
- `tests/test_wrapper_unix_inject.py`
<!-- /generated -->
