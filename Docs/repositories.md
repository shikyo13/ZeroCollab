# Repositories per project (design as built)

A project can have no repository (a conversation project), one, or several. The user can attach, replace, add, re-prioritise and remove repositories while the room runs. Agents can only ask; the user applies the change. This note describes the merged implementation (ZeroCollab main 406164f).

## Data model (`projects/<id>/project.json`)

| Field | Meaning |
|-|-|
| `path` | The primary repository root (kept for older readers) |
| `repositories` | `[{key, path, primary}]`; absent or empty before the first attachment, otherwise exactly one entry has `primary: true` |
| `repository_history` | `[{key, path, attached, detached, by?, worktrees}]`; one open entry (no `detached`) per attached repository. New attachments record `by: "user"`. Entries synthesized for a repository attached before history existed have `attached: null` and no `by` |
| `initial_repository_key` | Set at the first attachment. Tasks created before any repository existed resolve to it |
| `repo_request` | An agent's pending request: `{by, path, create, reason, time, action, replace, current}` |

`project_registry.repository_key(path)` is a slug of the folder name (up to 24 characters) plus the first 12 hex characters of the SHA-256 of the normalised resolved path, for example `zerocollab-1a2b3c4d5e6f`. Keys are stable for a path and safe as folder names.

## Worktrees

Each agent gets one Git worktree per attached repository at `data/worktrees/<project>/<repo-key>/<agent>`, on a branch `chatroom/<project>/<repo-key>/<agent>-<6 hex>`. An older worktree at `data/worktrees/<project>/<agent>` is reused only if its Git common directory matches the repository; a worktree that belongs to a different repository is refused rather than reused. Detaching a repository keeps its worktrees on disk (history records them).

A turn runs in the primary worktree. The agent's instructions list the others:

```
Additional repositories (key -> your isolated worktree):
<key> -> <path>
Use key:relative/path for task reservations, review files and local file tools in additional repositories. Bare task paths are pinned to the primary when claimed.
```

Because the instructions change, `instruction_hash` changes and the next turn starts a fresh native session instead of resuming one with stale paths.

## Task scope

- `ProjectRoom.task_repository_key(task)` resolves, in order: the task's own `repository_key`, the project's `scope_repository`, `initial_repository_key`, then the current primary.
- Claimed paths written as `key:relative/path` must name an attached key. Bare paths belong to the task's repository.
- Overlap checks compare the key first, so `a:src` and `b:src` never collide.
- Local file tools (`room_runtime.file_tool_target`) accept `key:path` and confine the path to that repository's worktree.
- `collect_diffs()` keeps one diff string per agent, with a `[Repository <key>]` section per repository when the agent has more than one worktree, cut to `DIFF_CHAR_LIMIT`.

## Actions

`POST /api/runtime/projects/{id}/repository` with `{action, path | key, create, interrupt}`:

| Action | Input | Effect |
|-|-|-|
| `replace` | `path` (or `create: true` for a new folder) | New primary; the old primary is detached; additional repositories stay |
| `add` | `path` / `create` | Attach as an additional repository (becomes primary if the project had none) |
| `primary` | `key` | Make an attached repository the primary |
| `remove` | `key` | Detach an additional repository. Refused for the primary, and refused with 409 (`RepositoryInUse`) while an owned, unfinished task reserves paths in it |

`POST .../repository/transition {action: cancel | interrupt}` steers a change in progress. `runtime_state.repository_transition` is `{path, phase: draining | preparing, action, waiting_on, queued, cancel_requested}` or null.

The change runs as a background job that survives the HTTP caller disconnecting: drain running turns (or interrupt them), prepare every agent's worktrees off the event loop, re-check ownership and removal, then commit `project.json` in one write. Nothing before the commit changes the project, so a cancel or failure keeps the current repositories. Team edits wait while a change runs, and `MutationGate` refuses the route during a deployment rehearsal. See flow 3 in [architecture.md](architecture.md).

## Agent requests

`room_repository` (agent tool) calls `project_registry.request_repository` with `action: add | replace`, a path or `create`, and a required reason. It stores `repo_request`. The Projects page shows it as a card. The user approves it through the same form, or dismisses it. On a project with no repository, an `add` request is presented as the first attachment.

## UI

`frontend/src/workspace/Repository.tsx`, in the Projects page for the active project:

- Repository rows with Make primary and Remove. These appear only when the server reports `repositories`.
- Add repository, and Change (replace) for the primary.
- A drain choice (wait or interrupt) when agents are running.
- A transition banner with Cancel change, and Interrupt now while draining.
- The agent request card.
- Previous repositories, from history entries that are not currently attached.

## Not done or deferred

- `initial_repository_key` is read from the project record on each task resolution. It is not cached.
- `repository_history` grows without bound.
- File names containing `:` cannot be addressed by the local file tools, because they read the text before the colon as a repository key.
- The drain loop polls every 100 ms.
- History `by` is `user` on new attachments and missing on synthesized legacy entries. It never records which agent requested the change.
