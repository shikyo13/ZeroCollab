# ZeroCollab

By ZeroTheAbsolute.

**ZeroCollab is a local-first, open-source desktop platform for autonomous software development by teams of AI agents.** Give a persistent engineering team an objective: its members can plan the work, own tasks, implement changes, run tests, inspect the application in a browser, review each other's work, address findings and continue toward the done criteria. You direct the project and control its permissions, priorities and stopping points.

The full autonomous development and QA loop is the core of ZeroCollab. Shared chat, the Kanban board, specialist profiles and live views are the workspace through which that team operates.

## The development and QA loop

1. **Plan and decide.** A project manager coordinates the backlog and the team. Specialists can submit independent positions, critique alternatives, develop a proposal and record explicit agreement or dissent. Accepted decisions can become tasks for the manager to assign.
2. **Assign real ownership.** Kanban tasks carry one owner, reserved file scopes, dependencies, priorities, next actions and blockers. Delegation keeps the original owner responsible until the recipient accepts. Conflicting reservations and cyclic dependencies are rejected; agents work on one active task at a time.
3. **Implement in parallel.** Native coding agents use their actual development tools in separate Git worktrees. Projects can span multiple repositories, with each agent's checkouts and task reservations tied to the right repository.
4. **Test and inspect the result.** Agents can build and test their changes, drive a project browser, inspect pages, click through workflows and capture screenshots. Annotations, task attachments and optional browser recordings make QA evidence visible to the developer and reviewers.
5. **Review, revise and retest.** Structured peer reviews retain the exact reviewed file bytes and hashes. Editing those files makes an approval stale. Findings and follow-ups return work to the responsible owner; unfinished dependencies and required reviews prevent agent completion.
6. **Continue toward completion.** Progress, resolved dependencies, satisfied waits, pending reviews and idle owners can trigger targeted wakes. Shared goals support bounded continuation. Automatic PM check-ins can identify idle work, propose up to two scoped tasks and wake their owners within a finite shared budget. Stalled work and failed delivery surface for attention.

This cycle can move between agents without another user prompt for every handoff. The developer can intervene at any point: steer a running turn, change priorities, answer permission requests, pause a teammate or stop the whole room. Continuation obeys Stop, availability and configured budgets; unresolved work remains visible.

## An engineering team you can configure and supervise

- **Frontier and local providers.** Combine OpenAI Codex, Anthropic Claude Code and Google's Gemini through Antigravity with local connections such as Ollama, LM Studio, llama.cpp, MLX LM and mlx-serve. Mix providers and models within one project instead of rebuilding the workflow around a single vendor.
- **Persistent specialists.** Give agents their own identities, roles, model settings and project context. Approved role cores and pinned skills define responsibilities and working methods. AI-assisted team creation includes a draft, a separate challenger and a coverage review before you approve the team.
- **Operational project management.** The board, task inboxes, ownership transfers, review requests, blockers and tracked follow-ups are connected to execution and automatic wakes. The selected PM can coordinate ordinary work; optional restricted check-ins provide bounded oversight and planning.
- **Structured consensus.** Independent positions remain sealed until reveal, followed by critique, a lead proposal and explicit verdicts. Missing responses and dissent stay visible when the developer records the decision.
- **Visible execution and durable context.** Chat, Board, Team, Live and Follow views connect conversations, owned work, tool activity and browser QA. Project specifications, notes, decisions, review artifacts and session history survive individual turns. Imported CLI transcripts remain history rather than replaying old actions.
- **Controlled delivery.** The managed room/application release workflow checks review coverage, rehearsal evidence, release authority and installation receipts, with acceptance checks tracked separately. Deployment remains an explicit controlled step.

Native CLI agents supply the shell, build and test capabilities of their coding tools. Direct local/API agents use the supported room and file tools; a separate local Codex route provides a coding harness. Tool support differs by route, and MLX runtimes require Apple Silicon. Worktrees and reservations coordinate responsibility; provider permissions still determine filesystem and tool access. Code and coordination state stay on your machine; inference uses your chosen provider or local server.

The Electron desktop app is the supported interactive product. One maintained UI serves that app; the standalone web frontend and terminal launchers are deprecated. ZeroCollab is [MIT licensed](LICENSE).

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

Existing app tests live in `tests/`; independent media tests live in `tools/motion/tests/`. Run them with the owning package installed. Native media checks opt in through `ZEROCOLLAB_TEST_FFMPEG`. See [the app validation record](Docs/research/2026-10-23-full-app-validation.md) for executed checks, fixes and remaining coverage limits, and [the extraction record](Docs/research/2026-10-23-desktop-motion-extraction.md) for the repository and standalone-tool migration.

[CI](.github/workflows/ci.yml) runs on main pushes and pull requests using a fresh Windows checkout, Python 3.13 and Node.js 22. Changes limited to `README.md`, `ROOM-QUICKSTART.md` and Markdown under `Docs/` skip CI; runtime instructions, agent profiles and skills still trigger it. It runs the app tests with summary output, checks wheel contents, typechecks/builds the renderer, prepares desktop resources and exercises the existing tool-contract and isolated MCP-child checks against that prepared backend. Dependencies install into a new environment; only package-manager caches are reused. Installer packaging and live-provider acceptance remain part of release validation.

[Documentation index](Docs/INDEX.md) · [Architecture](Docs/architecture.md) · [Collaboration rules](COLLABORATION.md)
