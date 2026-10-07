# Project summary

ZeroCollab (version 0.9.0, `VERSION`) is a local, single-user workspace where a person and several AI coding agents share one chat, one kanban board and one team roster. It runs on the user's machine (Python 3.11+, default `http://127.0.0.1:8300`), uses each provider's own locally signed-in CLI, and never handles provider credentials or calls paid APIs on its own.

## What the user gets

| Area | What it does | Main code |
|-|-|-|
| Chat | Channels and chat sessions per project, attachments, streaming replies, @mention routing with a hop limit, slash commands `/goal` and `/clear`, voice typing, export/import of messages | `app.py`, `store.py`, `router.py`, `archive.py`, `static/chat*.js` |
| Managed agents | One CLI turn per wake-up in the agent's own Git worktree; per-agent Stop/Resume, model and effort; tool approvals; steering mid-turn; diffs per agent | `room_runtime.py`, `native_adapters.py`, `steering_delivery.py`, `provider_controls.py` |
| Board | Tasks with owners, reserved paths, dependencies, priority, next action, evidence, images; peer reviews and follow-ups; spec and notes; execution limits and budgets | `project_room.py`, `coordination_records.py`, `coordination_state.py`, `coordination_queue.py`, `frontend/src/board/` |
| Projects | Several projects, each with its own board, team, goal, chats and repositories; switch, create, attach or change repositories while running | `project_registry.py`, `frontend/src/workspace/` |
| Team | Project-local agent profiles (name, prompt, color, provider template); team proposals drafted by connected agents and approved by the user | `agent_profiles.py`, `team_builder.py`, `frontend/src/team/` |
| Goal | A user-started shared goal with a lead; continues on events, never on a timer poll | `room_goals.py` |
| Markup | Agents and the user annotate screenshots (boxes, arrows, pins) | `room_markup.py`, `frontend/src/annotate/` |
| Deployments | Releases packaged from a commit, approved in the Deployments panel, applied by the supervisor with drain, verification and rollback | `release_pack.py`, `room_deploy.py`, `supervisor.py`, `frontend/src/components/Deployments.tsx` |
| Local models | OpenAI-compatible endpoints as managed agents with validated structured tool calls | `local_protocol.py`, `wrapper_api.py` |
| Launcher agents | Older path: a visible terminal per agent, prompts typed in by a wrapper | `wrapper*.py`, `agents.py`, `queue_file.py`, `windows/`, `macos-linux/` |

Agents talk to the room through two MCP surfaces: the `chat_*` tools (`mcp_bridge.py`, HTTP 8200 and SSE 8201, identity per instance through `mcp_proxy.py`) and the run-scoped `room_*` coordination tools for managed native CLIs (`room_mcp.py`).

## Design rules that shape the code

- **Local and official only.** Providers run through their official CLIs, which are signed in locally. Credentials are never read or stored. `login_gemini.py` opens the official sign-in. It does not inherit API-key billing.
- **Durable records.** State lives in JSON and JSONL files under `data/` (see [file-index.md](file-index.md)). Writes are atomic (`durable.py`). Each change has one commit point, such as `project.json` for a repository change. A restart therefore recovers from the records rather than from memory.
- **Event-driven, no polling models.** Agents wake on mentions, board records, goal events or human input. Nothing calls a model on a timer.
- **The human approves.** Agents can request a repository, propose a team or ask for a deployment. Only the user applies these.
- **Isolation.** Each agent works in its own worktree and branch, one per attached repository. Reserved task paths prevent edit collisions. A key prefix (`key:path`) separates paths in different repositories.
- **Bounded context.** Room context, reads and diffs are capped. Examples are the 4000 to 6000 character pages from `room_read` and the per-agent `DIFF_CHAR_LIMIT`.

## Runtime shape

A single process (`run.py`) serves the FastAPI app, the WebSocket and both MCP servers. Shared objects live in that process: the store, registry, router, RoomRuntime and ProjectRoom. `supervisor.py` owns that process and is the only thing that stops or replaces it. Diagrams and flows are in [architecture.md](architecture.md).

The UI has two layers on one page:

- **Chat client.** Classic scripts in `static/`, which own the WebSocket.
- **Workspace.** A React and TypeScript bundle built by Vite from `frontend/src` into `static/modern/`. It renders the board, projects, team, activity and deployments, and receives WebSocket frames through `lib/room.ts`.

## Status and known gaps (as of 2026-10-07)

- Multi-repository projects are merged. Open items are listed in [repositories.md](repositories.md).
- `marked` is loaded from a CDN with no pinned version and no integrity hash.
- Import cycles exist between `project_registry`, `project_room`, `room_runtime` and `coordination_records`. They are resolved by function-level imports. See [dependency-map.md](dependency-map.md).
- `room_runtime.py` (about 1800 lines), `app.py` (about 1300) and `supervisor.py` (about 850) are the largest modules.
