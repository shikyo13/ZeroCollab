# Architecture and flows

How the pieces fit at runtime, then the four flows most changes touch: a chat message, a managed agent turn, a repository change and a deployment. Diagrams are plain ASCII.

## Processes

```
 Start-Room.ps1 / Start-Room.cmd
        |
        v
 supervisor.py start  ---->  supervisor.py run  (owns the server process; never part of a release)
                                  |        ^
                     starts/stops |        | data/deploy/ spool (requests, maintenance.json,
                                  v        |  current.json, status.json, receipts, journal)
 +-------------------------------- run.py (one Python process) --------------------------------+
 |                                                                                             |
 |  uvicorn: app.py (FastAPI)            MCP HTTP :8200 (daemon thread)   MCP SSE :8201        |
 |    - /  page + session token (run.py)   mcp_bridge.py chat_* tools       mcp_bridge.py      |
 |    - /ws  one WebSocket per tab                                                             |
 |    - /api/...  REST, including /api/runtime/coordination/{run_id}  <-------------------+     |
 |                                                                                        |     |
 |  shared in-process objects:  store (store.py)  registry (registry.py)  router          |     |
 |                              RoomRuntime (room_runtime.py) + ProjectRoom               |     |
 |                              project_registry  room_goals  room_deploy (MutationGate)  |     |
 +----------------------------------------------------------------------------------------|-----+
        |                                   |                                  |          |
        v                                   v spawns per turn                  v          | HTTP, run-scoped
  data/  (JSON / JSONL records)      native CLI (native_adapters.py:     Git worktrees    | token and run id
  see file-index.md                  claude, codex, gemini,              data/worktrees/  |
                                     antigravity; local models via       <project>/       |
                                     local_protocol.py)                  <repo-key>/      |
                                            |                            <agent>          |
                                            | stdio MCP                                   |
                                            v                                             |
                                     room_mcp.py (separate Python process per run) -------+
                                     room_* tools; AGENT_ROOM_URL, AGENT_ROOM_SESSION_TOKEN,
                                     AGENT_ROOM_RUN_ID come from native_adapters.room_environment
```

Browser side:

```
 static/index.html
   classic scripts (core, store, prefs, channels, chat, chat/*, runtime.js)   <- chat, composer, settings
   static/modern/workspace.js  (built from frontend/src by Vite)            <- board, projects, team, activity
        |                                    |
        +--------- one /ws (owned by chat.js) +---- frames re-emitted to React via lib/room.ts useRoomEvent
        +--------- /api/* fetch with X-Session-Token (lib/api.ts)
```

Launcher-driven agents (`windows/`, `macos-linux/`, `wrapper*.py`) are the older path: a visible terminal runs the CLI and `wrapper.py` types queued prompts into it from `<agent>_queue.jsonl`. Managed agents (`room_runtime.py`) are the default workspace path and run each turn as a fresh CLI invocation inside the agent's worktree.

## Flow 1: a chat message

```
 browser composer --ws--> app.py websocket handler
                              |
                              v
                        store.add()  -- append zerocollab_log.jsonl
                              |
                              v  observer (any thread)
                        _on_store_message -> _handle_new_message
                              |
            +-----------------+--------------------------+
            v                                            v
     broadcast() to every /ws               managed runtime owns this channel?
                                               no  -> stop (other project's chat)
                                               yes -> router.get_targets(@mentions)
                                                        loop guard: routing.max_agent_hops
                                                      -> agents.trigger(target)
                                                           managed: RoomRuntime.enqueue()
                                                           launcher: <agent>_queue.jsonl
```

Agents post back through the MCP `chat_send` tool or the room tools; that is another `store.add()` and the same path.

## Flow 2: a managed agent turn

