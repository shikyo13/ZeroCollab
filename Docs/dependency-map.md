# Dependency map

What depends on what: third-party packages, the internal Python import graph, the frontend's dependencies and the order the chat client's scripts load. The import tables are generated from the source with `ast` (top-level and function-level imports both count).

## Third-party Python packages

`requirements.txt` pins the ranges: `fastapi>=0.110`, `uvicorn[standard]>=0.29`, `mcp>=1.0,<2.0` (v2 removes `mcp.server.fastmcp`), `httpx>=0.27`, `jsonschema>=4.23`, `pillow>=10.1` (`room_markup` uses `load_default(size=)`). `starlette` arrives with FastAPI.

<!-- generated:third-party -->
| Package | Used by |
|-|-|
| `PIL` | browser_recording_transport, motion_agent, motion_encode, motion_studio, room_markup |
| `fastapi` | app, coordination_records, decision_flow, live_feed, manager_wakes, motion_studio, project_registry, provider_relay, provider_setup, provider_store, room_deploy, room_runtime, run, shared_browser, team_builder |
| `httpx` | motion_render, motion_studio, provider_controls, provider_keys, provider_relay, provider_setup, provider_store, release_rehearse, room_mcp, room_runtime, shared_browser, supervisor |
| `jsonschema` | local_protocol, native_adapters |
| `kokoro_onnx` | motion_audio_voice |
| `mcp` | mcp_bridge, room_mcp |
| `numpy` | motion_audio_mix, motion_audio_voice |
| `onnxruntime` | motion_audio_voice |
| `piper` | motion_audio_voice |
| `starlette` | app |
| `uvicorn` | run |
| `websockets` | motion_render, motion_studio, shared_browser |
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
| `agent_profiles` | agent_skills, project_room, provider_controls, provider_store | - |
| `agent_skills` | - | - |
| `agents` | mcp_bridge, queue_file | - |
| `app` | agents, archive, durable, file_preview, local_files, mcp_bridge, project_registry, registry, router, store, ui_composing, ui_sessions | fastapi, starlette |
| `archive` | - | - |
| `browser_recording` | browser_recording_transport, shared_browser | - |
| `browser_recording_coordinator` | browser_recording, motion_studio | - |
| `browser_recording_transport` | motion_encode | PIL |
| `build_release` | - | - |
| `cli_homes` | durable | - |
| `config_loader` | - | - |
| `coordination_queue` | - | - |
| `coordination_records` | project_registry, project_room | fastapi |
| `coordination_state` | - | - |
| `decision_flow` | project_registry, project_room, team_builder | fastapi |
| `durable` | - | - |
| `file_preview` | git_preview, room_runtime | - |
| `git_preview` | - | - |
| `host_handover` | durable, release_maintenance | - |
| `live_feed` | - | fastapi |
| `local_files` | durable | - |
| `local_protocol` | - | jsonschema |
| `login_gemini` | native_adapters | - |
| `manager_wakes` | coordination_state, project_registry | fastapi |
| `mcp_bridge` | durable | mcp |
| `mcp_proxy` | - | - |
| `motion_agent` | motion_jobs, motion_scene, motion_studio, team_builder, video_analysis | PIL |
| `motion_audio` | motion_audio_cache, motion_audio_media, motion_audio_mix, motion_audio_process, motion_render | - |
| `motion_audio_cache` | motion_audio_process, motion_render | - |
| `motion_audio_media` | motion_audio_mix, motion_audio_process, motion_encode, motion_render | - |
| `motion_audio_mix` | - | numpy |
| `motion_audio_process` | shared_browser | - |
| `motion_audio_voice` | - | kokoro_onnx, numpy, onnxruntime, piper |
| `motion_encode` | - | PIL |
| `motion_jobs` | - | - |
| `motion_render` | motion_studio, shared_browser | httpx, websockets |
| `motion_scene` | motion_studio | - |
| `motion_studio` | motion_agent, motion_audio, motion_audio_process, motion_encode, motion_jobs, motion_render, motion_scene, motion_studio_jobs, shared_browser | PIL, fastapi, httpx, websockets |
| `motion_studio_jobs` | motion_jobs | - |
| `native_adapters` | agent_skills, provider_keys | jsonschema |
| `process_enforcement` | project_room | - |
| `profile_bell` | agent_profiles, process_enforcement | - |
| `project_registry` | project_room, room_runtime | fastapi |
| `project_room` | coordination_queue, coordination_records, coordination_state, durable, local_files, manager_wakes, project_registry, room_markup | - |
| `provider_controls` | durable, native_adapters, provider_keys, provider_store | httpx |
| `provider_keys` | provider_prices | httpx |
| `provider_prices` | - | - |
| `provider_relay` | provider_keys | fastapi, httpx |
| `provider_setup` | local_protocol, native_adapters, provider_keys, provider_relay, provider_store, room_runtime | fastapi, httpx |
| `provider_store` | project_room, provider_keys, provider_prices, provider_setup, room_runtime | fastapi, httpx |
| `queue_file` | - | - |
| `ready_wakes` | - | - |
| `registry` | durable | - |
| `release_agent` | release_pack | - |
| `release_completion` | release_manager | - |
| `release_control` | durable | - |
| `release_interface` | - | - |
| `release_maintenance` | room_deploy | - |
| `release_manager` | release_completion, release_interface, release_pack, release_rehearse, release_request, release_stage | - |
| `release_pack` | - | - |
| `release_rehearse` | release_agent, release_interface, release_pack, release_stage, room_deploy | httpx |
| `release_request` | release_agent, release_interface, release_manager, release_pack, release_rehearse, release_stage, room_deploy | - |
| `release_stage` | project_registry, release_agent, release_pack, room_deploy, shared_browser | - |
| `response_claims` | - | - |
| `room_deploy` | durable, mcp_bridge, release_control, release_maintenance, release_manager | fastapi |
| `room_goals` | coordination_records, durable | - |
| `room_markup` | - | PIL |
| `room_mcp` | - | httpx, mcp |
| `room_runtime` | agent_profiles, agent_skills, coordination_queue, coordination_records, coordination_state, decision_flow, durable, git_preview, host_handover, live_feed, local_protocol, manager_wakes, mcp_bridge, motion_agent, motion_jobs, motion_studio, native_adapters, process_enforcement, profile_bell, project_registry, project_room, provider_controls, provider_keys, provider_setup, provider_store, queue_file, ready_wakes, release_control, release_interface, response_claims, room_deploy, room_goals, shared_browser, steering_delivery, team_builder, work_continuity | fastapi, httpx |
| `room_writer` | - | - |
| `router` | - | - |
| `run` | app, cli_homes, config_loader, host_handover, mcp_bridge, room_runtime, room_writer, ui_sessions | fastapi, uvicorn |
| `shared_browser` | browser_recording, browser_recording_coordinator | fastapi, httpx, websockets |
| `steering_delivery` | coordination_queue | - |
| `store` | durable | - |
| `supervisor` | release_pack, room_writer | httpx |
| `team_builder` | project_registry, project_room | fastapi |
| `ui_composing` | - | - |
| `ui_sessions` | - | - |
| `video_analysis` | - | - |
| `work_continuity` | process_enforcement | - |
| `wrapper` | config_loader, mcp_proxy, queue_file, wrapper_unix, wrapper_windows | - |
| `wrapper_api` | config_loader, queue_file, wrapper | - |
| `wrapper_unix` | - | - |
| `wrapper_windows` | - | - |
<!-- /generated -->

