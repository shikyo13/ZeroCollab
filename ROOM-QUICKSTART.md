# ZeroCollab quickstart

By ZeroTheAbsolute.

## Start and stop

Run `Start-Room.cmd`, then open http://127.0.0.1:8300. Use `Stop-Room.cmd` to close the server. Closing the browser leaves the workspace running. Windows launchers use this folder's private Python environment.

For macOS/Linux, install the official Codex, Claude and Antigravity CLIs, sign in using their own commands, create a Python environment from `requirements.txt`, and run `python run.py` from this folder. Local model endpoints are configured separately.

The left navigation has four views: **Chat**, **Board**, **Team** and **Activity**. **Stop all** / **Resume all** and any pending agent approval requests stay visible above every view. **Quick guide** opens a short orientation; nothing opens automatically.

## Chat

Click **Address team** to insert `@all` into your message, or choose recipient toggles above the composer. You can also type an agent's handle, such as `@codex` or `@claude`, directly. Click **Send** when ready. Agents see the durable shared transcript and peer code changes. A human instruction opens a fresh bounded conversation; peer mentions and task delegation consume its handoff budget. Send a new message at any time to steer the discussion.

Converse, organize, delegate and work directly in chat. Agents choose their own responsibilities and task breakdown. A saved spec and an attached repository are optional, and ordinary messages are never automatically promoted to specs.

## Board

**Tasks** is the ownership board. Agents and you can create, claim, delegate and update bounded tasks. Each task has one owner, reserved file and directory paths, optional dependencies, and a check-in history. Reserved paths cannot overlap another owned task; branch/worktree isolation prevents agents from overwriting each other's files. These reservations coordinate intent; native tool use still follows each CLI's own permissions. Owned work without a check-in for ten minutes is flagged. Open a task to change its status or owner, start its owner on it, edit paths and dependencies, or post a check-in. Marking a task done while dependencies or reviews are open asks you to confirm.

**Reviews & follow-ups** lists peer reviews and tracked next actions, with anything waiting on you at the top. You can request a review from an agent, respond when you are the reviewer, cancel requests, and create, resolve or cancel follow-ups. Board requests only wake the recipient when you tick **Wake the recipient now**; otherwise they wait in that agent's inbox.

**Spec** holds the optional shared spec. **Save spec** stores it; **Save and discuss** also posts it to the current channel addressed to `@all`, without assigning roles or prescribing responses. **Use my last chat message** copies your latest message into the editor for review.

**Memory** shows the team's progress, decision, blocker and handoff notes. Decisions stay in project memory across restarts.

**Limits & usage** shows the current conversation's turns, handoffs, reported tokens and shared context, and holds the optional execution controls. Optional idle/total-duration, agent-turn, native-tool and reported-token cutoffs are disabled by default (0 = off). The handoff guard, local tool-round guard and per-agent queues always bound autonomous activity.

## Team

Each agent is a named profile with its own mention handle, provider, color, model, reasoning effort and personal prompt. **Add agent** creates one; **Duplicate** copies a profile, which is also how you switch an agent to a different provider; **Archive** retires one while keeping its history and working tree. Handles stay stable once created. Model and effort changes apply to the agent's next turn; an active turn keeps its original selection. Choices come from the connected native CLI or local endpoint. Gemini effort chooses the corresponding preset in the selected model family. A local server exposing one loaded model has a fixed model selector.

Each agent row shows live status, queued requests and the active turn's model and effort. **Stop** cancels that agent's turn and clears its queue; **Resume** allows new requests. Failed providers stop processing their queued work; **Refresh connections** reloads connection state and model choices when you are ready to retry.

## Activity

Activity shows recent tool activity. A project starts without a repository. When a coding task needs one, open **Projects** and use **Add repository** on the active project: attach the exact root of an existing Git repository with at least one commit, or create a new one. Agents then receive their own branches and worktrees from committed HEAD; existing files and uncommitted edits remain in the original repository. Agents can also ask for a repository with room_repository; the request appears on the same panel for you to approve or dismiss.

**Change repository** switches the project to another repository without stopping the room. Running turns finish first (or stop at once if you choose to interrupt them), new turns wait, and queued work, board, chats, team and goal are kept. Each agent then starts a fresh session in a separate worktree for the new repository. The previous repository's worktrees stay on disk, are listed under **Previous repositories**, and are reused if you switch back.

Click **Changes** in the top control bar to inspect each agent's patch. Agents receive peer patches in their context so they can review concrete results. Integration into your main branch is a deliberate task: request it explicitly when you want that change.

## How agents work here

Every provider receives the shared instructions in **COLLABORATION.md**: coordinate through the chat, claim ownership and file scopes, respect reservations, delegate useful work, share progress, and challenge assumptions with evidence. The team chooses its own roles. Changes to those instructions take effect on the next turn; existing native sessions are refreshed so old instructions cannot linger. Native session reuse also requires matching model and effort settings.

Efficiency comes from incremental delivery and bounded excerpts rather than an ever-growing transcript replay. Agents receive new relevant messages and compact shared state since their own delivery cursor. Initial shared-context excerpts fit within 16,000 characters; older chat, full specs and other details remain available through `room_read`. Routine progress updates do not wake peers. Native clients retain their own context handling and compaction. The whole team can run concurrently by default. There are no model calls for automatic summarization or polling.

Native CLIs retain their subscription sign-ins, tools, skills and MCP configuration. The workspace adds a local coordination MCP server. Its authorized automatic tool mode is enabled in config.toml under runtime.automatic_tool_approvals: Codex uses never approval with danger-full-access, Claude uses bypassPermissions, and Antigravity uses its per-process skip-permissions flag. Local file calls are schema-validated, confined to the agent's working directory, automatically approved, and logged. Global provider settings, account grants and model-server configuration are unchanged. Native administrative restrictions still apply; new authentication/data elicitations remain interactive. Setting the option false restores interactive tool approval. Local tool-enabled text is held until classified to keep raw protocol blocks out of chat; only structured calls matching advertised schemas execute.

## Local models and data

To add Ollama, LM Studio or another OpenAI-compatible local model, copy `config.local.toml.example` to `config.local.toml`, enable the relevant section, set the endpoint and exact served model ID, then restart. Extra managed agents automatically join and receive their own worktrees. The workspace leaves your model servers running as configured.

The transcript, provider selections, tool events and project state live under `data/`. Worktree changes are preserved across restarts. Provider errors and subscription limits appear in Team and in the transcript. Gemini CLI's retired individual sign-in interface is replaced here by official Antigravity; `Login-Gemini.cmd` opens that client's interactive sign-in when needed.

## License

ZeroCollab is released under the MIT License. See `LICENSE`.