```
 RoomRuntime.enqueue(name, ...)  -> WakeQueue (coordination_queue.py: max 4 entries,
                                    one pending batch per channel, human instructions kept in order)
        |
        v
 worker(name): take entry, hold it in deploy_held
        |
        v
 run_turn(): wait for admission
        - project and agent not paused, provider not blocked
        - running < parallel capacity
        - deployment admission open (room_deploy)
        - routing chain and conversation budget (project_room limits)
        |
        v
 build prompt: collaboration rules + room context (project_room) + instructions
        (instruction_hash changes when the team, repositories or rules change -> fresh native session;
         otherwise project_room resumes the saved provider session)
        |
        v
 native_adapters.<provider>(run, prompt)   cwd = agent's primary worktree
        - streamed deltas and tool events -> emit() -> /ws
        - room_* tool calls -> room_mcp -> RoomRuntime.coordination -> project_room / records
        - same-channel human input while running -> steering_delivery
        |
        v
 finish: native.terminate(run, grace): completed or stopped CLIs get stdin EOF and up to
         EXIT_GRACE seconds to exit (a hard kill mid OAuth refresh signs Claude Code out);
         failed runs are killed at once.
         collect_diffs() (one string per agent, "[Repository key]" sections),
         runtime_end event, goal scheduling (room_goals), publish_status()
```

## Flow 3: changing or adding a repository

Detail in [repositories.md](repositories.md).

```
 Projects > Repository panel (Repository.tsx)
   POST /api/runtime/projects/{id}/repository {action, path|key, create, interrupt}
        |
        v
 RoomRuntime.start_repository_change  (refused during deployment rehearsal by MutationGate)
        |  repository_transition = {phase: preparing, action, ...}
        v
 _change_repository (shielded task; survives a dropped HTTP caller)
   1. validate path (repository_root / new_repository_target), check no other project owns it
   2. phase = draining: wait for running turns
        - Cancel   -> keep current repositories, stop
        - Interrupt now -> cancel_turn() on each running agent
   3. under profile_lock, phase = preparing:
        create repo if asked; repository_plan(action); refuse remove while tasks reserve that key
        prepare worktrees for every agent (off the event loop)
   4. commit point: project_registry.commit_repository() writes project.json
        (repositories, repository_history, initial_repository_key) in one write
   5. update agent states, save workspace.json, clear transition, publish_status()
```

## Flow 4: a deployment

```
 python release_pack.py build --repo <repo> --commit <rev> --spool <spool>
                                -> <spool>/releases/<id>/  (the live spool is data/deploy/)
 human approves in Deployments  -> app writes data/deploy/requests/<id>.json
        |
        v
 Deployment.run() in supervisor.py run (single-flight lock, journal.json)
   take, plan
   drain: maintenance.json admission closed, writes open (interrupt only if the request allows)
          then writes blocked
        |  failure up to here: gate reopened (if it was closed), receipt rejected or aborted,
        |  server untouched
        v
   ---- server stop boundary: any later failure goes to rollback() ----
   stop server and confirm it exited
   snapshot data records (exact only while writes stay blocked)
   apply: back up replaced files and the previous current.json to backups/<id>/,
          place the release, write current.json            (writes still blocked)
   verified_start + verify: served release, endpoints, static files, on-disk hashes
        |
        |  rollback() on failure in this block:
        |    server not confirmed stopped  -> recovery_required, nothing restored
        |    compare-and-swap restore of files, previous current.json and data snapshot
        |      conflicts                    -> conflict, room left STOPPED (no restart)
        |      clean -> verified_start(previous) -> reopen -> rolled_back
        |               previous fails to verify  -> rollback_failed, room left stopped
        v
   ---- reopen boundary: journal records reopen_expect, then open_gate ----
   reopen admission and writes, resume agents unless they were paused
   receipt deployed; journal.json removed
   from here nothing is restored: an error while reopening is noted in the receipt only,
   and supervisor.py recover keeps the reopened release and its data
```

`supervisor.py recover` (room stopped) handles a crash mid-deployment: before the reopen boundary it restores files and data, after it keeps the release; either way the next start verifies before reopening.

Inside the app, `room_deploy.DeploymentState` reads `maintenance.json`; `enter_mutation` / `leave_mutation` / `track_continuation` count in-flight writes so the drain knows when the room is quiet, and `MutationGate` refuses mutating routes while writes are blocked.
