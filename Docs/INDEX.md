# ZeroCollab documentation library

Start here. Each document covers one subject; read the one that matches the question instead of the whole set. User-facing guides stay at the repository root.

## Library

| Document | Category | Read it for |
|-|-|-|
| [project-summary.md](project-summary.md) | Overview | What ZeroCollab is, feature areas and the module that owns each, design rules, known gaps |
| [architecture.md](architecture.md) | Architecture | Process and browser layout (ASCII), and four flows: chat message, managed agent turn, repository change, deployment |
| [dependency-map.md](dependency-map.md) | Dependencies | Third-party packages and who uses them, the internal Python import graph and its cycles, frontend packages, chat script load order |
| [file-index.md](file-index.md) | Files and data | Every root file, Python module, frontend file and test with its purpose; the `data/` directory layout and which module writes each record |
| [providers.md](providers.md) | Feature design | Provider templates, sign-in and local models today (with gaps), auth options, local model plan, release build slices and open decisions |
| [repositories.md](repositories.md) | Feature design | Repositories per project as built: data model, worktrees, `key:path` scopes, the replace/add/primary/remove actions, agent requests, open items |

## Elsewhere in the repository

| Document | Audience |
|-|-|
| [../README.md](../README.md) | Setup, launcher agents, configuration, MCP registration, security notes |
| [../ROOM-QUICKSTART.md](../ROOM-QUICKSTART.md) | Using the managed workspace: start/stop, chat, board, team, repositories |
| [../COLLABORATION.md](../COLLABORATION.md) | The collaboration rules appended to every managed agent's instructions |
| [../config.toml](../config.toml) | Agents, ports, routing and runtime limits (commented) |

## Quick answers

| Question | Where |
|-|-|
| Where is a REST route defined? | `run.py` serves `/` with the session token; `app.py` for chat, settings and uploads; `room_runtime.install` for `/api/runtime/*`; `project_registry.install` for projects and repositories; `room_deploy.install` for deployments; `team_builder.py` for proposals |
| Where do agent tools live? | `mcp_bridge.py` (`chat_*`), `room_mcp.py` plus `RoomRuntime.coordination` (`room_*`) |
| What does the frontend expect from the server? | `frontend/src/lib/types.ts` and `frontend/src/workspace/projectData.ts` mirror the payloads |
| Where is state on disk? | [file-index.md](file-index.md#data-directory-serverdata_dir-default-data) |
| What can stop or replace the server? | Only `supervisor.py`; see flow 4 in [architecture.md](architecture.md) |

## Keeping these docs current

- After adding, renaming or removing a module, frontend file, test or chat script, run `python Docs/refresh_tables.py`. It rewrites only the `<!-- generated:... -->` blocks in `file-index.md` and `dependency-map.md`. Give new files a module docstring or a leading `/* ... */` header, because those headers are where the purpose column comes from.
- When a flow, record layout or design rule changes, edit the hand-written text in the matching document in the same commit.
- Store research findings as new documents in this folder, one subject each, and add a row to the Library table above.
- Write in plain text: no em dashes, tables use the `|-|` separator, and diagrams are ASCII without box-drawing characters.
