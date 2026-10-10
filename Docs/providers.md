# Providers: current state and release plan

Status: design, 2026-10-09 (task 0a5de055, user request #4543). Source refs are to release commit 1909d84 (0.12.3).

## Current state

Providers are `managed = true` entries in `config.toml` (`[agents.<template>]`). `agent_profiles.AgentProfiles.templates` copies them; every team member is a profile on one template. There is no in-app way to add, edit or sign in to a provider.

| Template | Engine (native_adapters / room_runtime) | Sign-in today | Signed-out detection | Model list |
|-|-|-|-|-|
| codex | `codex app-server --stdio` (`native.codex`) | Codex CLI's own ChatGPT login (`~/.codex`) | None before a turn; the turn fails | `cli_catalog`: app-server `model/list` |
| claude | `claude --print --input-format stream-json` (`native.claude`) | Claude CLI's own login | `refresh()` runs `claude auth status`; shows "Run claude auth login in your own terminal" | `cli_catalog`: control `initialize` |
| antigravity | `agy` (`native.antigravity`) | `login_gemini.py`, run by hand in a terminal | Only when the catalog lookup fails (state `blocked`) | `agy models` |
| api (local) | `RoomRuntime.local_turn`: OpenAI-compatible `/chat/completions`, room file and coordination tools | None (no auth header) | `refresh()` GETs `/models` and checks the selected model is served | `/models`, effort from llama.cpp `/props` |

Deliberate guard: `native_adapters.subscription_env()` removes every variable in `API_ENV` (OpenAI, Anthropic, Gemini keys and base URLs, Bedrock/Vertex/Foundry switches, `CLAUDE_CODE_OAUTH_TOKEN`) from provider processes, so a stray key in the user's environment can never switch an agent to paid API billing. It does not change a login stored by the CLI itself: a Claude CLI signed in through the Anthropic Console, or a Codex CLI logged in with an API key, still bills that API account. The status page reports that as `billing: api` instead of promising subscription billing.

CLI discovery (`native_adapters.command`): `PATH`, then known install paths (`~/.codex/packages/standalone/current/bin/codex.exe`, `~/.local/bin/claude.exe`, `%LOCALAPPDATA%/agy/bin/agy.exe`, bundled `providers/antigravity/agy.exe`). Missing CLI raises "official CLI is not installed or discoverable".

## Gaps for a professional release

1. No Setup page: the user cannot see which CLIs are installed, which accounts are signed in, or fix either from the app.
2. Sign-in needs a terminal (`claude auth login`, `codex login`, `python login_gemini.py`).
3. Codex and Antigravity sign-out is found only when a turn or catalog lookup fails.
4. Providers exist only in `config.toml`; adding a local server means editing TOML and restarting.
5. Local models (`local_turn`):
   - Output is capped at `min(2048, max_tokens)`, default 1024, so longer answers are cut.
   - Fixed `temperature` 0.4.
   - No `Authorization` header, so keyed OpenAI-compatible servers (vLLM, LiteLLM, OpenRouter) cannot be used.
   - No context-window awareness: the room prompt budget (`input_chars`, 16000 chars) is the same for a 4k-token local model as for a frontier model.
   - No check that the server returns structured `tool_calls` (llama.cpp needs `--jinja`; Ollama only for tool-capable models). Failure shows at turn time as "returned tool markup as text".
   - No detection of Ollama (11434), LM Studio (1234) or llama.cpp (8080). (S1 added detection, and MLX LM and mlx-serve were added later; see Local model plan.)
6. The CLIs are installed separately by the user; the installer neither bundles nor checks them.

## Auth options

| Option | How | Billing | Fits |
|-|-|-|-|
| A. Subscription sign-in from the app | Setup page runs the official login: `claude auth login --claudeai`, `codex login` (browser), `agy` sign-in. Status from `claude auth status --json`, `codex login status`. Credentials stay in each CLI's own store | Subscription, no API charges | Default for everyone |
| B. API key per provider (opt-in) | Key stored in the OS credential store (Windows Credential Manager, macOS Keychain; Python `keyring`). Passed only to that provider's process: Claude via `ANTHROPIC_API_KEY` env on spawn; Codex via `OPENAI_API_KEY` env or `codex login --with-api-key` (that one writes Codex's global store, so prefer env per process). `subscription_env` stays the default; only a profile explicitly set to API-key mode gets its own key | Paid API usage | Teams without subscriptions; local or remote OpenAI-compatible servers with keys |
| C. Long-lived subscription token | `claude setup-token` (needs a Claude subscription) stored in the credential store and passed as `CLAUDE_CODE_OAUTH_TOKEN` | Subscription | Headless or service installs where a browser sign-in on that machine is not possible |

