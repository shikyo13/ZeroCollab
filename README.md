# ZeroCollab

By ZeroTheAbsolute.

ZeroCollab is an Electron desktop workspace for you and your coding agents: shared chat, task board, team profiles, projects, approvals and managed native provider turns. The desktop app is the supported interactive product. The standalone browser frontend and terminal launchers are deprecated.

## Run from source

Use Python 3.11+ and Node.js 22. Install app dependencies and build tools in this checkout:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e .
npm ci --prefix apps/desktop
npm ci --prefix apps/desktop/electron
npm ci --prefix apps/desktop/renderer
npm start
```

`Start-Room.cmd` / `Start-Room.ps1` also run `npm start`. Quit from the desktop app to close its owned backend. Development uses a separate `ZeroCollab-Desktop-Dev` profile. Select a prepared room folder in the desktop setup; its data and provider homes remain separate from source/build files. See [the workspace guide](ROOM-QUICKSTART.md) for Chat, Board, Team and project workflows.

`npm start` builds the renderer, prepares an ignored resource tree from canonical source and launches Electron. It does not install an application or deploy a room. Opening the backend URL in a normal browser shows a desktop-required response; the private session token is supplied by Electron's trusted preload and is never embedded in HTML.

## Source layout

| Path | Owner |
| --- | --- |
| `src/zerocollab/api` | REST, WebSocket and room MCP entry points |
| `src/zerocollab/coordination` | Board, decisions, queues and work continuity |
| `src/zerocollab/runtime` | Agent lifecycle, profiles, host policy and supervision |
| `src/zerocollab/storage` | Persistence, files, archives and project repositories |
| `src/zerocollab/providers` | Native CLI and local/API provider adapters |
| `src/zerocollab/browser` | Project preview browsers and live capture ownership |
| `src/zerocollab/integrations` | Live feed, room markup and optional external media argv adapter |
| `src/zerocollab/releases` | Existing managed release behavior |
| `apps/desktop/electron` | Electron shell, preload and updater |
| `apps/desktop/host` | Trusted desktop host/controller adapters |
| `apps/desktop/renderer` | One maintained app UI source/build tree |
| `tools/motion` | Independent CLI/MCP scene and media tool |
| `legacy` | Deprecated launchers and historical release manifests |
| `build` | Ignored resources, packages, fixtures and captures |

The renderer still uses its existing static chat modules while their behavior is retained. They now belong to the desktop renderer; there is no second maintained browser product. Runtime data, installed applications and existing worktrees are not source and are not moved by the build.

## Optional motion tools

Install [ZeroCollab Motion](tools/motion/README.md) separately. It owns scene JSON, renderer/assets, previews, video/audio export, inspection, media job lifecycle and recording encoding. Both its CLI and stdio MCP adapter work without this application. The app has no embedded Motion Studio, media queue or bundled encoder. Ordinary app animation and video attachment playback remain app behavior.

Browser recording retains live capture with the browser owner, then invokes the external CLI. Configure `[tools.motion] command = [...]` or install `zerocollab-motion` on PATH. Missing media dependencies produce a recording/tool error and do not prevent app startup. See the tool README for encoder, browser, optional voice dependencies and examples.

## Build and validation

`npm run build` typechecks/builds the desktop renderer. `npm run desktop:prepare` copies source into `build/desktop/resources`; `npm run desktop:package` builds an installer with publishing disabled. A distributable build requires `ZEROCOLLAB_PYTHON_HOME` pointing at a validated Python runtime with the app dependencies. Source development uses the checkout's `.venv`. Packaging and deploying are separate operations.

Existing app tests live in `tests/`; independent media tests live in `tools/motion/tests/`. Run them with the owning package installed. Native media checks opt in through `ZEROCOLLAB_TEST_FFMPEG`. See [the implementation evidence](Docs/research/2026-10-23-desktop-motion-extraction.md) for observed checks and baseline failures.

[Documentation index](Docs/INDEX.md) · [Architecture](Docs/architecture.md) · [Collaboration rules](COLLABORATION.md)
