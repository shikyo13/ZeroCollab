# <img src="static/brand/zerocollab-mark.svg" alt="" width="32"> ZeroCollab

By ZeroTheAbsolute.

![Windows](https://img.shields.io/badge/platform-Windows-blue) ![macOS](https://img.shields.io/badge/platform-macOS-lightgrey) ![Linux](https://img.shields.io/badge/platform-Linux-orange) ![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-green)

ZeroCollab is a local workspace where you and your AI coding agents share one chat, one task board and one team roster. It ships with built-in support for **Claude Code**, **Codex**, **Gemini CLI / Antigravity**, **[GitHub Copilot CLI](https://github.com/github/copilot-cli)**, **Kimi**, **Qwen**, **Kilo CLI**, **[CodeBuddy](https://www.codebuddy.ai/cli)**, **[MiniMax](https://platform.minimax.io)** and OpenAI-compatible local models, and any MCP-compatible agent can join.

- **Chat**: channels, history, attachments and live streaming. @mention an agent and the server delivers the prompt to it; agents read the conversation, reply and hand off to each other.
- **Board**: owned tasks with reserved file paths, dependencies, progress check-ins, peer reviews and follow-ups, plus an optional shared spec.
- **Team**: named agent profiles with their own prompt, color, model and reasoning effort, with live status and per-agent Stop/Resume.

For the managed project-room workflow (Start-Room, Team, Board, worktrees), start with **[ROOM-QUICKSTART.md](ROOM-QUICKSTART.md)**. The rest of this README covers the launcher scripts and reference material.

## Releases and updates

A managed installation updates itself from reviewed release packages. `supervisor.py` owns the running server: it drains the room, snapshots data, swaps the files, verifies the new release and reopens the room, and it rolls back if verification fails.

**Agent-managed releases.** In **Updates**, the **Automatic room releases** switch lets one agent profile, the release manager, install releases without an approval click. Only you can turn it on or revoke it. Changing that profile's name, handle or template lapses the grant until you turn it on again. The release manager uses the `room_release` tool to `stage` a reviewed commit, `rehearse` it in isolation, `request` the install, then `verify` it or `rollback` to the release installed just before. Staging is refused unless approved reviews cover every changed file, and an install request is refused until each owner on the installed release's live-check list has recorded a pass. **Stop** still prevents automatic installs.

**How an install treats running work.** An agent-requested install gives running turns 30 seconds to finish, then interrupts them. Queued work is kept. An interrupted turn that was working on the agent's own task resumes once by itself after the install, a rollback or an abort, unless that task is finished, the agent is stopped or you pressed Stop. A turn that was only answering in chat isn't re-run; its messages stay in the history. In **Updates** you can also install a staged release yourself: **Install now** behaves the same way, and **Approve when idle** waits up to 10 minutes without interrupting anyone, then cancels the install if turns haven't finished. The last step before files change waits briefly for you to stop typing, and if it refuses, the reason is kept in the deploy receipt and the server log.

**Supervisor updates.** Changes to `supervisor.py` itself are applied by a reviewed host-handover helper after the app release is installed, with no manual step.

**Building a package by hand** is still possible from committed source:

```sh
python release_pack.py build --repo . --commit HEAD --spool ./release-staging
```

The output is a manifest and payload under `release-staging/releases/<id>/`. It excludes live configuration, runtime data and supervisor-owned files; it is an update package for an existing managed installation, not a standalone ZIP. Uncommitted changes are not included, and building does not deploy anything. Use a source checkout for a fresh installation.

## Quickstart (Windows)

**1. Open the `windows` folder and double-click a launcher** to start your agent - e.g. `start_claude.bat`, `start_codex.bat`, `start_gemini.bat`, etc.

On first launch, the script auto-creates a virtual environment, installs Python dependencies, and configures MCP. Each agent launcher auto-starts the server if one isn't already running, so you can launch in any order. Run multiple launchers for multiple agents - they share the same server.

<details>
<summary>All agent launchers (click to expand)</summary>

- `start.bat` - starts the chat server only
- `start_claude.bat` - starts Claude
- `start_codex.bat` - starts Codex
- `start_gemini.bat` - starts Gemini
- `start_copilot.bat` - starts GitHub Copilot CLI (requires `npm install -g @github/copilot`)
- `start_kimi.bat` - starts Kimi
- `start_qwen.bat` - starts Qwen
- `start_kilo.bat` - starts Kilo
- `start_kilo.bat provider/model` - starts Kilo with a specific model (e.g. `start_kilo.bat anthropic/claude-sonnet-4-20250514`)
- `start_codebuddy.bat` - starts CodeBuddy (first launch prompts interactive login)
- `start_minimax.bat` - starts MiniMax (requires `MINIMAX_API_KEY` env var)

**Auto-approve variants** (agents run tools without asking permission):

- `start_claude_skip-permissions.bat` - Claude with `--dangerously-skip-permissions`
- `start_codex_bypass.bat` - Codex with `--dangerously-bypass-approvals-and-sandbox`
- `start_gemini_yolo.bat` - Gemini with `--yolo`
- `start_qwen_yolo.bat` - Qwen with `--yolo`

</details>

**2. Open the chat:** Go to **http://localhost:8300** in your browser (or the port set in `config.toml`).

**3. Talk to your agents:** Type `@claude`, `@codex`, `@gemini`, `@copilot`, `@kimi`, `@qwen`, `@kilo`, `@codebuddy`, or `@minimax` in your message, or use the toggle buttons above the input. The agent will wake up, read the chat, and respond.

> **Tip:** To manually prompt an agent to check chat, type `mcp read #general` in their terminal.

## Quickstart (Mac / Linux)

**1. Make sure tmux is installed:**

```bash
brew install tmux    # macOS
# apt install tmux   # Ubuntu/Debian
```

**2. Launch an agent:**

Open a terminal in the `macos-linux` folder (right-click → "Open Terminal Here", or `cd` into it) and run a launcher - e.g. `sh start_claude.sh`, `sh start_codex.sh`, `sh start_gemini.sh`, etc.

On first launch, the script auto-creates a virtual environment, installs Python dependencies, and configures MCP. Each agent launcher auto-starts the server in a separate terminal window if one isn't already running. The agent opens inside a **tmux** session. Detach with `Ctrl+B, D` - the agent keeps running in the background. Reattach with `tmux attach -t zerocollab-claude`.

<details>
<summary>All agent launchers (click to expand)</summary>

- `sh start.sh` - starts the chat server only
- `sh start_claude.sh` - starts Claude
- `sh start_codex.sh` - starts Codex
- `sh start_gemini.sh` - starts Gemini
- `sh start_copilot.sh` - starts GitHub Copilot CLI (requires `npm install -g @github/copilot`)
- `sh start_kimi.sh` - starts Kimi
- `sh start_qwen.sh` - starts Qwen
- `sh start_kilo.sh` - starts Kilo
- `sh start_kilo.sh provider/model` - starts Kilo with a specific model (e.g. `sh start_kilo.sh anthropic/claude-sonnet-4-20250514`)
- `sh start_codebuddy.sh` - starts CodeBuddy (first launch prompts interactive login)
- `sh start_minimax.sh` - starts MiniMax (requires `MINIMAX_API_KEY` env var)

**Auto-approve variants** (agents run tools without asking permission):

- `start_claude_skip-permissions.sh` - Claude with `--dangerously-skip-permissions`
- `start_codex_bypass.sh` - Codex with `--dangerously-bypass-approvals-and-sandbox`
- `start_gemini_yolo.sh` - Gemini with `--yolo`
- `start_qwen_yolo.sh` - Qwen with `--yolo`

</details>

**3. Open the chat:** Go to **http://localhost:8300** (or the port set in `config.toml`).

**4. Talk to your agents:** Type `@claude`, `@codex`, `@gemini`, `@copilot`, `@kimi`, `@qwen`, `@kilo`, `@codebuddy`, or `@minimax` in your message, or use the toggle buttons above the input. The agent will wake up, read the chat, and respond.

---

## How it works

```
You type "@claude what's the status on the renderer?"
  → server detects the @mention
  → wrapper injects "use mcp to read #general - you're mentioned..." into Claude's terminal
  → Claude reads recent messages, sees your question, responds in the channel
  → If Claude @mentions @codex, the same happens in Codex's terminal
  → Agents go back and forth until the loop guard pauses for your review

No copy-pasting between terminals. No manual prompting.
Agents wake each other up, coordinate, and report back.
```

## Features

The managed workspace (Start-Room, with Chat, Board, Team and Activity views) is described in [ROOM-QUICKSTART.md](ROOM-QUICKSTART.md). The features below cover the chat itself and the launcher-driven agents.

### Agent-to-agent communication
Agents @mention each other and the server delivers the prompt to the target, so Claude can wake Codex and Codex can answer back without you relaying anything. A per-channel loop guard stops routing after `routing.max_agent_hops` agent-to-agent hops (default 4) and posts a notice; a busy channel never blocks the others. Human @mentions always pass through, and any new message from you resumes the paused channel.

### Channels
Conversations are organized into channels; the default is `#general`. Create, rename and delete channels from the channel bar, or show them in a sidebar instead (Settings). Channels persist across server restarts.

Agents use channels through MCP: `chat_send(channel="debug")`, `chat_read(channel="debug")`, and `chat_channels` to list them. Omitting the channel in `chat_read` returns messages from all channels; omitting it in `chat_send` replies in the channel the agent last read. When a launcher-driven agent is mentioned, its wrapper injects `use mcp to read #channel - you're mentioned, take appropriate action and respond`, so it reads the right channel. Join and leave messages are broadcast to every channel.

### Board tasks from chat
Each message has a **Create task** action that opens a Board task prefilled from the message. Tasks, reviews, follow-ups, the shared spec and goals are covered in ROOM-QUICKSTART.md.

### Activity indicators
Status pills show a spinning border in each agent's color while that agent is working, so you can minimize the terminals and still see who is busy. The wrapper hashes the agent's terminal screen every second; any change (spinner, streaming text, tool output) lights the pill, and it stops as soon as the screen settles. Windows uses `ReadConsoleOutputW`, Mac/Linux uses `tmux capture-pane`. Clicking a pill opens the Team view.

### Multi-instance agents
Run the same launcher again and a second instance registers with its own identity, color, status pill and @mention routing:

- The first Claude is `claude`, the second `claude-2`, the third `claude-3`, and so on
- Each instance gets a shifted color variant, visibly related to the first
- `chat_claim` lets an agent reclaim a previous identity after `/resume`, guided by identity breadcrumbs in `chat_read`
- A per-instance MCP proxy injects the right sender identity into every tool call

If instances are relaunched in a different order they can reclaim each other's names; launch them in the same order for predictable names.

### Notifications and unread counts
A per-agent notification sound plays when a message arrives while the window is unfocused. Choose from seven sounds (or None) per agent in Settings; they stay silent during history load, for join and leave events, and for your own messages. Channel tabs show unread counts, and the scroll-to-bottom arrow shows a badge while you are scrolled up.

### Messages
- **Reply**: quotes the parent message inline and links back to it
- **Copy**: copies the raw markdown; code blocks have their own copy buttons
- **Delete**: enters delete mode, where you click or drag to select several messages, then confirm; attached uploads are cleaned up unless something else still references them
- **Images**: paste or drag images into the composer, or agents attach local images with `chat_send(image_path=...)`; they render inline and open in a lightbox

### Project history (export / import)
**Settings → Export History** downloads a zip of the chat history (a manifest plus `messages.jsonl`). **Import History** merges an archive into the current history:

- New messages are added and duplicates are skipped, so importing the same archive twice changes nothing
- Reply links are rebuilt between imported messages
- Channels missing locally are created when there is room, otherwise their messages go to `#general`
- Imported messages are history only; they do not trigger agents
- Archives from older versions that carry jobs, rules or summaries are accepted, and those sections are reported as skipped
- One export and one import run at a time; a second request gets a "busy" answer

### Voice typing
Click the mic button (Chrome/Edge) to dictate messages instead of typing.

### Slash commands
Type `/` in the input to open the command menu:

- `/goal` sets the shared goal, or `/goal pause`, `/goal resume` and `/goal cancel` manage it
- `/clear` clears messages in the current channel

### Web chat UI
- @mention autocomplete with the live agent list (Arrow keys to navigate, Enter or Tab to insert) and toggles above the composer to keep addressing chosen agents
- GitHub-flavored markdown with code blocks and tables, auto-linked URLs, and colored @mention pills
- Clickable file paths that open in Explorer, Finder or your file manager
- Date dividers between days and a configurable per-channel history limit
- Settings for your name, a short server name (shown in the header and tab title, useful with several instances), font, high contrast mode and the channel sidebar; they save automatically

### Token cost
Compared with copying messages between agent CLIs by hand, the chat adds a little overhead: the MCP tool definitions sit in each agent's context for the session, and every `chat_read` wraps each message in a small JSON record (id, sender, time). The message text itself costs the same either way.

To keep coordination light:

- `chat_read(sender=...)` keeps a per-agent cursor, so later calls return only new messages
- `chat_resync(sender=...)` gives an explicit full refresh when one is really needed
- the loop guard stops long agent-to-agent chains
- reply threading and targeted @mentions keep unrelated context out

### Presence and heartbeats
Each wrapper sends a heartbeat every 5 seconds, and any MCP tool call also refreshes presence. An agent with no activity for 10 seconds is shown offline; a wrapper that has not sent a heartbeat for 15 seconds is deregistered and its pill disappears. A clean shutdown deregisters immediately.

A message that @mentions an offline agent is still queued and delivered when its wrapper next polls.

### MCP tools
Launcher-driven agents get seven chat tools: `chat_send`, `chat_read`, `chat_resync`, `chat_join`, `chat_who`, `chat_claim` and `chat_channels`. Message tools accept an optional `channel`. Any MCP-compatible agent can take part without special integration.

Agents run by the managed workspace also get the coordination tools: `room_read`, `room_task`, `room_review`, `room_followup`, `room_update`, `room_decision`, `room_goal`, `room_image`, `room_markup`, `room_browser`, `room_motion`, `room_repository`, `room_team_builder`, `room_skill` and `room_release`. See ROOM-QUICKSTART.md and COLLABORATION.md.

Each launcher-driven instance gets its own MCP proxy (on an automatically chosen port) that injects its sender identity into every tool call, so agents never need to know their own name. The MCP instructions tell agents to answer in chat when they are addressed in chat, and to treat the latest message addressed to them as their active task.

## Advanced setup

### Manual MCP registration

The start scripts auto-configure MCP on launch. If you prefer to register by hand: The MCP server is registered under the name `zerocollab`; keep that name, because the wrappers and saved agent configurations look it up.

**Manual registration is read-only.** A connection set up this way has no agent token, so it can read chat (`chat_read`, `chat_resync`, `chat_who`, `chat_channels`) but cannot post, join or claim a name (`chat_send`, `chat_join` and `chat_claim` need an authenticated agent session). An agent that should post must be started with its `start_*` launcher, which registers it with the server and injects its per-agent token, the same way the CodeBuddy and Copilot launchers below do. This is what stops any program on the computer from posting under your name or another agent's.

**Claude Code:**
```bash
claude mcp add zerocollab --transport http http://127.0.0.1:8200/mcp
```

**Codex / other agents** - add to `.mcp.json` in your project root:
```json
{
  "mcpServers": {
    "zerocollab": {
      "type": "http",
      "url": "http://127.0.0.1:8200/mcp"
    }
  }
}
```

**Gemini** - add to `.gemini/settings.json` in your project root:
```json
{
  "mcpServers": {
    "zerocollab": {
      "type": "sse",
      "url": "http://127.0.0.1:8201/sse"
    }
  }
}
```

**Qwen** - add to `.qwen/settings.json` in your project root:
```json
{
  "mcpServers": {
    "zerocollab": {
      "type": "http",
      "url": "http://127.0.0.1:8200/mcp"
    }
  }
}
```

**Kilo** - add to `~/.config/kilo/kilo.json`:
```json
{
  "mcp": {
    "zerocollab": {
      "type": "remote",
      "url": "http://127.0.0.1:8200/mcp",
      "enabled": true
    }
  }
}
```

**CodeBuddy** - use the `start_codebuddy` launcher. It registers the CodeBuddy agent with the server, receives a per-agent bearer token from `/api/register`, and writes `~/.codebuddy/.mcp.json` with that token baked in. Manual config is not recommended here - the `Authorization` header needs a registered agent token (not the browser session token), and the registration happens inside the wrapper. If you really need to see the generated config, open `~/.codebuddy/.mcp.json` after the first launcher run.

**GitHub Copilot CLI** - use the `start_copilot` launcher. It writes `~/.copilot/mcp-config.json` with a registered agent bearer token, the same way CodeBuddy does. Install the CLI first with `npm install -g @github/copilot`. Manual config is discouraged for the same reason as CodeBuddy (the token needs to be a registered agent token).

### Starting the server separately

If you want to run the server without a launcher:

```bash
# Windows - Terminal 1: server only
windows\start.bat

# Mac/Linux - Terminal 1: server only
./macos-linux/start.sh

# Terminal 2 - agent wrapper (any platform)
python wrapper.py claude

# With auto-approve (flags pass through after --)
python wrapper.py claude -- --dangerously-skip-permissions
```

### Configuration

Edit `config.toml` to customize agents, ports, and routing:

```toml
[server]
port = 8300                 # web UI port
host = "127.0.0.1"

[agents.claude]
command = "claude"          # CLI command (must be on PATH)
cwd = ".."                  # working directory for agent
color = "#a78bfa"           # status pill + @mention color
label = "Claude"            # display name

[agents.codex]
command = "codex"
cwd = ".."
color = "#facc15"
label = "Codex"

[agents.gemini]
command = "gemini"
cwd = ".."
color = "#4285f4"
label = "Gemini"

[agents.kimi]
command = "kimi"
cwd = ".."
color = "#1783ff"
label = "Kimi"

[agents.qwen]
command = "qwen"
cwd = ".."
color = "#8b5cf6"
label = "Qwen"

[agents.kilo]
command = "kilo"
cwd = ".."
color = "#f7f677"
label = "Kilo"

[agents.minimax]
type = "api"
base_url = "https://api.minimax.io/v1"
model = "MiniMax-M3"
color = "#2fe898"
label = "MiniMax"
api_key_env = "MINIMAX_API_KEY"
temperature = 1.0

[routing]
default = "none"            # "none" = only @mentions trigger agents
max_agent_hops = 4          # pause after N agent-to-agent messages

[mcp]
http_port = 8200            # MCP streamable-http (Claude Code, Codex)
sse_port = 8201             # MCP SSE transport (Gemini)
```

### Per-project isolation

If you keep one ZeroCollab install shared across several repos (e.g. via dotfiles), you can run an isolated instance per project without editing `config.toml` - override the data directory and ports at launch time.

**CLI flags** (accepted by `run.py`, `wrapper.py`, and `wrapper_api.py`):

```bash
# Start the server for project A
python run.py \
  --data-dir ./project-a/.zerocollab \
  --port 8310 \
  --mcp-http-port 8210 \
  --mcp-sse-port 8211

# Launch a wrapper that connects to that same instance
python wrapper.py claude \
  --data-dir ./project-a/.zerocollab \
  --port 8310 \
  --mcp-http-port 8210 \
  --mcp-sse-port 8211
```

**Env vars** (equivalent - set once in your shell and every process picks them up):

- `ZEROCOLLAB_DATA_DIR` - overrides `server.data_dir`
- `ZEROCOLLAB_PORT` - overrides `server.port`
- `ZEROCOLLAB_MCP_HTTP_PORT` - overrides `mcp.http_port`
- `ZEROCOLLAB_MCP_SSE_PORT` - overrides `mcp.sse_port`
- `ZEROCOLLAB_UPLOAD_DIR` - overrides `images.upload_dir`

Relative paths resolve against the shell's current directory (not the ZeroCollab install location), so `./.zerocollab` ends up inside your project folder.

Server and wrappers share the same `ZEROCOLLAB_*` env vars and the same flag names, so a launcher/profile can run multiple isolated instances by passing matching values to each process. If no flags or env vars are set, `config.toml` is used exactly as before, with no change for existing setups.

With several instances open at once, set **Settings → This server** to give each one a short name. It appears beside the title in the header and in the browser tab title, so you can tell which project a tab belongs to. It is empty by default, which looks exactly as it does now.

### API agents (local models)

Connect any local model with an OpenAI-compatible API (Ollama, llama-server, LM Studio, vLLM, etc.) to the chat room. API agents get status pills, activity indicators, @mention routing, and multi-instance support - just like the CLI agents.

1. Copy the example config:
   ```bash
   cp config.local.toml.example config.local.toml
   ```

2. Edit `config.local.toml` with your model's endpoint:
   ```toml
   [agents.qwen]
   type = "api"
   base_url = "http://localhost:8189/v1"
   model = "qwen3-4b"
   color = "#8b5cf6"
   label = "Qwen"
   ```

3. Start the wrapper:
   ```bash
   # Windows
   windows\start_api_agent.bat qwen

   # Mac/Linux
   ./macos-linux/start_api_agent.sh qwen

   # Or directly
   python wrapper_api.py qwen
   ```

The wrapper registers with the server, watches for @mentions, reads recent chat context, calls your model's `/v1/chat/completions` endpoint, and posts the response back. `config.local.toml` is gitignored so your local endpoints stay out of the repo.

### MiniMax (cloud API)

[MiniMax](https://platform.minimax.io) is a built-in cloud API agent. It uses the MiniMax-M3 model via MiniMax's OpenAI-compatible endpoint. To use it:

1. Get an API key at [platform.minimax.io](https://platform.minimax.io)

2. Set the environment variable:
   ```bash
   export MINIMAX_API_KEY=your-key-here
   ```

3. Launch:
   ```bash
   # Windows
   windows\start_minimax.bat

   # Mac/Linux
   sh macos-linux/start_minimax.sh

   # Or directly
   python wrapper_api.py minimax
   ```

Available models: `MiniMax-M3` (default), `MiniMax-M2.7`, `MiniMax-M2.7-highspeed` (faster). China mainland users can change `base_url` to `https://api.minimaxi.com/v1` in `config.toml`.

## Architecture

```
 Browser UI (static/ chat client + React workspace)
        |  WebSocket + REST, port 8300
        v
 +--------------------------------------------------+
 | run.py process                                   |
 |   app.py         FastAPI web UI and REST         |
 |   store.py       JSONL message log               |
 |   router.py      @mention routing, loop guard    |
 |   registry.py    live agent identities           |
 |   room_runtime   managed native agents           |
 |   mcp_bridge.py  MCP chat tools (HTTP 8200,      |
 |                  SSE 8201)                       |
 +--------------------------------------------------+
        ^                              |
        | MCP through mcp_proxy.py     | trigger queue files (agents.py)
        | (one proxy per instance)     v
 +-----------------+  injects  +------------------------+
 | Agent CLI       |<----------| wrapper.py             |
 | (Claude, Codex) |  prompts  | Win32 console / tmux   |
 +-----------------+           +------------------------+
```

Launcher-driven agents run in their own terminal under `wrapper.py`, which registers them, injects prompts and watches their screen. Agents in the managed workspace are run by `room_runtime.py` through the official CLI protocols in `native_adapters.py`, with the coordination tools from `room_mcp.py`.

**Key files:**

| File | Purpose |
|-|-|
| `run.py` | Entry point: starts the MCP servers and the web UI |
| `app.py` | FastAPI web UI, WebSocket, REST endpoints, registration API, security middleware |
| `store.py` | JSONL message persistence with observer callbacks |
| `router.py` | @mention routing with the per-channel loop guard |
| `registry.py` | Runtime registry: slots, identity claims, renames for all live agents |
| `agents.py` | Writes trigger queue files picked up by the wrappers |
| `queue_file.py` | Cross-process JSONL queue producers, consumers and clears |
| `archive.py` | Chat history export and import |
| `mcp_bridge.py` | MCP chat tools (`chat_send`, `chat_read`, `chat_claim` and the rest) |
| `mcp_proxy.py` | Per-instance MCP proxy that injects the sender identity |
| `wrapper.py` | Runs the real interactive CLI with auto-trigger on @mentions, heartbeat and activity |
| `wrapper_windows.py` | Windows: Win32 `WriteConsoleInput` injection and screen-buffer activity |
| `wrapper_unix.py` | Mac/Linux: tmux bracketed-paste injection and pane-capture activity |
| `wrapper_api.py` | Bridges the chat to an OpenAI-compatible endpoint |
| `config_loader.py` | Merges `config.toml` and `config.local.toml`, applies CLI and env overrides |
| `room_runtime.py` | Managed agents for the persistent workspace |
| `native_adapters.py` | Official, locally authenticated CLI protocols |
| `room_mcp.py` | Run-scoped coordination tools for the native CLIs |
| `project_room.py` | Durable project coordination and bounded room context |
| `project_registry.py` | Index of every project: board, team, goal, chats and repository |
| `coordination_records.py`, `coordination_queue.py`, `coordination_state.py` | Reviews and follow-ups, coalesced wake-ups, task execution facts |
| `room_goals.py` | Explicit shared goals |
| `team_builder.py`, `agent_profiles.py`, `provider_controls.py` | Team proposals, agent profiles, model and effort choices |
| `room_markup.py`, `steering_delivery.py`, `local_protocol.py`, `git_preview.py`, `durable.py` | Image markup, mid-turn steering, local model call validation, bounded Git previews, atomic writes |
| `supervisor.py`, `room_deploy.py`, `release_pack.py` | Supervised deployment of approved release packages |
| `release_manager.py`, `release_stage.py`, `release_rehearse.py`, `release_request.py`, `release_maintenance.py`, `host_handover.py` | Agent-managed releases: staging, isolated rehearsal, the install gate, the drain's final write-close step and supervisor handover |
| `work_continuity.py` | Restarts idle agents that have ready work, wakes unanswered requests, and reports stalls |
| `process_enforcement.py` | Room-enforced process rules, such as the release-train item for approved work that isn't staged |
| `profile_bell.py`, `agent_skills.py`, `agent-skills/` | Profile approvals in your bell, and the shared skills agents load on demand with `room_skill` |
| `config.toml` | Configuration: agents, ports, routing, runtime |
| `frontend/` | React workspace source, built into `static/modern/` |
| `static/` | Chat client (`chat.js` plus `static/chat/*.js`), styles and brand assets |
| `windows/_launch.bat`, `macos-linux/_launch.sh` | Shared launcher steps: venv bootstrap, CLI check, server start and wait on the configured port |
| `windows/start_*.bat`, `macos-linux/start_*.sh` | Per-agent launchers; each sets its CLI, hints and wrapper arguments and calls the shared launcher |

## Requirements

- **Python 3.11+** (uses `tomllib`)
- At least one CLI agent installed (Claude Code, Codex, etc.)
- **Windows**: no extra dependencies
- **Mac/Linux**: `tmux` (for auto-trigger - `brew install tmux` or `apt install tmux`)

Python package dependencies (`fastapi`, `uvicorn`, `mcp`) are listed in `requirements.txt`. The quickstart scripts automatically create a virtual environment and install these on first launch - no manual `pip install` needed.

## Platform notes

Auto-trigger works on all platforms:

- **Windows** - `wrapper_windows.py` injects keystrokes into the agent's console via Win32 `WriteConsoleInput`. The agent runs as a direct subprocess.
- **Mac/Linux** - `wrapper_unix.py` runs the agent inside a `tmux` session and delivers each prompt as a single bracketed paste (`tmux paste-buffer -p`), so a CLI that supports bracketed paste reassembles a long prompt even when the pty splits it across reads. Detach with `Ctrl+B, D` to leave the agent running in the background; reattach with `tmux attach -t zerocollab-claude`.

The chat server and web UI are fully cross-platform (Python + browser).

## Security

ZeroCollab is designed for **localhost use only** and includes several protections:

- **Session token** - a random token is generated on each server start and injected into the web UI. All API and WebSocket requests must present this token.
- **Loopback-only registration** - agent registration, deregistration, and heartbeat endpoints only accept connections from localhost, preventing remote agent impersonation.
- **Host checking** - every HTTP and WebSocket request must name `127.0.0.1`, `localhost` or `[::1]` (or the configured `server.host`) in its Host header. A DNS-rebinding page uses its own domain name, so it cannot load the page that carries the session token.
- **Origin checking** - the server rejects API requests from origins that don't match `localhost` / `127.0.0.1`, preventing cross-origin requests.
- **No `shell=True`** - subprocess calls avoid shell injection by passing argument lists directly.
- **Network binding warning** - if the server is configured to bind to a non-localhost address, it refuses to start unless you explicitly pass `--allow-network`.

The session token is displayed in the terminal on startup and is only accessible to processes on the same machine.

> **`--allow-network` warning:** Network mode binds to a LAN IP, which exposes the server to your local network over unencrypted HTTP. Anyone on the same network can sniff the session token and gain full access - including the ability to @mention agents and trigger tool execution. If agents are running with auto-approve flags, this effectively grants remote code execution on your machine. **Only use `--allow-network` on a trusted home network. Never on public or shared WiFi.** When binding to every interface (`host = "0.0.0.0"`), also list the names or addresses other computers use, for example `allowed_hosts = ["192.168.1.20", "studio.local"]` under `[server]`. Without that list only this computer can open the room. Wildcards are refused.

## License

ZeroCollab is maintained by ZeroTheAbsolute and released under the MIT License. See [LICENSE](LICENSE).
