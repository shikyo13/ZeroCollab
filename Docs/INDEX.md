# ZeroCollab documentation library

Start here. Each document covers one subject; read the one that matches the question instead of the whole set. User-facing guides stay at the repository root.

## Library

| Document | Category | Read it for |
|-|-|-|
| [GLOSSARY.md](GLOSSARY.md) | Overview | One word per room concept (Stop, Resume, Quit, release, desktop shell, stage, rehearsal, install, donor, live check, normal launch) with the words to avoid |
| [project-summary.md](project-summary.md) | Overview | What ZeroCollab is, feature areas and the module that owns each, design rules, known gaps |
| [architecture.md](architecture.md) | Architecture | Process and browser layout (ASCII), and four flows: chat message, managed agent turn, repository change, deployment |
| [dependency-map.md](dependency-map.md) | Dependencies | Third-party packages and who uses them, the internal Python import graph and its cycles, frontend packages, chat script load order |
| [file-index.md](file-index.md) | Files and data | Every root file, Python module, frontend file and test with its purpose; the `data/` directory layout and which module writes each record |
| [providers.md](providers.md) | Feature design | Provider templates, sign-in and local models today (with gaps), auth options, local model plan, release build slices and open decisions |
| [repositories.md](repositories.md) | Feature design | Repositories per project as built: data model, worktrees, `key:path` scopes, the replace/add/primary/remove actions, agent requests, open items |
| [chat-file-references.md](chat-file-references.md) | Feature design | Pasting a local file path into chat as a reference instead of uploading a copy (`local_files.py`, `app.py` routes) |
| [decisions-in-chat-contract.md](decisions-in-chat-contract.md) | Feature design | Backend contract for in-chat team decisions |
| [decision-flow-model.md](decision-flow-model.md) | Feature design | Decision flow backend model v2 (`decisions.json`), a design for review |
| [../tools/motion/README.md](../tools/motion/README.md) | Motion | Current independent CLI/MCP, dependencies, artifact inputs/outputs and recording adapter |
| [../tools/motion/Docs/motion-scene-v2.md](../tools/motion/Docs/motion-scene-v2.md) | Motion reference | Scene v2 fields; historical tool names are superseded by the standalone interface |
| [research/2026-10-23-desktop-motion-extraction.md](research/2026-10-23-desktop-motion-extraction.md) | Migration evidence | Desktop-only source structure, extraction boundary, checks and limits |
| [research/agent-skills-adoption/plan.md](research/agent-skills-adoption/plan.md) | Research | Matt Pocock's agent skills mapped to the room (adopt, adapt or skip, with owners), and the first retro ([retro-2026-10-14-normal-launch.md](research/agent-skills-adoption/retro-2026-10-14-normal-launch.md)) |
| [research/harness-engineering/ranked-changes.md](research/harness-engineering/ranked-changes.md) | Research | Zero's harness-engineering research mapped to ZeroCollab: five ranked changes with evidence labels, seams and checks, plus what is already aligned |

## Elsewhere in the repository

| Document | Audience |
|-|-|
| [../README.md](../README.md) | Setup, launcher agents, releases and updates, configuration, MCP registration, security notes |
| [../ROOM-QUICKSTART.md](../ROOM-QUICKSTART.md) | Using the managed workspace: start/stop, chat, board and its enforced rules, work continuity, team profiles and skills, repositories, updates |
| [../COLLABORATION.md](../COLLABORATION.md) | The collaboration rules appended to every managed agent's instructions |
| [../config.toml](../config.toml) | Agents, ports, routing and runtime limits (commented) |

## Quick answers

| Question | Where |
|-|-|
| Where is a REST route defined? | `api/run.py` mounts the private desktop renderer without embedding its token; `api/app.py` for chat, settings and uploads; `room_runtime.install` for `/api/runtime/*`; `project_registry.install` for projects and repositories; `room_deploy.install` for deployments; `team_builder.py` for proposals |
| Where do agent tools live? | `mcp_bridge.py` (`chat_*`), `room_mcp.py` plus `RoomRuntime.coordination` (`room_*`) |
| What does the frontend expect from the server? | `apps/desktop/renderer/src/lib/types.ts` and `apps/desktop/renderer/src/workspace/projectData.ts` mirror the payloads |
| Where is state on disk? | [file-index.md](file-index.md#data-directory-serverdata_dir-default-data) |
| What can stop or replace the server? | Only `supervisor.py`; see flow 4 in [architecture.md](architecture.md) |

## Keeping these docs current

- After adding, renaming or removing a module, frontend file, test or chat script, run `python Docs/refresh_tables.py`. It rewrites only the `<!-- generated:... -->` blocks in `file-index.md` and `dependency-map.md`. Give new files a module docstring or a leading `/* ... */` header, because those headers are where the purpose column comes from.
- When a flow, record layout or design rule changes, edit the hand-written text in the matching document in the same commit.
- Store research findings as new documents in this folder, one subject each, and add a row to the Library table above.
- Write in plain text: no em dashes, tables use the `|-|` separator, and diagrams are ASCII without box-drawing characters.
