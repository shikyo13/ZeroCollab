# Desktop source cleanup and standalone motion extraction

Implemented on local branch `refactor/desktop-standalone-motion`, from `68bde70316edb256dae039e34e8469b490704f3f`. The owner authorized implementation, chose Electron as the maintained application, and explicitly excluded deployment. This supersedes the planning-only status in the [attached-review assessment](2026-10-23-attached-review-validation.md).

## Result

- App Python lives under `src/zerocollab/`, grouped by API, coordination, runtime, storage, providers, browser, integrations and releases. Imports, entry points, tests and provider MCP paths follow that source.
- Electron shell, trusted host adapters and renderer live under `apps/desktop/`. `scripts/prepare_desktop.py` builds ignored resources from canonical source. Generated backend copies and installer output are build products, not source owners.
- `npm start` / `Start-Room` start Electron. Normal browser root access is refused; trusted preload supplies API access and the private root-navigation header is injected only for the main frame. HTML contains no session token. Development has its own app profile.
- Deprecated terminal launchers and old donor manifests live under `legacy/`. Existing React and static chat modules remain together inside the desktop renderer, preserving behavior without a second browser product. This change does not rewrite the remaining static chat implementation.
- `tools/motion` is an independently installable wheel with CLI and stdio MCP over one engine. It owns scene validation/edit/save, HTML/PNG previews, renderer/assets, export/audio/voice helpers, inspection, queue/cancellation and recording encoding. The app's embedded Motion page/routes/tool and media gate were removed. Ordinary app animation and generic media playback remain.
- Browser capture retains its browser/project ownership and input privacy. An optional external argv adapter probes/invokes the motion CLI; encoding runs in its owned process. Capture then commits the final clip and sidecar. Cancel, page close, project change and shutdown join owned work and discard incomplete artifacts. App startup does not import/probe motion or require an encoder.

## Observed checks

All checks used this checkout's development dependencies and temporary fixture data, with no model/provider turns. Raw evidence stays on PC under `D:\Dev\Projects\ZeroCollab\ZeroCollab\build\`.

| Check | Observed result | Evidence |
| --- | --- | --- |
| Desktop renderer | TypeScript and Vite build pass, 161 modules | `npm run build`; compiled assets under `apps/desktop/renderer/static/modern` |
| Native Electron lifecycle | Fixture room rendered; parent owns child/job and writer; approvals false; close acknowledgement, child exit 0 and graceful shutdown | `build/desktop-smoke/appdata/ZeroCollab-Desktop-Dev/session.json`, `workspace.png` |
| Desktop access boundary | Public HTML contains no token; unauthenticated API refused; core configures/installs with all motion-package imports forbidden; root requires desktop header | `tests/test_desktop_boundary.py`, `build/boundary-tests.log`; native session `security_checks` |
| App regression selection | 190 tests passed across persistence/files, team/continuity, release contracts/checkpoints, host handover, focus, providers/profiles/skills, settings/sessions, CLI homes, RPC, process ownership and uploads | `build/app-verified.log` |
| Media tests | 28 passed, including native Opus/PCM checks with an explicit encoder, queue teardown/cancellation, preview isolation and concurrent asset registration | `build/motion-tests.log`, `tools/motion/tests/` |
| Capture/tool process boundary | Four recording-boundary tests and the desktop-boundary test passed; actual JPEG spool encoded externally, committed clip/sidecar, temporary spool/request files removed; cancellation before start/during probe joined | `tests/test_motion_boundary.py`, `build/boundary-tests.log` |
| Tool without app | Wheel installed in `build/tool-only`; app package absent; self-contained HTML, PNG and 3-frame WebM generated; inspection measured VP9, 320x180, 30 fps, 0.1 seconds | `build/tool-only-smoke.json`, `build/tool-only-output/` |
| Artifact handoff | Independently produced video registered/resolved through the app's existing file registry, with matching original path and size, without copying | `build/artifact-handoff/receipt.json`; inline-player interaction was not tested |
| MCP | Real stdio handshake and 14 tool schemas; invalid scene refused; actual render cancelled with its integer generation; next render completed and returned a real PNG | `build/mcp-jobs.log`; initial validation in `build/check_mcp.py` |
| Renderer source ownership | Tool core rebuild passes; optional product snapshot adapter rebuilds from an explicit app checkout | `npm --prefix tools/motion run build:core`; `build:product-adapter -- <checkout>` |
| Source consistency | Python compilation and staged whitespace checks pass | `compileall`; `git diff --cached --check` |

The explicit FFmpeg used for native fixtures was read from the existing installation; no installed bytes were changed. The motion package discovers its own dependencies. Restricted MCP environments can pass an explicit `--browser`, which was required for the real stdio render check.

## Existing failures and limits

The broader suite is not claimed green. At the exact pre-migration commit with the same dependencies, `test_manager_wakes` reproduces five failures and two errors; the Claude protocol group in `test_project_manager` reproduces its hang; `test_run_speech` reproduces its `FakeRun.first_output_at` fixture error. Logs are retained in `build/test_manager_wakes-baseline.log`, `build/test_project_manager-baseline.log` and `build/test_run_speech-baseline.log`. The relevant seven project-manager setup tests pass. These unrelated baseline defects were not changed.

The native screenshot uses an empty disposable room, not the owner's active project or an installed-feature acceptance run. No provider execution, real voice-model synthesis, macOS/Linux desktop launch, distributable installer build, live migration or live recording is claimed. Optional voice workers/models must be provisioned explicitly. Installer creation requires a validated distributable Python runtime; publishing is disabled in the package command.

No live app was launched, restarted, installed or deployed; no existing room configuration, credentials, runtime data or worktree was moved or deleted. The separate research hub records the owner decision and implementation evidence; it does not install this branch.
