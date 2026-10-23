# Windows app validation and README update — 2026-10-23

The desktop development/QA workflow, clean source snapshot, independent media tests and isolated Windows installer checks passed after the fixes below. This is bounded Windows validation, not acceptance of every optional engine, platform or production deployment.

The source started at `607d73063f889a1b39e0486c24f5b0ebf9b058c9` and includes the fixes committed with this record. The README now leads with autonomous team development and QA: planning, ownership, implementation, testing, browser QA, exact-revision review, rework, automatic continuation and human control. Electron remains the maintained application; motion remains an independent CLI/MCP package.

## Defects found and fixed

- The unanchored `providers/` ignore pattern excluded **all 11 files in the relocated provider source package**. Local tests could see them; a fresh checkout could not. The rule now excludes only root vendor downloads (`/providers/`), and the provider sources are tracked. A clean index export and wheel contents check verify their inclusion.
- The desktop host starts the base Python interpreter and adds its dependency directory. A child MCP server did not inherit that directory. The first real Codex turn failed its required MCP handshake because the isolated profile had no global `mcp` package. Native room servers now explicitly bootstrap the host dependency directory in isolated Python. The script remains a separate argv entry so interrupted Antigravity server configurations are recognized and replaced. Real initialization and interrupted-config tests cover both paths.
- The optional motion module fallback had the same base-interpreter dependency problem. It now starts the separately installed distribution with its dependency directory explicitly loaded. The fallback is tested using the isolated base interpreter.
- Existing tests needed current runtime collaborators, prompt replay acknowledgements, the human PM configuration schema, telemetry fields and qualified logger names. The Claude protocol fixture now has a finite test timeout. Assertions for ownership, budgets, restart behavior, restricted audit permissions, history, review hashes and Stop remain in place.

No deployment gates or production permissions were loosened to get passing results.

## Executed checks

| Check | Observed result |
| --- | --- |
| Full app suite in a clean Git-index source export | **663 tests: 659 passed, 4 Unix-only tests skipped**, no failures. The snapshot contains the provider files and does not rely on ignored local source. |
| Independent motion suite with explicit FFmpeg | **28 passed**, including native audio and recording encoding. |
| Clean source renderer | TypeScript checking and Vite production build passed, 161 modules. The existing large-chunk warning remains. |
| App wheel | Built from the clean source export; all 11 provider modules included. |
| Dependencies and syntax | `pip check`, Python compile checks and Git diff checks passed. |
| Desktop boundary | Core startup with motion imports forbidden; public root/API refusal; no HTML session token; room MCP initialization in isolated base Python; interrupted Antigravity config replacement. |
| Native desktop UI | Created and switched projects, attached a disposable repository, created separate agent worktrees, saved PM settings, used Chat/Board/Team/Live and confirmed Stop. Board inspected at 1280×800 and 1920×1080. |
| Packaged recording and playback | A native agent recorded its actual browser through the external tool: **60 frames, 2 seconds, 30 fps, 1280×800**. Only `clip.webm` and its sidecar were published. The Chat video decoded and advanced beyond 0.1 seconds without a media error. |
| Windows package | NSIS installer built with `--publish never`; no encoder bundled into the app. |
| Isolated installation/replacement | Installer exited 0 in a separate Hyper-V clone. The fixture room database hash was preserved across replacement. Packaged startup rendered the workspace with an owned writer/process job; shutdown acknowledged and child exited 0. Public HTML/token and anonymous API checks passed. The final packaged room MCP handshake also passed inside the VM. |

The original test VM's saved CPU state could not resume on this host. A separate differencing clone used a copy of its saved disk. The original VM and saved state were preserved. Installer operations took place only in the clone; the installed host application and real room were not updated.

Installer: `build/desktop/dist/ZeroCollab-Setup-0.1.6.exe` (desktop 0.1.6, bundled application 0.12.86).

SHA-256: `861dd07eb03e67f4df4ca77c21fe2d2affaaecd4e4be4f15ccd05cb1df3fcb49`.

## Real three-provider development and QA cycle

This was an executed application workflow using signed-in native providers, rather than scripted provider responses:

1. Sent a bounded repair request through the desktop composer to the Codex builder, in a disposable repository containing a counter that displayed 0 on its first click.
2. The builder read the room through MCP, created and claimed task `9c625528`, reserved `index.html`, reproduced the bug, changed `n++` to `++n`, checked ten handler invocations and drove the project browser. It attached before/after screenshots and annotations.
3. It requested review `689cf0ad` against SHA-256 `cef32d5cf6e696d990cfddc6a3d160dcd5f68acbb9b4246e50f2914685c45340`, then created tracked reviewer and QA follow-ups.
4. Claude inspected the captured file and its live hash, approved that revision and kept completion waiting on independent QA. Its minor spacing note became a tracked follow-up and was explicitly deferred outside the repair scope.
5. Gemini through Antigravity independently reloaded the served page, observed 0 then 1, 2 and 3, matched the source hash and attached screenshot/markup evidence. It resolved QA follow-up `bc866dd1`.
6. Results woke the responsible owners. The builder closed the repair after review and QA; the PM then closed coordination task `c5fd2400`. All three development follow-ups were resolved. No additional user instruction was sent between the repair request and those handoffs/completions.

Ordinary PM coordination and follow-up/dependency wakes were observed. Automatic 15-minute check-ins were configured through Team, but the full interval was not waited out; scheduling, finite budgets, restart and Stop behavior have test coverage. Formal multi-stage consensus and team-design workflows have source/test coverage; a new live multi-agent consensus/team-design session was not run here.

## Evidence and limits

Raw artifacts are ignored local build outputs on PC at `D:\Dev\Projects\ZeroCollab\ZeroCollab\build\full-validation`:

- `snapshot-tests-final.log`, `snapshot-renderer.log`, `motion-final.log`, `package-final.log`, `app-wheel.log`, `manifest.json`.
- `desktop/loop-completed.json`, the disposable room's runtime events, and `desktop/board-1280.png`, `board-1920.png`, `board-done.png`, `chat.png`, `team.png`, `live.png`.
- `desktop/playback.json`, `desktop/playback.png`, and the completed recording under the fixture project's `recordings/43727f58515b/`.
- `vm/session-final.json`, `vm/workspace-final.png`; installer, database-preservation and final packaged-MCP receipts in the tool output.

Four Unix transport tests require another OS. No local inference server responded on loopback ports 8080, 11434 or 1234, so local-provider adapters have test coverage but no real local-model generation in this run. MLX/Apple Silicon, optional voice synthesis, public updater-feed download/install and real-room owner acceptance were not executed. Fixture installation/replacement does not establish migration of every historical live room. No update feed or release was published and no host application was deployed.

Prior standalone wheel/CLI/MCP scene rendering, inspection and cancellation evidence is retained in [the extraction record](2026-10-23-desktop-motion-extraction.md). Those unchanged tool paths were reused; earlier selected app tests were not treated as complete app validation.