### Who imports each module

<!-- generated:python-importers -->
| Module | Imported by |
|-|-|
| `agent_profiles` | profile_bell, room_runtime |
| `agent_skills` | agent_profiles, native_adapters, room_runtime |
| `agents` | app |
| `app` | run |
| `archive` | app |
| `browser_recording` | browser_recording_coordinator, shared_browser |
| `browser_recording_coordinator` | shared_browser |
| `browser_recording_transport` | browser_recording |
| `cli_homes` | run |
| `config_loader` | run, wrapper, wrapper_api |
| `coordination_queue` | project_room, room_runtime, steering_delivery |
| `coordination_records` | project_room, room_goals, room_runtime |
| `coordination_state` | manager_wakes, project_room, room_runtime |
| `decision_flow` | room_runtime |
| `durable` | app, cli_homes, host_handover, local_files, mcp_bridge, project_room, provider_controls, registry, release_control, room_deploy, room_goals, room_runtime, store |
| `file_preview` | app |
| `git_preview` | file_preview, room_runtime |
| `host_handover` | room_runtime, run |
| `live_feed` | room_runtime |
| `local_files` | app, project_room |
| `local_protocol` | provider_setup, room_runtime |
| `manager_wakes` | project_room, room_runtime |
| `mcp_bridge` | agents, app, room_deploy, room_runtime, run |
| `mcp_proxy` | wrapper |
| `motion_agent` | motion_studio, room_runtime |
| `motion_audio` | motion_studio |
| `motion_audio_cache` | motion_audio |
| `motion_audio_media` | motion_audio |
| `motion_audio_mix` | motion_audio, motion_audio_media |
| `motion_audio_process` | motion_audio, motion_audio_cache, motion_audio_media, motion_studio |
| `motion_encode` | browser_recording_transport, motion_audio_media, motion_studio |
| `motion_jobs` | motion_agent, motion_studio, motion_studio_jobs, room_runtime |
| `motion_render` | motion_audio, motion_audio_cache, motion_audio_media, motion_studio |
| `motion_scene` | motion_agent, motion_studio |
| `motion_studio` | browser_recording_coordinator, motion_agent, motion_render, motion_scene, room_runtime |
| `motion_studio_jobs` | motion_studio |
| `native_adapters` | login_gemini, provider_controls, provider_setup, room_runtime |
| `process_enforcement` | profile_bell, room_runtime, work_continuity |
| `profile_bell` | room_runtime |
| `project_registry` | app, coordination_records, decision_flow, manager_wakes, project_room, release_stage, room_runtime, team_builder |
| `project_room` | agent_profiles, coordination_records, decision_flow, process_enforcement, project_registry, provider_store, room_runtime, team_builder |
| `provider_controls` | agent_profiles, room_runtime |
| `provider_keys` | native_adapters, provider_controls, provider_relay, provider_setup, provider_store, room_runtime |
| `provider_prices` | provider_keys, provider_store |
| `provider_relay` | provider_setup |
| `provider_setup` | provider_store, room_runtime |
| `provider_store` | agent_profiles, provider_controls, provider_setup, room_runtime |
| `queue_file` | agents, room_runtime, wrapper, wrapper_api |
| `ready_wakes` | room_runtime |
| `registry` | app |
| `release_agent` | release_rehearse, release_request, release_stage |
| `release_completion` | release_manager |
| `release_control` | room_deploy, room_runtime |
| `release_interface` | release_manager, release_rehearse, release_request, room_runtime |
| `release_maintenance` | host_handover, room_deploy |
| `release_manager` | release_completion, release_request, room_deploy |
| `release_pack` | release_agent, release_manager, release_rehearse, release_request, release_stage, supervisor |
| `release_rehearse` | release_manager, release_request |
| `release_request` | release_manager |
| `release_stage` | release_manager, release_rehearse, release_request |
| `response_claims` | room_runtime |
| `room_deploy` | release_maintenance, release_rehearse, release_request, release_stage, room_runtime |
| `room_goals` | room_runtime |
| `room_markup` | project_room |
| `room_runtime` | file_preview, project_registry, provider_setup, provider_store, run |
| `room_writer` | run, supervisor |
| `router` | app |
| `shared_browser` | browser_recording, motion_audio_process, motion_render, motion_studio, release_stage, room_runtime |
| `steering_delivery` | room_runtime |
| `store` | app |
| `team_builder` | decision_flow, motion_agent, room_runtime |
| `ui_composing` | app |
| `ui_sessions` | app, run |
| `video_analysis` | motion_agent |
| `work_continuity` | room_runtime |
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
