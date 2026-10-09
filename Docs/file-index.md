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
| `agent_profiles.py` | 94 | Project-local expert identities, independent of provider subscriptions. |
| `agents.py` | 70 | Agent trigger - writes to queue files picked up by visible worker terminals. |
| `app.py` | 1304 | ZeroCollab - FastAPI web UI + agent auto-trigger. |
| `archive.py` | 294 | Export/import archive for project history (chat messages). |
| `build_release.py` | 16 | Retired ZIP entrypoint; use release_pack.py for managed release packages. |
| `config_loader.py` | 137 | Shared config loader - merges config.toml + config.local.toml. |
| `coordination_queue.py` | 110 | Coalesce pending wake-ups; resolve record references immediately before delivery. |
| `coordination_records.py` | 238 | Durable peer reviews and follow-ups shared by the board and agent tools. |
| `coordination_state.py` | 70 | Task execution facts. Waiting is derived from records, never from elapsed time. |
| `durable.py` | 24 | Atomic app-side text persistence; callers own transaction ordering and locks. |
| `git_preview.py` | 82 | Bounded retained output for read-only Git previews (called off the event loop). |
| `local_protocol.py` | 40 | Classify local model output and validate structured calls before execution. |
| `login_gemini.py` | 12 | Open the official Gemini sign-in UI without inheriting API-key billing settings. |
| `mcp_bridge.py` | 618 | MCP server for agent chat tools - runs alongside the web server. |
| `mcp_proxy.py` | 336 | Per-instance MCP identity proxy. |
| `native_adapters.py` | 586 | Official, locally authenticated CLI protocols. No credential handling. |
| `project_registry.py` | 790 | Index of every project this room has worked in: board, team, goal, chats and repository. |
| `project_room.py` | 873 | Durable project coordination and bounded room context, without model calls. |
| `provider_controls.py` | 166 | Room-local model/effort preferences and official, read-only provider catalogs. |
| `queue_file.py` | 75 | Coordinate JSONL queue producers, consumers and explicit clears across processes. |
| `registry.py` | 816 | Runtime Registry - single source of truth for all live agent state. |
| `release_pack.py` | 218 | Package a committed revision as a release the supervisor can deploy. |
| `room_deploy.py` | 256 | App side of the supervisor protocol. This module never stops or replaces the server. |
| `room_goals.py` | 134 | Explicit shared goals. Event-driven continuation, never a background model poll. |
| `room_markup.py` | 96 | Bounded raster markup shared by agent tools; no network or chat side effects. |
| `room_mcp.py` | 158 | Run-scoped local coordination tools for official native CLIs. |
| `room_runtime.py` | 1784 | Managed agents for the ZeroCollab persistent workspace. |
| `router.py` | 105 | Message routing based on @mentions with per-channel loop guard. |
| `run.py` | 182 | Entry point - starts MCP server (port 8200) + web UI (port 8300). |
| `steering_delivery.py` | 90 | Forward same-channel input once; provider acknowledgment owns delivery. |
| `store.py` | 239 | JSONL message persistence for the chat room with observer callbacks. |
| `supervisor.py` | 848 | Room supervisor: owns the room server process and executes approved deployments. |
| `team_builder.py` | 323 | User-approved team proposals, drafted by the room's connected native agents. |
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
| `annotate/index.tsx` | 54 | Open the annotation dialog for any same-origin image and upload the result. Used by Board attachments and exposed as window.ZeroAnnotate so the legacy chat lightbox can offer the same tool without importing React code. |
| `App.tsx` | 64 | - |
| `board/ActivityFeed.tsx` | 134 | Live team activity: who is running right now, then the newest board events (check-ins, ownership/status moves, notes, unlinked reviews and follow-ups). |
| `board/Attachments.tsx` | 183 | Task images: thumbnails on cards, a gallery in task detail with upload by picker, paste or drop, and annotation into a new derived image. The task's list is replaced as a whole through POST /api/runtime/tasks. |
| `board/board.css` | 296 | Board styles. Scoped to .bd- and built on the shared palette tokens (static/style.css :root, overridden by the workspace shell). Every token used here is defined at :root; only --font-ui keeps a fallback for pages without the shell. |
| `board/Board.tsx` | 263 | Project board: kanban of owned tasks plus reviews/follow-ups, spec, memory and execution controls. Replaces the legacy static/project.js panel and uses the same /api/runtime endpoints and project_state/runtime_state events. |
| `board/BoardToolbar.tsx` | 88 | Task list toolbar: text, owner and area filters, grouping, ordering, the feed toggle and New task. |
| `board/Coordination.tsx` | 447 | Peer reviews and follow-ups (POST /api/runtime/reviews/followups). The human acts as "user": may request/cancel any review, respond when assigned as reviewer, and create/resolve/cancel any follow-up. |
| `board/detail/EditTask.tsx` | 88 | Task editor: title, details, areas, reserved paths and dependencies. |
| `board/detail/ExecutionFacts.tsx` | 63 | What is happening with an unfinished task now: execution state, next action, priority and any pending ownership transfer. |
| `board/detail/TaskOverview.tsx` | 50 | Read-only task body: details, areas, reserved paths and dependency links. |
| `board/detail/TaskProgress.tsx` | 59 | Check-in form and the task's update history, newest first. |
| `board/detail/TaskRecords.tsx` | 59 | Evidence, reviews and follow-ups attached to one task. |
| `board/detail/useResizablePanel.ts` | 36 | Width of the task detail panel, resizable by pointer drag or arrow keys. The width is published as --detail-width on the enclosing .bd-tasks grid. |
| `board/Lane.tsx` | 65 | One swimlane: a status column per task state, with the done column trimmed to recent cards. |
| `board/lanes.ts` | 54 | Filtering, swimlane grouping and column ordering for the task board. |
| `board/NewTaskForm.tsx` | 139 | - |
| `board/ProjectPanels.tsx` | 278 | Optional spec, project memory, execution limits and conversation budgets. Same endpoints and limit ranges as the legacy static/project.js panel. |
| `board/shared.tsx` | 206 | - |
| `board/TaskCard.tsx` | 194 | - |
| `board/TaskDetail.tsx` | 205 | Task detail panel: status, ownership, reservations, dependencies, check-ins, reviews and follow-ups. All writes go through POST /api/runtime/tasks. The sections live in ./detail; this component owns status and ownership. |
| `board/useBoardPrefs.ts` | 50 | Board view preferences remembered in localStorage: section tab, swimlane grouping, card order and whether the activity feed is open. |
| `board/useChangeHighlights.ts` | 33 | Ids of tasks that changed while the board is open, each kept for a short highlight. |
| `components/ActivityPage.tsx` | 22 | Activity & controls page: deployments, then the legacy runtime panel (agent requests, tool activity and the Changes view). |
| `components/brand.ts` | 9 | - |
| `components/deployments.css` | 29 | - |
| `components/Deployments.tsx` | 148 | - |
| `components/ErrorBoundary.tsx` | 32 | - |
| `components/Help.tsx` | 23 | - |
| `components/Icon.tsx` | 11 | - |
| `components/LegacySlot.tsx` | 10 | - |
| `components/WelcomeBar.tsx` | 20 | One-line orientation shown until the user dismisses it (remembered in localStorage). |
| `components/WorkControls.tsx` | 77 | - |
| `legacy.ts` | 49 | - |
| `lib/api.ts` | 65 | Fetch helpers for the existing FastAPI routes. The server injects window.__SESSION_TOKEN__ into the page; every /api call must carry it. |
| `lib/constants.ts` | 42 | Values shared across the workspace. Field limits mirror the server's caps (named alongside), so the browser stops typing where the server would truncate or reject the text. Change both sides together. |
| `lib/room.ts` | 258 | Bridge between React views and the legacy room client (static/core.js, static/store.js, static/chat.js). |
| `lib/storage.ts` | 18 | localStorage throws when storage is disabled or full (private windows, policy); view preferences are optional, so reads fall back and writes are dropped. |
| `lib/teamBuilder.ts` | 122 | Team builder API (/api/runtime/team-builder), contract agreed with the backend owner in follow-up 65ef16f7. Proposals are drafted and challenged by connected agents, saved server-side, and only "Add approved members" changes the roster. |
| `lib/types.ts` | 234 | Contract types for the existing backend (room_runtime.py, project_room.py, coordination_records.py). These mirror server payloads; they are not a new API. |
| `main.tsx` | 15 | - |
| `styles.css` | 208 | Shared workspace tokens; legacy feature styles consume the same palette. |
| `team/builder/BriefStep.tsx` | 118 | Step 1: the brief, and which connected agents draft and challenge it. |
| `team/builder/Coverage.tsx` | 103 | Step 2: read-only matrix of which member leads or supports each need in the brief. |
| `team/builder/labels.ts` | 19 | Display labels and small formatters shared by the team builder steps. |
| `team/builder/MemberCard.tsx` | 228 | Step 2: one proposed member, with approve / edit / revise / decline decisions. |
| `team/builder/Summary.tsx` | 101 | Step 3: the proposed team at a glance, and adding the approved members. |
| `team/ProfileEditor.tsx` | 337 | Agent profile form, ported from static/profiles.js with the same validation hints, handle generation, model/effort catalogs and archive semantics. |
| `team/team.css` | 43 | Team page. Reuses the board primitives (.bd-btn, .bd-field, .bd-panel, tokens). |
| `team/Team.tsx` | 265 | Team page: named agent profiles (static/profiles.js) and their live runtime controls (static/runtime.js provider cards) in one place. |
| `team/teamBuilder.css` | 91 | Team builder (approved mockup final.html). Reuses board tokens and buttons. |
| `team/TeamBuilder.tsx` | 184 | Team builder: Brief > Review team > Add approved members. Implements the user-approved design (task ccc5fbdb, mockup final.html 5783b87f5e0f1949). |
| `workspace/NewProject.tsx` | 37 | - |
| `workspace/projectData.ts` | 144 | Project index (project_registry.py): every repository or conversation this room has worked in, with its board counts, team, goal and channels. |
| `workspace/projects.css` | 95 | Projects page and sidebar switcher. Built on the board tokens (.bd-root). |
| `workspace/Projects.tsx` | 216 | Projects page: every repository or conversation the room has worked in, side by side. |
| `workspace/ProjectSwitcher.tsx` | 36 | Sidebar project picker: always shows which project is attached and jumps to any other. |
| `workspace/Repository.tsx` | 237 | Repository controls for the active project: the attached repositories (the primary one is each agent's working folder; additional ones get their own worktrees), changing, adding, promoting and removing them, a change in progress, an agent's room_repository request, and the repositories used before. Every change keeps the board, chats, team, goal and queued work; running turns finish first unless interrupted, and new turns wait for the worktrees. |
| `workspace/useWorkspaceRoute.ts` | 63 | The workspace view and focused project, kept in the URL (?view=, ?project=) so reloads, links and the browser Back button land on the same page. |
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