Not possible: re-implementing vendor OAuth inside ZeroCollab. Subscription credentials belong to the official CLIs.

Recommendation: A for every provider. B as an explicit per-provider opt-in with a visible "API billing" label, never inherited from the environment. C only if headless installs matter.

## Local model plan

- Detection: probe `127.0.0.1:11434` (Ollama `/api/tags`, `/v1/models`), `:1234` (LM Studio `/v1/models`), `:8080` (llama.cpp `/v1/models`, `/props`). Offer "Add local provider" with the found models; show "not running" with the start command when installed but stopped (`ollama serve`, `lms server start`).
- MLX (macOS on Apple silicon only; other platforms do not list these rows), as built in `provider_setup.LOCAL_SERVERS`:
  - MLX LM (`pip install mlx-lm`, `mlx_lm.server --model <mlx-community/...>`) listens on `127.0.0.1:8080`, the same default as llama.cpp. Its `/v1/models` lists the local Hugging Face cache, and each entry is only `id`/`object`/`created`. Chat completions accept `tools` and return structured `tool_calls` for models whose chat template supports them. Its `--max-tokens` default is 512, but `local_turn` always sends `max_tokens`.
  - mlx-serve (`brew install mlx-serve`, `mlx-serve serve`, needs macOS 26.2+) listens on `127.0.0.1:11234` and serves `/v1/models` and `/v1/chat/completions`.
  - One shared port means one probe, and the port is credited only on positive evidence. `served_by()` recognizes llama.cpp from `owned_by: "llamacpp"`. MLX LM has no marker, and a Python `Server` header or an installed app is not evidence. An identified server shows Running, and the other row shows "<server> is using this port". An answer that names no server becomes a single "Running, not identified" row for that address ("llama.cpp or MLX LM"). Only the identified owner, or that single row, counts as the configured provider at the address.
  - Listing and probing both require Apple silicon (`apple_silicon()`: `platform.machine()`, or `sysctl hw.optional.arm64` for a Python running under Rosetta). Intel Macs, Windows and Linux never probe 11234. Manually configured `api` providers that point at an MLX server elsewhere keep working on any platform.
- Capability check on add: one tiny `/chat/completions` request with a single tool; pass only if the reply carries structured `tool_calls`. Read the context length (Ollama `/api/show` `num_ctx`/model info, llama.cpp `/props` `n_ctx`) and store it.
- Config per local provider: base URL, optional key (credential store), output token limit (raise the 2048 cap; default from context size), context window used to bound the prompt budget for that agent.

## Proposed build slices

| Slice | Delivers | Needs user decision |
|-|-|-|
| S1 | Provider status API and read-only Setup page: CLI installed/version, signed in or not (claude, codex, agy), local servers found | No |
| S2 | Sign in / sign out buttons that launch the official CLI logins and poll status; Codex and Antigravity signed-out detection in `refresh()` | No (subscription only) |

