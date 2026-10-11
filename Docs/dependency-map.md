# Dependency map

What depends on what: third-party packages, the internal Python import graph, the frontend's dependencies and the order the chat client's scripts load. The import tables are generated from the source with `ast` (top-level and function-level imports both count).

## Third-party Python packages

`requirements.txt` pins the ranges: `fastapi>=0.110`, `uvicorn[standard]>=0.29`, `mcp>=1.0,<2.0` (v2 removes `mcp.server.fastmcp`), `httpx>=0.27`, `jsonschema>=4.23`, `pillow>=10.1` (`room_markup` uses `load_default(size=)`). `starlette` arrives with FastAPI.

<!-- generated:third-party -->
| Package | Used by |
|-|-|
| `PIL` | room_markup |
| `fastapi` | app, coordination_records, decision_flow, project_registry, provider_relay, provider_setup, provider_store, room_deploy, room_runtime, run, shared_browser, team_builder |
| `httpx` | provider_controls, provider_keys, provider_relay, provider_setup, provider_store, room_mcp, room_runtime, shared_browser, supervisor |
| `jsonschema` | local_protocol, native_adapters |
| `mcp` | mcp_bridge, room_mcp |
| `starlette` | app |
| `uvicorn` | run |
| `websockets` | shared_browser |
<!-- /generated -->

## Internal Python imports

Import cycles exist and are broken by importing inside functions rather than at module top:

- `project_registry` and `project_room` import each other.
- `project_registry` and `room_runtime` import each other (`room_runtime.git` is used for repository checks).
- `coordination_records` imports `project_registry`, which imports `project_room`, which imports `coordination_records`.

Change these modules with the cycle in mind: a new top-level import between them can fail at startup.

### Who each module imports

<!-- generated:python-imports -->
| Module | Imports (internal) | Third-party |
|-|-|-|
| `agent_profiles` | project_room, provider_controls, provider_store | - |
| `agents` | mcp_bridge, queue_file | - |
| `app` | agents, archive, durable, file_preview, mcp_bridge, project_registry, registry, router, store | fastapi, starlette |
| `archive` | - | - |
| `build_release` | - | - |
| `config_loader` | - | - |
| `coordination_queue` | - | - |
| `coordination_records` | project_registry | fastapi |
| `coordination_state` | - | - |
| `decision_flow` | project_registry, project_room, team_builder | fastapi |
| `durable` | - | - |
| `file_preview` | git_preview, room_runtime | - |
| `git_preview` | - | - |
| `local_protocol` | - | jsonschema |
| `login_gemini` | native_adapters | - |
| `mcp_bridge` | durable | mcp |
| `mcp_proxy` | - | - |
| `native_adapters` | - | jsonschema |
| `project_registry` | project_room, room_runtime | fastapi |
| `project_room` | coordination_queue, coordination_records, coordination_state, durable, project_registry, room_markup | - |
| `provider_controls` | durable, native_adapters, provider_keys, provider_store | httpx |
| `provider_keys` | provider_prices | httpx |
| `provider_prices` | - | - |
| `provider_relay` | provider_keys | fastapi, httpx |
| `provider_setup` | local_protocol, native_adapters, provider_keys, provider_relay, provider_store, room_runtime | fastapi, httpx |
| `provider_store` | project_room, provider_keys, provider_prices, provider_setup, room_runtime | fastapi, httpx |
| `queue_file` | - | - |
| `ready_wakes` | - | - |
| `registry` | durable | - |
| `release_pack` | - | - |
| `room_deploy` | durable, mcp_bridge | fastapi |
| `room_goals` | coordination_records, durable | - |
| `room_markup` | - | PIL |
| `room_mcp` | - | httpx, mcp |
| `room_runtime` | agent_profiles, coordination_queue, coordination_records, coordination_state, decision_flow, durable, git_preview, local_protocol, mcp_bridge, native_adapters, project_registry, project_room, provider_controls, provider_keys, provider_setup, provider_store, queue_file, ready_wakes, room_deploy, room_goals, shared_browser, steering_delivery, team_builder | fastapi, httpx |
| `router` | - | - |
| `run` | app, config_loader, mcp_bridge, room_runtime | fastapi, uvicorn |
| `shared_browser` | - | fastapi, httpx, websockets |
| `steering_delivery` | coordination_queue | - |
| `store` | durable | - |
| `supervisor` | release_pack | httpx |
| `team_builder` | project_registry, project_room | fastapi |
| `wrapper` | config_loader, mcp_proxy, queue_file, wrapper_unix, wrapper_windows | - |
| `wrapper_api` | config_loader, queue_file, wrapper | - |
| `wrapper_unix` | - | - |
| `wrapper_windows` | - | - |
<!-- /generated -->

