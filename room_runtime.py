"""Managed agents for the ZeroCollab persistent workspace."""
import asyncio
import contextlib
import hashlib
import json
import logging
import os
import platform
import re
import subprocess
import time
import uuid
from pathlib import Path

from durable import atomic_write

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

import native_adapters as native
from provider_controls import ProviderControls
from coordination_state import pending_human_wait
from project_room import ROOM_TOOL_PROVIDERS, ProjectRoom, coordination_tools
from room_goals import RoomGoals
import project_registry
from agent_profiles import AgentProfiles
from team_builder import TeamBuilder
from local_protocol import visible_text, validate_calls
from coordination_queue import WakeQueue, current_request
from steering_delivery import SteeringDelivery

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
FILE_TOOLS = {"read_file", "file_glob_search", "grep_search", "write_file", "edit_file", "get_info"}
PRIVATE_PARTS = {".git", ".codex", ".claude", ".gemini", ".aws", ".ssh", ".env", ".agents"}


def _parse_workspace(text):
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Workspace state must be an object")
    for key in ("project", "project_id"):
        if value.get(key) is not None and not isinstance(value[key], str):
            raise ValueError(f"Invalid workspace {key}")
    return value


def load_workspace(path):
    """Recover a validated selection; never silently select a different project."""
    backup = path.with_suffix('.json.bak')
    if not path.exists() and not backup.exists():
        return {}
    try:
        return _parse_workspace(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as primary_error:
        try:
            saved = _parse_workspace(backup.read_text(encoding="utf-8"))
        except (OSError, ValueError) as backup_error:
            raise RuntimeError(f"Cannot recover workspace selection from {path} ({primary_error}); restore a valid workspace.json or its .bak") from backup_error
        LOGGER.warning("Recovered workspace selection from %s after %s", backup, primary_error)
        # Recovery must not automatically launch work under an older selection.
        return {**saved, "paused": True}


def save_workspace(path, value):
    """Atomically retain the previous valid selection before installing the next."""
    encoded = json.dumps(value, indent=2)
    if path.exists():
        try:
            previous = path.read_text(encoding='utf-8')
            _parse_workspace(previous)
        except ValueError:
            pass  # Keep the valid backup when replacing a corrupt primary.
        else:
            atomic_write(path.with_suffix('.json.bak'), lambda stream: stream.write(previous))
    atomic_write(path, lambda stream: stream.write(encoded))


def local_tool_definitions():
    def tool(name, description, properties, required):
        return {"type": "function", "function": {"name": name, "description": description,
                "parameters": {"type": "object", "properties": properties, "required": required}}}
    path = {"type": "string", "description": "Path inside your isolated worktree"}
    string = {"type": "string"}
    return [
        tool("read_file", "Read a UTF-8 project file, at most 200 lines and 256 KiB.",
             {"path": path, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, ["path"]),
        tool("write_file", "Write a UTF-8 file inside your working directory, at most 256 KiB.",
             {"path": path, "content": string}, ["path", "content"]),
        tool("edit_file", "Replace exact unique text matches in a file inside your working directory.",
             {"path": path, "edits": {"type": "array", "items": {"type": "object",
                 "properties": {"old_text": string, "new_text": string}, "required": ["old_text", "new_text"]}}}, ["path", "edits"]),
        tool("file_glob_search", "Find project files by glob, at most 100 results.",
             {"path": path, "include": string}, ["include"]),
        tool("grep_search", "Search text in project files; literal matching by default, at most 100 results.",
             {"path": path, "pattern": string, "literal": {"type": "boolean"}, "ignore_case": {"type": "boolean"}}, ["pattern"]),
        tool("get_info", "Report platform and isolated worktree. No shell execution is available.", {}, []),
    ]


def git(path, *args):
    result = subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(path), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        env=native.subscription_env(), creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Git command failed")
    return result.stdout.strip()


def scoped_path(root, raw):
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ValueError("A project file path is required")
    candidate = Path(raw)
    if any(part.lower() in PRIVATE_PARTS or part.lower().startswith(".env.") for part in candidate.parts):
        raise ValueError("Credential, Git metadata and private settings paths are excluded")
    if os.name == "nt" and ":" in raw.replace(str(candidate.drive), "", 1):
        raise ValueError("Alternate data streams are excluded")
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("Path is outside this agent's worktree")
    if any(part.lower() in PRIVATE_PARTS or part.lower().startswith(".env.")
           for part in resolved.relative_to(root.resolve()).parts):
        raise ValueError("Resolved path points to excluded private settings")
    # resolve() follows existing junctions/symlinks, including parent directories.
    return resolved


def visible_files(root):
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(p.lower() in PRIVATE_PARTS or p in ("node_modules", ".venv", "__pycache__") for p in relative.parts):
            continue
        if path.is_file() and not path.is_symlink():
            try:
                scoped_path(root, str(relative))
            except ValueError:
                continue
            yield path


def execute_file_tool(root, name, params):
    """No shell execution; confinement is enforced for every file operation."""
    if name not in FILE_TOOLS:
        raise ValueError("This tool is disabled. Shell commands are not exposed to local models.")
    if name == "get_info":
        return json.dumps({"os": platform.system(), "worktree": str(root), "shell_enabled": False})
    path = scoped_path(root, params.get("path", "."))
    if name == "read_file":
        if path.stat().st_size > 262144:
            raise ValueError("File exceeds 256 KiB limit")
        lines = path.read_text("utf-8").splitlines()
        start = max(0, int(params.get("start_line", 1)) - 1)
        end = min(len(lines), int(params.get("end_line", start + 200)))
        return "\n".join(f"{i+1}: {lines[i]}" for i in range(start, end))[:16000]
    if name == "write_file":
        content = params.get("content", "")
        if not isinstance(content, str) or len(content.encode()) > 262144:
            raise ValueError("Content exceeds 256 KiB limit")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Revalidate after directory creation and before the actual write.
        scoped_path(root, str(path))
        path.write_text(content, "utf-8")
        return f"Wrote {path.relative_to(root)} ({len(content)} characters)"
    if name == "edit_file":
        if path.stat().st_size > 262144:
            raise ValueError("File exceeds 256 KiB limit")
        original = path.read_text("utf-8")
        replacements = []
        for edit in params.get("edits", []):
            old, new = edit.get("old_text"), edit.get("new_text")
            if not isinstance(old, str) or not old or not isinstance(new, str) or original.count(old) != 1:
                raise ValueError("Each old_text must occur exactly once; new_text must be a string")
            begin = original.index(old)
            replacements.append((begin, begin + len(old), new))
        replacements.sort()
        if any(a[1] > b[0] for a, b in zip(replacements, replacements[1:])):
            raise ValueError("Overlapping edits are excluded")
        updated = original
        for begin, end, new in reversed(replacements):
            updated = updated[:begin] + new + updated[end:]
        if len(updated.encode()) > 262144:
            raise ValueError("Edited file exceeds 256 KiB limit")
        scoped_path(root, str(path))
        path.write_text(updated, "utf-8")
        return f"Edited {path.relative_to(root)}"
    if not path.is_dir():
        raise ValueError("Search path must be a directory")
    output = []
    pattern = params.get("pattern", "")
    matcher = re.compile(re.escape(pattern) if params.get("literal", True) else pattern,
                         re.I if params.get("ignore_case") else 0) if name == "grep_search" else None
    for entry in visible_files(path):
        if name == "file_glob_search":
            if entry.match(params.get("include", "*")):
                output.append(str(entry.relative_to(root)))
        elif entry.stat().st_size <= 262144:
            with contextlib.suppress(UnicodeError, OSError):
                for i, line in enumerate(entry.read_text("utf-8").splitlines()):
                    if matcher.search(line):
                        output.append(f"{entry.relative_to(root)}:{i+1}: {line[:300]}")
                    if len(output) >= 100:
                        break
        if len(output) >= min(100, int(params.get("limit", 100))):
            break
    return "\n".join(output)[:16000] or "No matches"


def request_task_ids(entry):
    ids = set()
    for request in (entry or {}).get('requests', []):
        ref = request.get('ref') or {}
        if ref.get('collection') == 'tasks':
            ids.add(ref['id'])
        if request.get('task_id'):
            ids.add(request['task_id'])
    return ids


class Run:
    def __init__(self, manager, name, entry):
        self.manager, self.name, self.entry = manager, name, entry
        self.id = uuid.uuid4().hex[:12]
        self.worktree = Path(manager.states[name]["worktree"])
        self.channel = entry["channel"]
        self.text, self.stderr = "", ""
        self.message_id = None
        self.message_phase = None
        self.completed_messages = set()
        self.published_messages = []
        self.output_chars = 0
        self.process = self.stderr_task = None
        self.provider_session = None
        self.settings = dict(manager.controls.values[name])
        self.session_key = name + ':' + self.channel
        self.previous_session = {}
        self.context_delivery = {}
        self.receipts = set()
        # Tasks this turn is known to work on: started for, delegated in its requests, or claimed/updated
        # during the turn. Running alone does not say which owned task is executing.
        self.task_ids = request_task_ids(entry)
        self.steering = SteeringDelivery(self)
        self.interrupt = None
        self.reported_settings = {}
        self.reported_tokens = self.input_chars = self.estimated_input_tokens = 0
        self.coordination_calls = 0
        self.tools_listed = False
        self.native_tool_calls = 0
        self.native_tool_ids = set()
        self.stop_reason = ''
        self.deadline = None
        self.started_at = self.last_activity = 0
        self.limits = dict(manager.project_room.limits)
        self.automatic_tool_approvals = manager.cfg.get('runtime', {}).get('automatic_tool_approvals', False)
        self.instructions = (
            f"You are {name} in a real shared project chatroom. Reply as yourself. Other participants' "
            "messages are conversation context. Participate directly in the current chat. Mentions wake agents. "
            "No paid APIs, rental, credential reads, auth/settings changes, or recursive agent spawning. "
            "room_read retrieves older chat, optional specs, tasks, decisions and peer patches. "
            "room_task operates the user's kanban board: ownership, reserved paths, dependencies and progress. "
            "room_review tracks peer findings against a named revision; room_followup tracks next actions. "
            "Read room_read(section='inbox') at checkpoints and before finishing. room_update leaves durable notes. "
            "room_image views an uploaded image; room_markup shares a screenshot or uploaded image, optionally with "
            "numbered boxes, arrows and pins, so show visual work with mockups and result screenshots instead of only describing it. "
            "room_goal reads or finishes the user's shared goal. A project may have no Git repository; when the work needs "
            "one, room_repository asks the user to attach or create it. "
            "Do not run tests unless the human requests them or they resolve a concrete blocker. "
            "Use your native tools and skills for the requested work; do not restart existing model services "
            "or send messages through external apps unless the human explicitly asks. "
            f"Your working directory is {self.worktree}. "
            "Do not merge, commit or modify another agent's tree unless the human explicitly requests it. "
            "Tool actions remain subject to provider permissions.\n\n"
            + (ROOT / 'COLLABORATION.md').read_text('utf-8').strip()
        )
        profile = manager.profiles.items.get(name, {})
        self.instructions += f"\n\nYour project identity is {profile.get('name', name)} (@{name})."
        if profile.get('background'):
            self.instructions += '\nPersonal background and expert perspective supplied by the user:\n' + profile['background']
        self.instructions += '\nApply this perspective while collaborating with the team and following the current user request and room rules.'
        self.instruction_hash = hashlib.sha256(self.instructions.encode('utf-8')).hexdigest()
        self.previous_session = manager.project_room.previous_session(self)

    def install_steer(self, sender):
        self.steering.install(sender)

    async def steer_delivered(self, request):
        await self.steering.delivered(request)

    def begin(self, deadline):
        self.deadline = deadline
        self.started_at = time.monotonic()
        self.touch()

    def touch(self):
        self.last_activity = time.monotonic()
        if self.deadline is None or self.deadline.expired():
            return
        deadlines = []
        if self.limits['turn_seconds']:
            deadlines.append(self.last_activity + self.limits['turn_seconds'])
        if self.limits['turn_max_seconds']:
            deadlines.append(self.started_at + self.limits['turn_max_seconds'])
        self.deadline.reschedule(min(deadlines) if deadlines else None)

    def timeout_detail(self):
        total = self.limits['turn_max_seconds']
        if total and time.monotonic() >= self.started_at + total:
            return f'Turn exceeded the configured total duration of {total} seconds'
        return f'No visible agent activity for the configured {self.limits["turn_seconds"]} seconds'

    async def delta(self, text):
        self.touch()
        if not self.message_id:
            await self.begin_message(uuid.uuid4().hex[:12])
        self.text += text
        self.output_chars += len(text)
        if self.output_chars > 48000:
            raise RuntimeError("Response exceeded room output limit")
        await self.manager.emit("runtime_delta", {"run": self.id, "message": self.message_id,
            "phase": self.message_phase, "agent": self.name, "channel": self.channel, "text": text})

    async def begin_message(self, identifier, phase=None):
        if identifier in self.completed_messages:
            return False
        if self.message_id and self.message_id != identifier:
            await self.finish_message(phase='commentary')
        if self.message_id != identifier:
            self.message_phase = None
        self.message_id = identifier
        self.message_phase = phase or self.message_phase
        return True

    async def finish_message(self, identifier=None, text=None, phase=None, partial=False):
        if identifier and identifier in self.completed_messages:
            return
        if identifier and not self.message_id:
            await self.begin_message(identifier, phase)
        if phase:
            self.message_phase = phase
        if text is not None and not self.text:
            await self.delta(text)
        if not self.message_id:
            return
        identifier = self.message_id
        content = self.text.strip()
        if content:
            final = self.message_phase == 'final_answer' and not partial
            metadata = {'run_id': self.id, 'provider_message_id': identifier,
                'phase': self.message_phase or 'commentary', 'no_route': not final,
                'worktree': str(self.worktree), 'settings': self.settings,
                'reported_settings': self.reported_settings, 'chain': self.entry['chain']}
            if partial:
                metadata['outcome'] = 'partial'
            message = self.manager.host.store.add(self.name, content, channel=self.channel, metadata=metadata)
            self.published_messages.append(message['id'])
        self.completed_messages.add(identifier)
        self.text = ''
        self.message_id = self.message_phase = None
        await self.manager.emit('runtime_message_end', {'run': self.id, 'message': identifier,
            'agent': self.name, 'channel': self.channel})

    async def tool_event(self, name, params, status):
        self.touch()
        if self.text and self.message_phase != 'final_answer' and status in ('started', 'ACTIVE'):
            await self.finish_message(phase='commentary')
        if self.manager.states[self.name]['provider'] != 'api' and status in ('started', 'ACTIVE'):
            identifier = params.get('id', params.get('_room_step')) if isinstance(params, dict) else None
            if identifier is None or str(identifier) not in self.native_tool_ids:
                if identifier is not None:
                    self.native_tool_ids.add(str(identifier))
                self.native_tool_calls += 1
                if self.limits['native_tool_calls'] and self.native_tool_calls > self.limits['native_tool_calls']:
                    self.stop_reason = 'Native tool-call budget reached. Send a new instruction to continue this bounded task.'
                    raise RuntimeError(self.stop_reason)
        await self.manager.emit("runtime_tool", {"run": self.id, "agent": self.name, "channel": self.channel,
                                                  "tool": name, "status": status, "details": params}, persist=True)

    async def approval(self, tool, params, reason):
        if self.automatic_tool_approvals:
            await self.tool_event(tool, params, 'automatically approved')
            return True
        return await self.manager.ask(self, tool, params, reason)

    async def question(self, params):
        return await self.manager.ask(self, "Question", params, "Agent needs your answer", question=True)

    async def report_settings(self, model=None, effort=None):
        if model:
            self.reported_settings['model'] = model
        if effort:
            self.reported_settings['effort'] = effort
        await self.manager.emit('runtime_config', {'run': self.id, 'agent': self.name,
            'requested': self.settings, 'reported': dict(self.reported_settings)}, persist=True)

    async def report_usage(self, usage):
        self.touch()
        if isinstance(usage, dict):
            values = self.manager.project_room.record_usage(self, usage)
            if self.manager.goals.goal:
                self.manager.goals.save()
            await self.manager.emit('runtime_usage', {'run': self.id, 'agent': self.name,
                'channel': self.channel, 'input_chars': self.input_chars,
                'estimated_room_input_tokens': self.estimated_input_tokens, **values}, persist=True)

    async def report_permissions(self, effective):
        self.manager.states[self.name]['permissions'] = effective
        await self.manager.emit('runtime_permissions', {'run': self.id, 'agent': self.name,
            'automatic_tool_approvals': self.automatic_tool_approvals, 'effective': effective}, persist=True)


class RoomRuntime:
    def __init__(self, host):
        self.host = host
        self.cfg = host.config
        self.data = (ROOT / self.cfg["server"].get("data_dir", "data")).resolve()
        self.states, self.queues, self.workers, self.running, self.approvals = {}, {}, {}, {}, {}
        self.loop = None
        self.paused = False
        self.project = None
        self.identity = None
        self.audit = self.data / "runtime_events.jsonl"
        self.controls = ProviderControls(self)
        self.project_room = ProjectRoom(self)
        self.goals = RoomGoals(self)
        self.profiles = AgentProfiles(self)
        self.team_builder = TeamBuilder(self)
        self.profile_lock = asyncio.Lock()
        self.deploy_held = {}
        self.deployment = None

    def handles(self, name):
        return name in self.states

    def parallel_capacity(self):
        return self.project_room.limits['parallel_agents'] or max(1, sum(not s.get('archived') for s in self.states.values()))

    def snapshot(self):
        return {"agents": {n: {**{k: v for k, v in s.items() if k != "task"}, "queued": self.queues[n].qsize() + int(s.get('waiting', False)),
                              "controls": self.controls.describe(n)} for n, s in self.states.items()}, "project": str(self.project or ""), "paused": self.paused,
                "automatic_tool_approvals": self.cfg.get('runtime', {}).get('automatic_tool_approvals', False),
                "limits": {**self.project_room.limits, "parallel_agents_effective": self.parallel_capacity(), "queue_per_agent": 4},
                "budgets": list(self.project_room.chains.values())[-12:], "goal": self.goals.goal,
                "approvals": [a["display"] for a in self.approvals.values()],
                "runs": [{"id": r.id, "agent": r.name, "channel": r.channel, "text": r.text,
                          "message": r.message_id, "phase": r.message_phase,
                          "steering": [{'message_id': q.get('message_id'), 'status': q.get('_steering')}
                                       for q in r.steering.pending[-8:]],
                          "settings": r.settings, "reported_settings": r.reported_settings,
                          "reported_tokens": r.reported_tokens, "input_chars": r.input_chars} for r in self.running.values()]}

    async def emit(self, kind, data, persist=False):
        if persist:
            with self.audit.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"type": kind, "time": time.time(), "data": data}, ensure_ascii=False) + "\n")
        await self.host._broadcast(json.dumps({"type": kind, "data": data}, ensure_ascii=False))

    async def publish_status(self):
        await self.emit("runtime_state", self.snapshot())
        await self.host.broadcast_status()
        if self.project_room.path is not None:
            # Task execution states (running, queued, idle) follow turns, not only board edits.
            await self.emit('project_state', self.project_room.snapshot())

    def publish_soon(self):
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(self.publish_status())

    def save_project(self):
        for name, state in self.states.items():
            if name in self.profiles.items:
                self.profiles.items[name]['paused'] = state.get('paused', False)
        if self.profiles.path:
            self.profiles.save()
        save_workspace(self.data / "workspace.json", {"project": str(self.project) if self.project else None,
              "project_id": self.identity, "paused": self.paused,
              "paused_agents": [n for n, s in self.states.items() if s.get("paused")],
              "worktrees": {n: s["worktree"] for n, s in self.states.items()}})

    def setup_project(self, project, identity=None):
        """Activate a project. identity keeps a project's board, team and chats when its
        repository is added later; without one, the repository path selects the project."""
        if project:
            project = project_registry.repository_root(project)
        if identity is None:
            identity = project_registry.identity_for(self, project)
        self.project = project
        self.identity = identity
        self.project_room.use_project(identity)
        self.goals.load()
        for worker in self.workers.values():
            worker.cancel()
        for name in list(self.states):
            self.host.registry.deactivate_managed(name)
            self.cfg['agents'].pop(name, None)
        self.states.clear(); self.queues.clear(); self.workers.clear()
        self.controls.values.clear(); self.controls.catalogs.clear()
        self.profiles.load(identity)
        self.team_builder.load()
        for profile in self.profiles.items.values():
            workspace = self.workspace_for(profile['id'])
            self.activate_profile(profile, workspace)
        self.save_project()
        project_registry.remember(self)
        project_registry.ensure_session(self)
        if project_registry.sync_channels(self):
            self.host._save_settings()

    def owns_channel(self, channel):
        """Only the active project's own chats are writable and routed to its agents."""
        return channel in project_registry.owned(self)

    def default_channel(self):
        return project_registry.default_session(self)

    def channel_title(self, channel):
        title = next((s.get('title') for s in project_registry.sessions(project_registry.active_folder(self))
                      if s['id'] == channel), None) if self.project_room.path is not None else None
        return f'"{title}" (#{channel})' if title and title != channel else f'#{channel}'

    def workspace_for(self, name):
        if self.deployment and self.deployment.rehearsal:
            path = self.data / 'rehearsal-workspaces' / self.identity / name
            path.mkdir(parents=True, exist_ok=True)
            return dict(worktree=str(path), branch='', workspace_kind='conversation')
        if not self.project:
            # Each conversation project keeps its agents' scratch folders apart.
            base = self.data / 'chat' if self.identity == 'chat' else self.data / 'conversations' / self.identity
            path = base / name
            path.mkdir(parents=True, exist_ok=True)
            return dict(worktree=str(path), branch='', workspace_kind='conversation')
        identity = self.profiles.path.parent.name
        path = self.data / 'worktrees' / identity / name
        if not (path / '.git').exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            git(self.project, 'worktree', 'add', '-b', f'chatroom/{identity}/{name}-{uuid.uuid4().hex[:6]}', str(path), 'HEAD')
        return dict(worktree=str(path), branch=git(path, 'branch', '--show-current'), workspace_kind='repository')

    def activate_profile(self, profile, workspace):
        name = profile['id']
        cfg = self.profiles.config(profile)
        cfg['avatar_base'] = profile['template']
        self.cfg['agents'][name] = cfg
        self.states[name] = {'provider': cfg.get('provider', profile['template']), 'template': profile['template'],
            'label': profile['name'], 'color': profile['color'], 'archived': profile.get('archived', False),
            'state': 'ready', 'detail': '', 'paused': profile.get('paused', False) or profile.get('archived', False), **workspace}
        self.controls.register(name, cfg, restore_saved=False)
        peer = next((n for n, p in self.profiles.items.items() if n != name and p['template'] == profile['template']
                     and self.controls.catalogs.get(n, {}).get('models')), None)
        if peer:
            self.controls.catalogs[name] = dict(self.controls.catalogs[peer])
        if profile.get('archived'):
            self.host.registry.deactivate_managed(name)
        else:
            self.host.registry.register_managed(name, cfg)
        self.queues[name] = WakeQueue(maxsize=4)
        self.workers[name] = asyncio.create_task(self.worker(name))

    async def change_profile(self, body, name='', creation_key='', expected_project=None):
        async with self.profile_lock:
            if expected_project is not None and self.identity != expected_project:
                raise ValueError('Project changed; reload the team builder')
            if creation_key:
                existing = next((p for p in self.profiles.items.values()
                                 if p.get('team_builder_key') == creation_key), None)
                if existing:
                    # Creation may have succeeded before the proposal result was saved.
                    self.save_project()
                    return existing
            profile = self.profiles.prepare(body, name)
            if creation_key:
                profile['team_builder_key'] = creation_key
            name = profile['id']
            if name not in self.states:
                workspace = self.workspace_for(name)
                self.profiles.items[name] = profile
                try:
                    self.activate_profile(profile, workspace)
                except Exception:
                    if creation_key:
                        # Failed activation must not look like an idempotent success on retry.
                        self.profiles.items.pop(name, None)
                        worker = self.workers.pop(name, None)
                        if worker:
                            worker.cancel()
                        self.queues.pop(name, None)
                        self.states.pop(name, None)
                        self.cfg['agents'].pop(name, None)
                        self.controls.values.pop(name, None)
                        self.controls.catalogs.pop(name, None)
                        self.host.registry.deactivate_managed(name)
                    raise
            else:
                self.profiles.items[name] = {**self.profiles.items[name], **profile}
                cfg = self.profiles.config(profile); cfg['avatar_base'] = profile['template']
                self.cfg['agents'][name] = cfg
                self.states[name].update(label=profile['name'], color=profile['color'])
                self.controls.values[name] = {'model': profile['model'], 'effort': profile['effort']}
                if not profile.get('archived'):
                    self.host.registry.register_managed(name, cfg)
            self.save_project()
            await self.host.broadcast_agents()
            await self.publish_status()
            return profile

    async def archive_profile(self, name, archived):
        async with self.profile_lock:
            if name not in self.profiles.items:
                raise ValueError('Agent profile was not found')
            if archived:
                owned = [t['id'] for t in self.project_room.state['tasks'] if t['owner'] == name and t['status'] != 'done']
                if owned:
                    raise ValueError('Transfer or release owned tasks before archiving: ' + ', '.join(owned))
                await self.stop_agent(name)
                self.host.registry.deactivate_managed(name)
            else:
                cfg = self.profiles.config(self.profiles.items[name]); cfg['avatar_base'] = self.profiles.items[name]['template']
                self.host.registry.register_managed(name, cfg)
                # Restoring a profile does not start it or unpause the room.
                self.states[name]['paused'] = True
            self.profiles.items[name]['archived'] = archived
            self.states[name]['archived'] = archived
            self.save_project()
            await self.host.broadcast_agents()
            await self.publish_status()
            return self.profiles.items[name]

    async def start(self):
        self.loop = asyncio.get_running_loop()
        self.data.mkdir(parents=True, exist_ok=True)
        saved = load_workspace(self.data / "workspace.json")
        self.paused = saved.get("paused", False)
        project = saved.get('project')
        if project:
            path = Path(project).resolve()
            old_sandboxes = {self.data / 'project', self.data / 'project-native'}
            if path in old_sandboxes and (path / 'README.md').is_file() and (path / 'README.md').read_text('utf-8').strip() == '# Shared project sandbox\n\nA disposable project for trying the AI room.':
                # Retire the automatic demo selection, preserving its files and worktrees.
                project = None
        project_registry.migrate_sessions(self)
        identity = saved.get('project_id')
        if not (identity and project_registry.IDENTITY.match(identity) and (self.data / 'projects' / identity).is_dir()):
            identity = None
        self.setup_project(project, identity)
        for name in saved.get('paused_agents', []):
            if name in self.states:
                self.states[name]['paused'] = True
        self.save_project()
        self.restore_deploy_queue()
        self.heartbeat = asyncio.create_task(self.beat())
        await self.refresh()

    async def refresh(self):
        if self.deployment and (self.deployment.rehearsal or self.deployment.reload()['writes'] == 'blocked'):
            self.deploy_refresh_pending = not self.deployment.rehearsal
            for state in self.states.values():
                state.update(state='ready', detail='Rehearsal: provider checks and agent turns disabled' if self.deployment.rehearsal else 'Deployment verification: agent turns are held')
            await self.publish_status()
            return
        for name, state in list(self.states.items()):
            if name in self.running:
                continue
            provider = state["provider"]
            try:
                if provider == "api":
                    cfg = self.cfg["agents"][name]
                    url = cfg["base_url"].rstrip("/") + "/models"
                    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                        response = await client.get(url)
                        response.raise_for_status()
                        ids = [m["id"] for m in response.json().get("data", [])]
                        model = self.controls.values[name]['model']
                        if model not in ids:
                            raise RuntimeError(f"Selected model {model} is not served")
                    state.update(state="ready", detail=f"Local model {model} · scoped file tools · shell disabled")
                else:
                    executable = native.command(provider)
                    if provider == "claude":
                        proc = await asyncio.create_subprocess_exec(*executable, "auth", "status", env=native.subscription_env(),
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
                        try:
                            raw, _ = await asyncio.wait_for(proc.communicate(), 10)
                        finally:
                            if proc.returncode is None:
                                proc.kill()
                                await proc.wait()
                        auth = json.loads(raw)
                        if not auth.get("loggedIn"):
                            state.update(state="login_required", detail='Run claude auth login in your own terminal, then click Refresh connections.')
                            continue
                    state.update(state="ready", detail="Official CLI detected · live session checked when mentioned")
            except Exception as exc:
                state.update(state="blocked", detail=str(exc)[:600])
        # One catalog lookup per configured provider connection, shared by its profiles.
        groups = {}
        for name, state in self.states.items():
            if name not in self.running:
                groups.setdefault(state['template'], []).append(name)
        names = [group[0] for group in groups.values()]
        results = await asyncio.gather(*(self.controls.refresh(name) for name in names))
        for name, connected in zip(names, results):
            for peer in groups[self.states[name]['template']]:
                self.controls.catalogs[peer] = dict(self.controls.catalogs[name])
                if not connected and self.states[peer]['provider'] == 'antigravity':
                    self.states[peer].update(state='blocked', detail=self.controls.catalogs[name]['error'])
        self.deploy_refresh_pending = False
        await self.publish_status()

    async def resume_deploy_checks(self):
        if not self.deployment or not self.deployment.admission_open():
            return
        # Startup already consumed matching wakes. Mismatched/aborted checkpoints must
        # not survive to replay on a later ordinary start or project switch.
        (self.deployment.root / 'pending-wakes.json').unlink(missing_ok=True)
        self.deploy_checkpoint_restored = False
        if getattr(self, 'deploy_refresh_pending', False) and self.deployment.enter_mutation():
            try:
                async with self.profile_lock:
                    await self.refresh()
            finally:
                self.deployment.leave_mutation()

    def turn_admission_open(self):
        return (not self.deployment or self.deployment.admission_open()) and not getattr(self, 'deploy_refresh_pending', False)

    async def beat(self):
        import mcp_bridge
        while True:
            try:
                await self.resume_deploy_checks()
            except Exception:
                LOGGER.exception('Post-deployment provider refresh failed; turns remain held')
            if self.deployment and self.deployment.reload()["writes"] == "blocked":
                await asyncio.sleep(.25)
                continue
            for name, state in self.states.items():
                if state.get('archived'):
                    continue
                # The manager owns this identity even while a provider needs login.
                mcp_bridge._touch_presence(name)
                mcp_bridge.set_active(name, name in self.running)
            await asyncio.sleep(3)

    def enqueue_sync(self, *args):
        if self.deployment:
            self.deployment.track_continuation()
        def deliver():
            try:
                self.enqueue(*args)
            finally:
                if self.deployment:
                    self.deployment.leave_mutation()
        try:
            self.loop.call_soon_threadsafe(deliver)
        except RuntimeError:
            if self.deployment:
                self.deployment.leave_mutation()
            raise

    def routing_chain(self, sender, channel, metadata=None, text=''):
        if sender in self.states:
            identifier = (metadata or {}).get('chain') or self.project_room.chain_for(channel)
            return identifier
        return self.project_room.new_chain(channel)

    def enqueue(self, name, channel="general", message="", prompt="", chain=None, routing=None, ref=None):
        if self.deployment and self.deployment.rehearsal:
            return False
        if not self.owns_channel(channel):
            # Every wake path (mentions, goals, delegation, task starts) ends here; agents only work
            # in the active project's own chats, never in another project's or legacy history.
            self.host.store.add("system", f"{name} was not woken: #{channel} is not a chat in this project.",
                                channel=self.default_channel(), metadata={'no_route': True})
            return False
        if self.paused:
            self.host.store.add("system", f"Room is stopped. Click Resume before waking {name}.", channel=channel)
            return
        if self.states[name].get("paused"):
            self.host.store.add("system", f"{name} is stopped. Click its Resume agent button before waking it.", channel=channel)
            return
        if self.states[name]["state"] in ("login_required", "blocked"):
            self.host.store.add("system", f"{name} cannot start: {self.states[name]['detail']}", channel=channel)
            return
        chain = chain or self.project_room.chain_for(channel)
        request = {'prompt': prompt or message, 'chain': chain, 'human': ref is None,
                   **(routing or {})}
        if ref:
            request.update(ref=ref, human=False)
        try:
            self.queues[name].put_nowait({'channel': channel, 'chain': chain,
                                         'requests': [request]})
            run = self.running.get(name)
            if run and run.channel == channel:
                run.steering.offer(request)
            self.publish_soon()
            return True
        except asyncio.QueueFull:
            self.host.store.add("system", f"{name}'s pending channel queue is full; this request was refused.", channel=channel)
            return False

    def diffs(self):
        if not self.project:
            return {}
        result = {}
        for name, state in self.states.items():
            if not state.get("worktree"):
                continue
            path = Path(state["worktree"])
            exclusions = [":(exclude)" + name for name in PRIVATE_PARTS]
            exclusions += [":(exclude)" + name + "/**" for name in PRIVATE_PARTS]
            diff = git(path, "diff", "HEAD", "--", ".", ":(exclude).env.*", *exclusions)
            # The temporary Gemini MCP connection is runtime metadata, not project work.
            if self.states[name]['provider'] == 'antigravity':
                diff = git(path, 'diff', 'HEAD', '--', '.', ':(exclude).env.*', ':(exclude).agents/mcp_config.json', *exclusions)
            untracked = git(path, "ls-files", "--others", "--exclude-standard").splitlines()
            for filename in untracked[:8]:
                if filename.replace('\\', '/') == '.agents/mcp_config.json':
                    continue
                try:
                    file = scoped_path(path, filename)
                    if file.stat().st_size <= 24000:
                        diff += f"\nNew file {filename}:\n" + file.read_text("utf-8")
                except (ValueError, UnicodeError, OSError):
                    continue
            result[name] = diff[:24000]
        return result

    def context(self, run):
        return self.project_room.context(run) + self.goals.context(run)

    async def worker(self, name):
        while True:
            entry = await self.queues[name].get()
            self.deploy_held[name] = entry
            while not self.turn_admission_open():
                await asyncio.sleep(.1)
            self.deploy_held.pop(name, None)
            if self.paused or self.states[name].get("paused") or self.states[name]["state"] in ("blocked", "login_required"):
                continue
            self.deploy_held[name] = entry
            task = asyncio.create_task(self.run_turn(name, entry))
            self.states[name]["task"] = task  # removed from public state below
            try:
                await task
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
            except Exception:
                LOGGER.exception('Turn admission failed; worker remains available')
                with contextlib.suppress(Exception):
                    self.goals.pause('Turn admission failed; inspect server logs before resuming.')
            finally:
                self.states[name].pop("task", None)
                self.deploy_held.pop(name, None)
                try:
                    # A finishing turn may queue the goal's next round while admission is
                    # closed. The worker holds it and the deployment checkpoint preserves it.
                    if not self.deployment or self.deployment.reload()['writes'] == 'open':
                        self.goals.schedule()
                    await self.publish_status()
                except Exception:
                    LOGGER.exception('Goal scheduling/status failed; worker remains available')
                    with contextlib.suppress(Exception):
                        self.goals.pause('Goal scheduling failed; inspect server logs before resuming.')

    async def run_turn(self, name, entry):
        self.states[name]['waiting'] = True
        admitted = False
        try:
            await self.publish_status()
            while len(self.running) >= self.parallel_capacity() or not self.turn_admission_open():
                await asyncio.sleep(.2)
            if self.paused or self.states[name].get('paused') or self.states[name]['state'] in ('blocked', 'login_required'):
                return
            self.queues[name].merge_waiting(entry)
            receipts = self.project_room.state.get('receipts', {}).get(name + ':' + entry['channel'], [])
            requests = [r for r in entry['requests'] if not r.get('_steer_delivered') and current_request(r, name, self.project_room.state, receipts)
                        and self.goals.accepts(r) and self.team_builder.accepts(r)]
            if not requests:
                await self.emit('runtime_queue_pruned', {'agent': name, 'channel': entry['channel'],
                    'reason': 'Resolved, superseded or already delivered requests'}, persist=True)
                return
            humans = [r for r in requests if r.get('human')]
            # Only agent chat @mentions count as handoffs. Reviews, follow-ups and delegations carry a
            # tracked record (ref); goals and team-builder runs carry their own token and controls.
            handoffs = [r for r in requests if not r.get('human') and not r.get('ref')
                        and not r.get('goal_token') and not r.get('team_builder')]
            entry['chain'] = (humans or requests)[-1]['chain']
            if not humans and handoffs and self.project_room.handoffs_spent(entry['chain']):
                limit = self.project_room.limits['chat_handoffs']
                self.host.store.add('system', f'{name} was not woken by a chat @mention: agents have handed off {limit} times '
                                    'since your last message. Reviews and follow-ups still go through. Send a message to continue, '
                                    'or raise or turn off "Chat @mention handoffs" in Board > Limits & usage.', channel=entry['channel'])
                requests = [r for r in requests if r not in handoffs]
                handoffs = []
                if not requests:
                    return
            entry['requests'] = requests
            entry['prompt'] = '\n\n'.join(r['prompt'] for r in requests)
            if len(requests) > 1:
                entry['prompt'] = 'Pending updates (oldest to newest; later human direction takes precedence):\n' + entry['prompt']
            if not self.project_room.permit(entry['chain']):
                if any(r.get('goal_token') for r in requests):
                    self.goals.pause('Conversation turn or token budget reached.')
                self.host.store.add('system', 'Conversation budget reached. Send a new instruction to continue this project.', channel=entry['channel'])
                return
            if not humans and handoffs:
                self.project_room.handoff(entry['chain'])
            if self.goals.goal:
                self.goals.save()
            admitted = True
        finally:
            self.states[name]['waiting'] = False
            if not admitted:
                self.team_builder.turn_finished(entry)
                await self.publish_status()
        self.deploy_held.pop(name, None)
        run = Run(self, name, entry)
        self.running[name] = run
        self.states[name]["state"] = "working"
        outcome = "completed"
        await self.emit("runtime_start", {"run": run.id, "agent": name, "channel": run.channel, "settings": run.settings}, persist=True)
        await self.publish_status()
        try:
            prompt = self.context(run)
            async with asyncio.timeout(None) as deadline:
                run.begin(deadline)
                provider = self.states[name]["provider"]
                if provider == "api":
                    await self.local_turn(run, prompt)
                else:
                    await getattr(native, provider)(run, prompt)
            await run.finish_message(phase=run.message_phase or 'final_answer')
            if not run.published_messages:
                raise RuntimeError("Provider completed without a response")
            self.project_room.complete(run)
            self.states[name].update(state="ready", detail="Live turn completed")
            self.check_room_tools(run)
        except asyncio.CancelledError:
            outcome = "cancelled"
            if self.goals.goal and self.goals.goal['owner'] == name:
                self.goals.pause('Goal lead turn cancelled.')
            self.states[name].update(state="ready", detail="Stopped; pending work cleared")
            await run.finish_message(partial=True)
            self.host.store.add("system", f"{name} stopped.", channel=run.channel)
        except Exception as exc:
            outcome = "failed"
            if self.goals.goal and self.goals.goal['owner'] == name:
                self.goals.pause('Goal lead failed; inspect the agent status before resuming.')
            detail = run.stop_reason or str(exc) or type(exc).__name__
            if isinstance(exc, TimeoutError):
                pending = [a['display']['tool'] for a in self.approvals.values() if a['display']['run'] == run.id]
                detail = run.timeout_detail() + (f' while waiting for {", ".join(pending)}' if pending else '')
            if "disconnected" in detail.lower() and run.stderr:
                # Only textual diagnostics; redact credential-looking values if a CLI emits them.
                diagnostic = re.sub(r'(?i)(?:bearer\s+|(?:token|api[-_]?key|password|secret)\s*[=:]\s*)\S+', '[redacted]', run.stderr)
                diagnostic = re.sub(r'\b(?:sk-|hf_)[A-Za-z0-9_-]+', '[redacted]', diagnostic)
                detail += ": " + diagnostic[-1200:]
            self.states[name].update(state="blocked", detail=detail[:600])
            while not self.queues[name].empty():
                self.queues[name].get_nowait()
            self.host.store.add("system", f"{name} failed: {detail[:600]}", channel=run.channel)
            await run.finish_message(partial=True)
        finally:
            await run.steering.close(outcome)
            await native.terminate(run)
            native.restore_room_server(run)
            for identifier, approval in list(self.approvals.items()):
                if approval["display"]["run"] == run.id:
                    if not approval["future"].done():
                        approval["future"].cancel()
                    self.approvals.pop(identifier, None)
            self.running.pop(name, None)
            self.team_builder.turn_finished(entry)
            await self.emit("runtime_end", {"run": run.id, "agent": name, "outcome": outcome}, persist=True)
            await self.publish_status()

    async def coordination(self, run, tool, arguments):
        if tool == 'tools_listed':
            # The room MCP server reports that the provider loaded the tools; not an agent action.
            run.tools_listed = True
            return {'ok': True}
        run.touch()
        run.coordination_calls += 1
        if tool == 'room_team_builder':
            action = arguments.get('action', 'get')
            if action == 'get':
                return self.team_builder.context()
            if action != 'submit':
                raise ValueError('Agents may only get or submit team proposals; users approve and add members')
            async with self.team_builder.lock:
                result = self.team_builder.submit(run.name, arguments.get('proposal_id'),
                    arguments.get('revision'), arguments.get('payload'))
            await self.emit('team_builder', result)
            return result
        if tool == 'room_goal':
            result = self.goals.change(run.name, **arguments)
            await self.publish_status()
            return result
        if tool == 'room_markup':
            result = self.project_room.markup(run.name, run.channel, run.entry['chain'], **arguments)
            await self.emit('project_state', self.project_room.snapshot())
            return result
        if tool == 'room_read':
            return self.project_room.read(run, **arguments)
        if tool == 'room_repository':
            result = project_registry.request_repository(self, run.name, **arguments)
            action = 'create a new repository at' if result['create'] else 'attach the repository'
            self.host.store.add(run.name, f"Repository request: {action} {result['path']}. {result['reason']}\nThe user can approve it in Projects.",
                                channel=run.channel, metadata={'no_route': True, 'chain': run.entry['chain']})
            await self.emit('project_state', self.project_room.snapshot())
            return {**result, 'status': 'Waiting for the user; attaching stops the room, so keep working without it.'}
        if tool == 'room_task':
            before = next((t['status'] for t in self.project_room.state['tasks'] if t['id'] == arguments.get('task_id')), None)
            result = self.project_room.task(run.name, **arguments)
            if arguments.get('action') == 'get':
                return result
            if result.get('owner') == run.name or (result.get('transfer') or {}).get('to') == run.name:
                run.task_ids.add(result['id'])
            if arguments.get('action') == 'delegate':
                # A delegation stays with the current owner until the recipient accepts the transfer.
                transfer = result.get('transfer') or {}
                recipient = transfer.get('to') or result['owner']
                accept = (f"\n{result['owner'] or 'Nobody'} keeps ownership until you accept with room_task(action='accept', task_id='{result['id']}')."
                          if transfer else '')
                delivery = self.notify_participant(recipient, run.channel, run.entry['chain'],
                    f"Own task {result['id']}: {result['title']}\n{result['details']}\nReserved paths: {', '.join(result['paths'])}{accept}\nRead your inbox and report progress via room tools.",
                    {'collection': 'tasks', 'id': result['id'], 'updated': result['updated'], 'action': 'delegate'})
                result = {**result, 'delivery': delivery}
            if result['status'] == 'done' and before not in (None, 'done'):
                ready = self.dependency_ready(result['id'], run.channel, run.entry['chain'], actor=run.name)
                if ready:
                    result = {**result, 'dependents_notified': ready}
            text = f"Task {result['id']}: {result['title']} | owner {result['owner'] or 'unclaimed'} | {result['status']}"
        elif tool == 'room_update':
            result = self.project_room.note(run.name, **arguments)
            text = f"{result['kind']}: {result['text']}"
        elif tool in ('room_review', 'room_followup'):
            return await self.coordination_record(run.name, tool, arguments, run.channel, run.entry['chain'])
        else:
            raise ValueError('Unknown coordination tool')
        self.host.store.add(run.name, text, channel=run.channel, metadata={'no_route': True, 'chain': run.entry['chain']})
        await self.emit('project_state', self.project_room.snapshot())
        return result

    def check_room_tools(self, run):
        """The room sees room tools load (the MCP server reports a listing) or get used. A turn with neither
        is reported as unconfirmed, once per change: the listing report is best effort, so its absence
        is not proof the tools failed."""
        state = self.states[run.name]
        if state.get('provider') not in ROOM_TOOL_PROVIDERS:
            return
        observed = run.tools_listed or run.coordination_calls > 0
        previous = state.get('room_tools')
        state['room_tools'] = 'observed' if observed else 'unconfirmed'
        if not observed and previous != 'unconfirmed':
            self.host.store.add('system', f"No room-tool listing or room-tool calls were observed from {run.name} in this turn, so the room "
                                f"can't confirm its tools loaded. If {run.name} reports missing tools, check the {state.get('provider')} "
                                "CLI's MCP status.", channel=run.channel, metadata={'no_route': True})
        elif observed and previous == 'unconfirmed':
            self.host.store.add('system', f"{run.name}'s room tools were observed again.", channel=run.channel, metadata={'no_route': True})

    def dependency_ready(self, finished, channel, chain, actor=''):
        """Finishing a task turns waiting downstream work into a concrete next action for its owner."""
        tasks = self.project_room.state['tasks']
        done = {t['id'] for t in tasks if t['status'] == 'done'}
        deliveries = {}
        for task in tasks:
            owner = task.get('owner')
            if (finished not in task.get('depends_on', []) or task['status'] == 'done' or not owner or owner == actor
                    or owner not in self.states or not set(task['depends_on']) <= done):
                continue
            if pending_human_wait(task, self.project_room.state):
                continue  # still gated by the user; finishing a dependency doesn't change that
            ask = ("It is marked blocked: check whether these dependencies were the block, then continue it or record what it still waits for."
                   if task['status'] == 'blocked' else 'Continue it now, or record on the task what it is waiting for.')
            deliveries[task['id']] = {'owner': owner, **self.notify_participant(owner, channel, chain,
                f"All dependencies of your task {task['id']} ({task['title']}) are done; {finished} just finished.\n"
                f"Next action on record: {task.get('next_action') or 'none recorded'}.\n" + ask,
                {'collection': 'tasks', 'id': task['id'], 'updated': task['updated'], 'action': 'ready'})}
        return deliveries

    def activity(self, name):
        """What the runtime is doing for an agent right now: a running turn, and turns waiting to start
        (queued, or taken by the worker and held for capacity). A 'working' task with neither is idle."""
        run = self.running.get(name)
        queue = self.queues.get(name)
        waiting = list(queue._queue) if queue else []
        if name in self.deploy_held:
            waiting.append(self.deploy_held[name])
        return {'running': bool(run), 'channel': run.channel if run else None, 'queued': len(waiting),
                'running_task_ids': sorted(run.task_ids) if run else [],
                'queued_task_ids': sorted(set().union(*(request_task_ids(e) for e in waiting)))}

    def notify_participant(self, name, channel, chain, prompt, ref):
        # Durable records exist before delivery; a refused wake-up must not lose the request.
        # status: queued (a turn will start, or the request joins the recipient's running turn), deferred
        # (not woken now; the record waits in their inbox) or dropped (not woken; resend later).
        state = self.states.get(name)
        kept = 'The record stays in their inbox.'
        if name == 'user':
            return {'status': 'deferred', 'queued': False, 'reason': 'Visible in the room for the user; nothing wakes the user.'}
        if not state:
            return {'status': 'dropped', 'queued': False, 'reason': f'{name} is not a participant in this room. {kept}'}
        if self.paused:
            return {'status': 'deferred', 'queued': False, 'reason': f'The room is stopped, so {name} was not woken. {kept}'}
        if state.get('paused'):
            return {'status': 'deferred', 'queued': False, 'reason': f'{name} is stopped by the user, so was not woken. {kept}'}
        if state['state'] in ('blocked', 'login_required'):
            return {'status': 'deferred', 'queued': False, 'reason': f"{name} cannot start ({state.get('detail') or state['state']}). {kept}"}
        before = self.activity(name)
        run = self.running.get(name)
        queue = self.queues.get(name)
        joins = any(p['channel'] == channel for p in (queue._queue if queue else ())) or \
            (self.deploy_held.get(name) or {}).get('channel') == channel
        queued = self.enqueue(name, channel, prompt=prompt, chain=chain, ref=ref)
        if not queued:
            return {'status': 'dropped', 'queued': False, 'reason': f"{name}'s wake queue is full or this chat is not in the project, "
                    f'so no turn was started. {kept} Resend the notification after their current work finishes.'}
        if run and run.channel == channel and run.steering.sender and not run.steering.closed:
            # Acceptance is only known when the provider confirms it (runtime_steering 'accepted'), so not claimed here.
            return {'status': 'queued', 'queued': True, 'reason': f'{name} is working in this chat; the request is sent into its '
                    'running turn at its next step, or starts a turn right after it.'}
        if joins:
            return {'status': 'queued', 'queued': True, 'reason': f'Added to the turn already waiting to start for {name} in this chat.'}
        if before['running']:
            where = 'this chat' if before['channel'] == channel else 'another chat'
            wait = f"after its current turn in {where}"
        elif before['queued']:
            wait = f"after {before['queued']} turn(s) already waiting"
        elif len(self.running) >= self.parallel_capacity():
            wait = f"when one of the {self.parallel_capacity()} parallel turn slots frees"
        else:
            wait = 'now'
        return {'status': 'queued', 'queued': True, 'reason': f'A turn for {name} starts {wait}.'}

    async def coordination_record(self, actor, tool, arguments, channel, chain):
        arguments = dict(arguments)
        notify = arguments.pop('notify', True)
        method = self.project_room.records.review if tool == 'room_review' else self.project_room.records.followup
        result = method(actor, **arguments)
        if not result['changed']:
            return {'record': result['item'], 'changed': False, 'deliveries': {}}
        item = result['item']
        item.setdefault('channel', channel)
        self.project_room.save()
        text = result['summary']
        ref = {'collection': 'reviews' if tool == 'room_review' else 'followups',
               'id': item['id'], 'updated': item['updated'], 'action': arguments['action']}
        self.host.store.add(actor, text, channel=channel, metadata={'no_route': True, 'chain': chain, 'coordination_ref': ref})
        deliveries = {}
        if notify:
            for recipient in dict.fromkeys(result['notify']):
                if recipient != actor:
                    deliveries[recipient] = self.notify_participant(recipient, channel, chain,
                        text + '\nRead room_read(section="inbox") for the full request/findings and coordinate with the team.', ref)
        await self.emit('project_state', self.project_room.snapshot())
        return {'record': item, 'changed': True, 'deliveries': deliveries}

    async def ask(self, run, tool, params, reason, question=False):
        identifier = uuid.uuid4().hex
        future = self.loop.create_future()
        display = {"id": identifier, "run": run.id, "agent": run.name, "channel": run.channel,
                   "tool": tool, "params": params, "reason": reason, "worktree": str(run.worktree), "question": question}
        self.approvals[identifier] = {"future": future, "display": display}
        await run.tool_event(tool, params, "awaiting approval")
        await self.emit("runtime_approval", display, persist=True)
        try:
            return await asyncio.wait_for(future, 120)
        except asyncio.TimeoutError:
            return "" if question else False
        finally:
            self.approvals.pop(identifier, None)
            await self.emit("runtime_approval_resolved", {"id": identifier})

    async def decide(self, identifier, allowed, answer=""):
        approval = self.approvals.get(identifier)
        if not approval or approval["future"].done():
            raise ValueError("This approval expired or was already answered")
        approval["future"].set_result(answer if approval["display"]["question"] else allowed)
        await self.emit("runtime_decision", {"id": identifier, "allowed": allowed}, persist=True)

    async def local_turn(self, run, prompt):
        cfg = self.cfg["agents"][run.name]
        base = cfg["base_url"].rstrip("/")
        tools = local_tool_definitions() if cfg.get("tools", True) else []
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=5), trust_env=False) as client:
            if cfg.get("tools", True) and cfg.get("tool_schema_url"):
                response = await client.get(cfg["tool_schema_url"])
                response.raise_for_status()
                tools = [t["definition"] for t in response.json() if t.get("tool") in FILE_TOOLS]
            tools = tools + coordination_tools() if cfg.get('tools', True) else []
            messages = [{"role": "system", "content": run.instructions + "\nLocal file tools run only in your working directory. Shell execution is unavailable. Tool arguments must match their advertised schemas."},
                        {"role": "user", "content": prompt}]
            rounds = run.limits['local_tool_rounds']
            for turn in range(rounds + 1):
                payload = {"model": run.settings['model'], "messages": messages, "stream": True,
                           'stream_options': {'include_usage': True},
                           "max_tokens": min(2048, int(cfg.get("max_tokens", 1024))), "temperature": 0.4}
                if tools:
                    payload["tools"] = tools
                    # Keep the server's structured tool parser active on every round.
                    # The execution guard below bounds calls without changing their protocol.
                    payload["tool_choice"] = "auto"
                payload.update(cfg.get("request_options", {}))
                payload['model'] = run.settings['model']
                payload.pop('reasoning_effort', None)
                if run.settings['effort']:
                    payload['reasoning_effort'] = run.settings['effort']
                    payload['chat_template_kwargs'] = {**payload.get('chat_template_kwargs', {}), 'reasoning_effort': run.settings['effort']}
                await run.report_settings()
                calls, text = {}, ""
                finish_reason = None
                async with client.stream("POST", base + "/chat/completions", json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                            continue
                        item = json.loads(line[5:])
                        if item.get('usage'):
                            usage = item['usage']
                            previous = getattr(run, 'local_reported_tokens', 0)
                            total = usage.get('total_tokens', usage.get('prompt_tokens', 0) + usage.get('completion_tokens', 0))
                            await run.report_usage({'total_tokens': previous + total})
                            run.local_reported_tokens = previous + total
                        if item.get('model') and run.reported_settings.get('model') != item['model']:
                            await run.report_settings(model=item['model'])
                        for choice in item.get("choices", []):
                            finish_reason = choice.get('finish_reason') or finish_reason
                            delta = choice.get("delta", {})
                            if delta.get("content"):
                                text += delta["content"]
                                if not tools:
                                    await run.delta(delta["content"])
                            for call in delta.get("tool_calls", []):
                                part = calls.setdefault(call.get("index", 0), {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                                if call.get("id"):
                                    part["id"] = call["id"]
                                for key in ("name", "arguments"):
                                    if call.get("function", {}).get(key):
                                        part["function"][key] += call["function"][key]
                clean = visible_text(text)
                if finish_reason == 'length' and calls:
                    raise RuntimeError('Local tool call was truncated; no incomplete call was executed')
                if tools and clean:
                    # Hold tool-enabled content until classified, so malformed protocol
                    # and reasoning are never streamed into ordinary chat.
                    await run.delta(clean + ('\n' if calls else ''))
                if not calls:
                    return
                await run.finish_message(phase='commentary')
                if turn == rounds or len(calls) > 6:
                    raise RuntimeError("Local tool budget exceeded")
                structured = list(calls.values())
                for call in structured:
                    call['id'] = call.get('id') or 'call_' + uuid.uuid4().hex
                messages.append({"role": "assistant", "content": clean or None, "tool_calls": structured})
                for call in structured:
                    name, params = call['function']['name'], {}
                    result = "Denied"
                    try:
                        call, name, params = validate_calls([call], tools)[0]
                        if name in {t['function']['name'] for t in coordination_tools()}:
                            result = json.dumps(await self.coordination(run, name, params), ensure_ascii=False)
                            await run.tool_event(name, {'result': result[:4000]}, 'completed')
                            messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': result})
                            continue
                        if name not in FILE_TOOLS:
                            raise ValueError("Tool is disabled")
                        if name != "get_info":
                            scoped_path(run.worktree, params.get("path", "."))
                        if await run.approval(name, params, "Bonsai file action · applies only to the displayed worktree"):
                            result = execute_file_tool(run.worktree, name, params)
                            await run.tool_event(name, {"result": result[:4000]}, "completed")
                    except (ValueError, OSError) as exc:
                        result = "Tool refused: " + str(exc)
                        await run.tool_event(name, {"error": result}, "refused")
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})

    async def cancel_turn(self, name):
        run = self.running.get(name)
        if run:
            await run.steering.close('cancelled')
            if run.interrupt:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(run.interrupt(), timeout=1)
        task = self.states[name].get('task')
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def stop_agent(self, name):
        if name not in self.states:
            raise ValueError("Choose a registered agent")
        self.states[name]["paused"] = True
        self.team_builder.interrupt(name)
        if self.goals.goal and self.goals.goal['owner'] == name:
            self.goals.pause('Goal lead stopped by user.')
        self.save_project()
        while not self.queues[name].empty():
            self.queues[name].get_nowait()
        await self.cancel_turn(name)
        self.host.store.add("system", f"{name} stopped. Its queued work was cleared; the other agents can continue.", channel=self.default_channel())
        await self.publish_status()

    async def stop(self):
        self.paused = True
        self.team_builder.interrupt()
        self.goals.pause('Room stopped by user.')
        self.save_project()
        for queue in self.queues.values():
            while not queue.empty():
                queue.get_nowait()
        for path in self.data.glob("*_queue.jsonl"):
            path.write_text("", "utf-8")
        await asyncio.gather(*(self.cancel_turn(name) for name in self.states))
        self.host.store.add("system", "Room stopped. Active turns cancelled and queues cleared. Click Resume when ready.", channel=self.default_channel())
        await self.publish_status()

    def save_deploy_queue(self):
        from room_deploy import atomic_json
        pending = {name: list(queue._queue) for name, queue in self.queues.items()}
        for name, entry in self.deploy_held.items():
            if name not in self.running:
                pending.setdefault(name, []).insert(0, entry)
        atomic_json(self.deployment.root / 'pending-wakes.json', {'project_id': self.identity, 'queues': pending})

    def restore_deploy_queue(self):
        if not self.deployment:
            return
        from room_deploy import read_json
        path = self.deployment.root / 'pending-wakes.json'
        saved = read_json(path, {}) or {}
        if saved.get('project_id') != self.identity:
            return
        for name, entries in saved.get('queues', {}).items():
            if name in self.queues:
                for entry in entries:
                    self.queues[name].put_nowait(entry)
        # The checkpoint remains until startup verification opens admission.
        self.deploy_checkpoint_restored = bool(saved)

    async def close(self):
        if self.deployment and self.deployment.reload()['writes'] == 'blocked':
            if not self.running:
                self.save_deploy_queue()
        else:
            await self.stop()
        self.heartbeat.cancel()
        for worker in self.workers.values():
            worker.cancel()
        await asyncio.gather(self.heartbeat, *self.workers.values(), return_exceptions=True)


def install(host):
    runtime = RoomRuntime(host)
    host.agents.runtime = runtime
    host.runtime = runtime
    import room_deploy
    room_deploy.install(host, runtime)
    project_registry.install(host, runtime)
    import team_builder
    team_builder.install(host, runtime)
    import mcp_bridge
    mcp_bridge.channel_guard = runtime.owns_channel

    @host.app.get("/api/runtime")
    async def status():
        # Tasks are private; never serialize asyncio objects or provider credentials.
        snapshot = runtime.snapshot()
        return snapshot

    @host.app.post("/api/runtime/stop")
    async def stop():
        await runtime.stop()
        return {"ok": True}

    @host.app.post("/api/runtime/resume")
    async def resume():
        runtime.paused = False
        runtime.save_project()
        await runtime.publish_status()
        return {"ok": True}

    @host.app.post("/api/runtime/refresh")
    async def refresh():
        async with runtime.profile_lock:
            await runtime.refresh()
        return {"ok": True}

    @host.app.get('/api/runtime/profiles')
    async def profiles():
        return runtime.profiles.describe()

    @host.app.post('/api/runtime/profiles')
    async def create_profile(request: Request):
        try:
            return await runtime.change_profile(await request.json())
        except (ValueError, KeyError, TypeError) as exc:
            return JSONResponse({'error': str(exc)}, 400)

    @host.app.post('/api/runtime/profiles/{name}')
    async def edit_profile(name: str, request: Request):
        try:
            return await runtime.change_profile(await request.json(), name)
        except (ValueError, KeyError, TypeError) as exc:
            return JSONResponse({'error': str(exc)}, 400)

    @host.app.post('/api/runtime/profiles/{name}/archive')
    async def archive_profile(name: str, request: Request):
        try:
            body = await request.json()
            if not isinstance(body.get('archived'), bool):
                raise ValueError('Specify archived as true or false')
            return await runtime.archive_profile(name, body['archived'])
        except (ValueError, KeyError, TypeError) as exc:
            return JSONResponse({'error': str(exc)}, 400)

    @host.app.post("/api/runtime/agent/{name}/stop")
    async def stop_agent(name: str):
        try:
            await runtime.stop_agent(name)
            return {"ok": True}
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)

    @host.app.post("/api/runtime/agent/{name}/resume")
    async def resume_agent(name: str):
        if name not in runtime.states:
            return JSONResponse({"error": "Choose a registered agent"}, 400)
        if runtime.states[name].get('archived'):
            return JSONResponse({'error': 'Restore this profile in Team before resuming it'}, 400)
        runtime.states[name]["paused"] = False
        runtime.save_project()
        await runtime.publish_status()
        return {"ok": True}

    @host.app.post('/api/runtime/settings/{name}')
    async def settings(name: str, request: Request):
        try:
            body = await request.json()
            selected = runtime.controls.change(name, body.get('model'), body.get('effort'))
            await runtime.emit('runtime_preferences', {'agent': name, 'settings': selected}, persist=True)
            await runtime.publish_status()
            return {'ok': True, 'settings': selected, 'applies': 'next turn'}
        except (ValueError, KeyError) as exc:
            return JSONResponse({'error': str(exc)}, 400)

    @host.app.post("/api/runtime/approval/{identifier}")
    async def approval(identifier: str, request: Request):
        body = await request.json()
        try:
            await runtime.decide(identifier, body.get("allow") is True, str(body.get("answer", "")))
            return {"ok": True}
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 409)

    @host.app.post("/api/runtime/project")
    async def project(request: Request):
        if not runtime.paused or runtime.running or any(s.get('task') for s in runtime.states.values()) or any(not q.empty() for q in runtime.queues.values()):
            return JSONResponse({"error": "Stop the room before changing projects"}, 409)
        try:
            body = await request.json()
            async with runtime.profile_lock:
                if body.get('id'):
                    runtime.setup_project(project_registry.switch_target(runtime, body['id']) or None, body['id'])
                else:
                    runtime.setup_project(body.get('path') or None)
                await runtime.refresh()
            await runtime.publish_status()
            await host.broadcast_settings()
            await runtime.emit('project_state', runtime.project_room.snapshot())
            return {"ok": True, "id": runtime.identity}
        except (ValueError, RuntimeError, KeyError) as exc:
            return JSONResponse({"error": str(exc)}, 400)

    @host.app.get("/api/runtime/diffs")
    async def diffs():
        return runtime.diffs()

    @host.app.get('/api/runtime/project-state')
    async def project_state():
        result = runtime.project_room.snapshot()
        messages = host.store.get_recent(30)
        humans = [m for m in messages if m['sender'] not in runtime.states and m['sender'] != 'system']
        result['last_human_message'] = humans[-1]['text'] if humans else ''
        return result

    @host.app.get('/api/runtime/coordination-tools')
    async def coordination_catalog():
        return {'transport': 'stdio MCP for native CLIs; function tools for local models',
                'tools': coordination_tools()}

    @host.app.post('/api/runtime/reviews')
    async def reviews(request: Request):
        return await human_coordination_record('room_review', request)

    @host.app.post('/api/runtime/followups')
    async def followups(request: Request):
        return await human_coordination_record('room_followup', request)

    async def human_coordination_record(tool, request):
        try:
            body = await request.json()
            channel = body.pop('channel', '') or runtime.default_channel()
            if not runtime.owns_channel(channel):
                raise ValueError('Choose a chat in the current project')
            # New human requests stay quiet unless the form opts in; answering an agent's review or
            # follow-up wakes that agent, since it is waiting on the human's verdict or result.
            body.setdefault('notify', body.get('action') in ('respond', 'resolve'))
            return await runtime.coordination_record('user', tool, body, channel, runtime.project_room.chain_for(channel))
        except (ValueError, TypeError, KeyError) as exc:
            return JSONResponse({'error': str(exc)}, 400)

    @host.app.post('/api/runtime/spec')
    async def spec(request: Request):
        try:
            body = await request.json()
            runtime.project_room.set_spec(body.get('text', ''))
            host.store.add('system', 'Optional shared spec saved. Ordinary chat remains available independently of the spec.', channel=runtime.default_channel())
            await runtime.emit('project_state', runtime.project_room.snapshot())
            return {'ok': True}
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, 400)

    @host.app.post('/api/runtime/limits')
    async def limits(request: Request):
        try:
            selected = runtime.project_room.change_limits(await request.json())
            await runtime.publish_status()
            await runtime.emit('project_state', runtime.project_room.snapshot())
            return {'ok': True, 'limits': selected}
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, 400)

    @host.app.get('/api/runtime/goal')
    async def goal_status():
        return runtime.goals.goal

    @host.app.post('/api/runtime/goal')
    async def goal_change(request: Request):
        try:
            body = await request.json()
            result = runtime.goals.change('user', **body)
            runtime.goals.schedule()
            await runtime.publish_status()
            return result
        except (ValueError, TypeError) as exc:
            return JSONResponse({'error': str(exc)}, 400)

    @host.app.post('/api/runtime/tasks')
    async def tasks(request: Request):
        try:
            body = await request.json()
            start = body.pop('start', False)
            channel = body.pop('channel', '') or runtime.default_channel()
            if start and not runtime.owns_channel(channel):
                raise ValueError('Choose a chat in the current project before starting a task')
            before = next((t['status'] for t in runtime.project_room.state['tasks'] if t['id'] == body.get('task_id')), None)
            task = runtime.project_room.task('user', **body)
            if task['status'] == 'done' and before not in (None, 'done') and runtime.owns_channel(channel):
                runtime.dependency_ready(task['id'], channel, runtime.project_room.new_chain(channel))
            if start:
                if not task['owner']:
                    raise ValueError('Assign an owner before starting a task')
                chain = runtime.project_room.new_chain(channel)
                runtime.enqueue(task['owner'], channel, prompt=f"Task {task['id']}: {task['title']}\n{task['details']}\nReserved paths: {', '.join(task['paths'])}",
                                chain=chain, routing={'task_id': task['id']})
            await runtime.emit('project_state', runtime.project_room.snapshot())
            return task
        except (ValueError, TypeError) as exc:
            return JSONResponse({'error': str(exc)}, 400)

    @host.app.post('/api/runtime/coordination/{run_id}')
    async def coordination(run_id: str, request: Request):
        run = next((r for r in runtime.running.values() if r.id == run_id), None)
        if run is None:
            return JSONResponse({'error': 'This agent turn has ended'}, 409)
        try:
            body = await request.json()
            return await runtime.coordination(run, body['tool'], body.get('arguments', {}))
        except (ValueError, TypeError, KeyError) as exc:
            return JSONResponse({'error': str(exc)}, 400)

    return runtime