S2 as built (`provider_setup.py`, `Providers.tsx`): `POST /api/runtime/providers/<provider>/sign-in` opens a new console (Windows `CREATE_NEW_CONSOLE`, macOS Terminal through `osascript`, Linux `x-terminal-emulator`/`xterm`; otherwise it returns the command to run) running `python provider_setup.py sign-in <provider>`. That helper runs the official command (`claude auth login`, `codex login`, `agy`) with `subscription_env()` and waits for Enter so the result stays readable. Launches are serialized; a second click while that window is open does not open another on Windows or xterm, where the launched process lives as long as the window. macOS Terminal and terminal wrappers that hand off to a server return at once, so there a repeated click can open another window. The button is held from the click. For providers with a status command (`reports`: Claude, Codex) the page polls every 5 s for up to 3 minutes until the CLI reports signed in, or until the reported method changes for "Change sign-in" on an API-billed login; the deadline is checked on every tick, so failing status requests cannot hold the button. Antigravity has no status command, so it gets a next-step note instead of a wait. Rehearsal previews refuse the route. There is no sign-out button: signing out stays in each CLI. `refresh()` uses the same `sign_in_status`, so a signed-out Claude or Codex agent shows "Sign-in needed" instead of failing its first turn; an unknown answer does not block it. Antigravity signed-out detection remains the model-list check.
| S3 | Providers stored in `data/providers.json` and edited in the app, merged over `config.toml` templates; no restart | No |

S3 as built (`provider_store.py`, `ProviderForm.tsx`, `Providers.tsx`):
- **Store.** `data/providers.json` is `{"providers": {<id>: entry}}`, layered over the managed `config.toml` templates by id. A new id adds a connection. A config id with an entry is "edited", and Reset deletes the entry. `{"removed": true}` hides a config connection until Restore. A file that can't be read is reported on the page and never overwritten; changes stay disabled until it is fixed.
- **Kinds.** Codex, Claude and Antigravity (one connection each, using the official CLI and its own sign-in), and `api` for OpenAI-compatible servers. So an install whose `config.toml` has an empty `[agents]` can still connect subscriptions.
- **Fields.** Name and color for every connection. For a server, also the address (http or https, no user name, password, query or fragment), the model, and the maximum response length (256 to 32768 tokens). `local_turn` now sends that value instead of capping it at 2048.
- **Checks on save.** Creating a connection, or changing a server's address or model, lists that connection's models with exactly those settings first. The model must be served. Ids are fixed: a rename edits only the label, and `status` and `probe` are reserved.
- **Guards.** Changes are refused in a rehearsal preview, while a deployment check blocks writes, and during a repository change. Edit, Remove and Reset are refused while an agent on that connection is mid-turn, so a turn's config is never changed under it. Idle agents get the new template at once. Remove is refused while any agent uses the connection in any saved project, archived agents included.
- **First agent.** A connection no agent uses keeps its own model list (from save and from Refresh connections), so Add agent offers it, and the first agent's model is checked against that list. No agent is created automatically.
- **Routes.** `GET /api/runtime/providers`, `POST /api/runtime/providers` (create), `POST /api/runtime/providers/probe` (a server's models, nothing saved), `POST /api/runtime/providers/<id>` (edit), and `.../<id>/remove` and `.../<id>/reset`.
- **Not in S3.** The structured tool-call check, the context window and API keys. Those are S4 and S5.
| S4 | Local provider detection, capability probe, key header, output and context limits | No |
| S5 | API-key mode (option B) with the OS credential store | Yes: enable paid API keys at all |
| S6 | Installer checks or bundles the CLIs | Yes: bundle (licensing, size) or detect and link |

## Decisions for the user

1. Auth modes: subscription sign-in only, or also opt-in API keys per provider (paid usage).
2. CLIs: bundle them in the installer, or detect and link to the official installers.
3. First local servers to support: Ollama, LM Studio, llama.cpp, plus MLX LM and mlx-serve on Macs (user #4891). All are OpenAI-compatible.

## Verification limits

Agents must not sign in or out of the user's real accounts. Login flows are verified in a disposable profile (`CODEX_HOME`, `CLAUDE_CONFIG_DIR` pointed at a temporary folder) or by the user. Local detection is checked against a real server the user starts, or a stub server.