### Who imports each module

<!-- generated:python-importers -->
| Module | Imported by |
|-|-|
| `agent_profiles` | room_runtime |
| `agents` | app |
| `app` | run |
| `archive` | app |
| `config_loader` | run, wrapper, wrapper_api |
| `coordination_queue` | project_room, room_runtime, steering_delivery |
| `coordination_records` | project_room, room_goals, room_runtime |
| `coordination_state` | project_room, room_runtime |
| `decision_flow` | room_runtime |
| `durable` | app, mcp_bridge, project_room, provider_controls, registry, room_deploy, room_goals, room_runtime, store |
| `file_preview` | app |
| `git_preview` | file_preview, room_runtime |
| `local_protocol` | provider_setup, room_runtime |
| `mcp_bridge` | agents, app, room_deploy, room_runtime, run |
| `mcp_proxy` | wrapper |
| `native_adapters` | login_gemini, provider_controls, provider_setup, room_runtime |
| `project_registry` | app, coordination_records, decision_flow, project_room, room_runtime, team_builder |
| `project_room` | agent_profiles, decision_flow, project_registry, provider_store, room_runtime, team_builder |
| `provider_controls` | agent_profiles, room_runtime |
| `provider_keys` | provider_controls, provider_relay, provider_setup, provider_store, room_runtime |
| `provider_prices` | provider_keys, provider_store |
| `provider_relay` | provider_setup |
| `provider_setup` | provider_store, room_runtime |
| `provider_store` | agent_profiles, provider_controls, provider_setup, room_runtime |
| `queue_file` | agents, room_runtime, wrapper, wrapper_api |
| `ready_wakes` | room_runtime |
| `registry` | app |
| `release_pack` | supervisor |
| `room_deploy` | room_runtime |
| `room_goals` | room_runtime |
| `room_markup` | project_room |
| `room_runtime` | file_preview, project_registry, provider_setup, provider_store, run |
| `router` | app |
| `shared_browser` | room_runtime |
| `steering_delivery` | room_runtime |
| `store` | app |
| `team_builder` | decision_flow, room_runtime |
| `wrapper` | wrapper_api |
| `wrapper_unix` | wrapper |
| `wrapper_windows` | wrapper |
<!-- /generated -->

## Frontend (`frontend/`)

| Dependency | Version range | Role |
|-|-|-|
| `react`, `react-dom` | `^19.2.0` | Workspace views |
| `typescript` | `~5.9.3` | Type check (`tsc --noEmit` runs before every build) |
| `vite` | `^7.3.1` | Bundler; `npm run build` writes `static/modern/workspace.js` and `workspace.css` |
| `@types/react`, `@types/react-dom` | `^19.2.0` | Types |

The workspace has no client router or state library. Its seams with the rest of the app:

- `lib/api.ts` calls the FastAPI routes with the page's session token.
- `lib/room.ts` bridges to the chat client: `static/chat.js` owns the single `/ws` connection and re-emits frames that React views subscribe to with `useRoomEvent`.
- `lib/types.ts` mirrors server payloads from `room_runtime.py`, `project_room.py` and `coordination_records.py`; it is a copy of the contract, not a new API, so change both sides together.

## Chat client script order (`static/index.html`)

Classic scripts sharing globals, so order matters. `marked` 15.0.12 is vendored at `static/vendor/marked.min.js`, byte-identical to the npm package file, with its upstream license at `static/vendor/marked.LICENSE.md`. `.gitattributes` marks `static/vendor/**` as `-text`, so releases (which ship checkout bytes) never convert their line endings. `chat.js` `init()` calls `marked.setOptions` first, so the page must not depend on a CDN being reachable. To upgrade, replace the file from the npm tarball and bump the `?v=` tag.

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
