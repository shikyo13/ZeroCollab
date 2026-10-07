"""Index of every project this room has worked in: board, team, goal, chats and repository.

A project is keyed by a stable identity: 'chat' (the original conversation-only project),
the hash setup_project() derived from a repository path (projects attached before
identities were stable), or a random 8-hex ID minted by New project. Boards, rosters and
goals live under data/projects/<identity>; project.json remembers the repository path,
an optional display name, the project's chat sessions and a pending repository request.

A chat session's id is the store's channel key and never changes; its title is local to
the project. Room channels that no project owns are legacy history: readable, never
writable, never routed to agents."""
import asyncio
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from project_room import write_json

STATUSES = ('open', 'working', 'blocked', 'review', 'done')
PRIVATE = ('sessions', 'cursors', 'receipts')
IDENTITY = re.compile(r'^(chat|[0-9a-f]{8})$')
# Same rule as app._CHANNEL_NAME_RE: session ids are the store's channel keys.
SESSION_ID = re.compile(r'^[a-z0-9][a-z0-9\-]{0,19}$')
SESSION_TITLE_MAX = 40
SESSIONS_PER_PROJECT = 24
# Store and system defaults write to 'general', so it can never belong to one project.
SHARED_CHANNEL = 'general'
MIGRATION = 'sessions.json'


class Conflict(ValueError):
    """The repository already belongs to another project."""
    def __init__(self, message, project_id):
        super().__init__(message)
        self.project_id = project_id


class Stale(ValueError):
    """A request was made for a project that is no longer active."""


class RepositoryInUse(ValueError):
    """A repository still has reserved work."""


def _read(path, fallback):
    try:
        return json.loads(path.read_text('utf-8'))
    except (OSError, ValueError):
        return fallback


def _same_path(a, b):
    return bool(a) and bool(b) and os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))


def record(folder):
    return _read(folder / 'project.json', {})


def _save_record(folder, value):
    write_json(folder / 'project.json', value)


def _root(runtime):
    return runtime.data / 'projects'


def _folders(runtime):
    root = _root(runtime)
    return [f for f in root.iterdir() if f.is_dir() and IDENTITY.match(f.name)
            and ((f / 'coordination.json').exists() or (f / 'agents.json').exists())] if root.is_dir() else []


def active_folder(runtime):
    return runtime.project_room.path.parent if runtime.project_room.path is not None else None


def remember(runtime):
    """Record the active project's repository so the index never has to infer it later."""
    folder = active_folder(runtime)
    value = record(folder)
    path = str(runtime.project) if runtime.project else ''
    if value.get('path') != path:
        value['path'] = path
        _save_record(folder, value)


def _recover_path(data, identity):
    """Projects attached before project.json existed: each agent worktree's .git file points
    at <repo>/.git/worktrees/<agent>. Accept the repository only if it hashes to this identity."""
    for gitfile in sorted((data / 'worktrees' / identity).glob('*/.git')):
        try:
            text = gitfile.read_text('utf-8').strip() if gitfile.is_file() else ''
        except OSError:
            continue
        if not text.startswith('gitdir:'):
            continue
        gitdir = Path(text[len('gitdir:'):].strip())
        if gitdir.parent.name != 'worktrees' or gitdir.parent.parent.name != '.git':
            continue
        repo = gitdir.parent.parent.parent.resolve()
        if hashlib.sha256(str(repo).encode()).hexdigest()[:8] == identity:
            return str(repo)
    return ''


def path_of(runtime, folder):
    """Repository path of a project; '' for conversation projects and unrecoverable ones."""
    if runtime.project_room.path is not None and runtime.project_room.path.parent == folder:
        return str(runtime.project or '')
    path = record(folder).get('path')
    if path is None:
        path = '' if folder.name == 'chat' else _recover_path(runtime.data, folder.name)
    return path


def _pin_path(runtime, folder, value):
    """Record a recovered repository path in project.json; recovery only works while worktrees
    still exist. An unrecoverable repository stays unpinned: an empty 'path' would make it
    look like a conversation project."""
    if 'path' in value:
        return
    recovered = path_of(runtime, folder)
    if recovered or folder.name == 'chat':
        value['path'] = recovered


def _available(folder, value, path):
    # A repository project whose path cannot be recovered must never be attached as conversation mode.
    conversation = folder.name == 'chat' or ('path' in value and not value['path'])
    return conversation if not path else Path(path).is_dir()


def _name(folder, value, path):
    return value.get('name') or (Path(path).name if path else ('Conversation only' if folder.name == 'chat' else folder.name))


def _folder(runtime, identity):
    if not IDENTITY.match(identity or ''):
        raise ValueError('Choose a listed project')
    folder = _root(runtime) / identity
    if not folder.is_dir():
        raise ValueError('Choose a listed project')
    return folder


def _is_active(runtime, folder):
    return runtime.project_room.path is not None and runtime.project_room.path.parent == folder


def _state(runtime, folder):
    return runtime.project_room.state if _is_active(runtime, folder) else _read(folder / 'coordination.json', {})


def _goal(runtime, folder):
    return runtime.goals.goal if _is_active(runtime, folder) else _read(folder / 'goal.json', None)


def _used_channels(state, goal):
    """Channels a project's agents and records actually used (evidence, not ownership)."""
    channels = {key.split(':', 1)[1] for section in ('sessions', 'cursors') for key in state.get(section, {}) if ':' in key}
    channels |= {item['channel'] for item in state.get('reviews', []) + state.get('followups', []) if item.get('channel')}
    if goal and goal.get('channel'):
        channels.add(goal['channel'])
    return channels


# --- identities ---

def owner_of(runtime, path, exclude=''):
    """Identity of the project that already owns a repository path, or ''."""
    hashed = hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:8]
    for folder in _folders(runtime):
        if folder.name == exclude:
            continue
        own = path_of(runtime, folder)
        if any(_same_path(item['path'], path) for item in repositories(runtime, folder)) or (folder.name == hashed and not own and 'path' not in record(folder)):
            return folder.name
    return ''


def identity_for(runtime, path):
    """Existing project for a repository path, else the legacy path hash; 'chat' without a path."""
    if not path:
        return 'chat'
    return owner_of(runtime, path) or hashlib.sha256(str(path).encode()).hexdigest()[:8]


def switch_target(runtime, identity):
    """Repository path to attach when switching to a listed project ('' for conversation projects)."""
    folder = _folder(runtime, identity)
    path = path_of(runtime, folder)
    if not _available(folder, record(folder), path):
        raise ValueError("This project's repository folder no longer exists or could not be recovered")
    return path


def _new_identity(runtime):
    while True:
        identity = uuid.uuid4().hex[:8]
        if not (_root(runtime) / identity).exists():
            return identity


# --- chat sessions ---

def sessions(folder):
    return [s for s in record(folder).get('sessions', []) if isinstance(s, dict) and SESSION_ID.match(str(s.get('id', '')))]


def claimed_channels(runtime):
    return {s['id'] for folder in _folders(runtime) for s in sessions(folder)}


def migrate_sessions(runtime):
    """One-time: give each project the channels only it used; everything else stays legacy.
    Usage is evidence, not proof, so a channel used by several projects (or none) is not assigned."""
    marker = _root(runtime) / MIGRATION
    if marker.exists():
        return
    folders = _folders(runtime)
    owners = {}
    for folder in folders:
        for channel in _used_channels(_read(folder / 'coordination.json', {}), _read(folder / 'goal.json', None)):
            owners.setdefault(channel, set()).add(folder.name)
    now = time.time()
    assigned = {}
    for channel, projects in owners.items():
        if len(projects) == 1 and channel != SHARED_CHANNEL and SESSION_ID.match(channel):
            assigned.setdefault(next(iter(projects)), []).append(channel)
    for folder in folders:
        value = record(folder)
        if 'sessions' in value:
            continue
        _pin_path(runtime, folder, value)
        value['sessions'] = [{'id': c, 'title': c, 'created': now} for c in sorted(assigned.get(folder.name, []))]
        _save_record(folder, value)
    write_json(marker, {'version': 1, 'migrated': now, 'assigned': {k: sorted(v) for k, v in assigned.items()},
                        'legacy': sorted(c for c in owners if c not in {x for v in assigned.values() for x in v})})


def _title(raw):
    title = ' '.join(str(raw or '').split())
    if not title or len(title) > SESSION_TITLE_MAX:
        raise ValueError(f'Give the chat a name between 1 and {SESSION_TITLE_MAX} characters')
    return title


def _new_session_id(runtime, taken):
    while True:
        identifier = 'c' + uuid.uuid4().hex[:7]
        if identifier not in taken:
            return identifier


def _add_session(runtime, folder, title):
    value = record(folder)
    items = value.setdefault('sessions', [])
    title = _title(title)
    if any(s.get('title', '').lower() == title.lower() for s in items):
        raise ValueError('This project already has a chat with that name')
    if len(items) >= SESSIONS_PER_PROJECT:
        raise ValueError(f'A project can have up to {SESSIONS_PER_PROJECT} chats')
    taken = set(runtime.host.room_settings['channels']) | claimed_channels(runtime)
    session = {'id': _new_session_id(runtime, taken), 'title': title, 'created': time.time()}
    items.append(session)
    _save_record(folder, value)
    return session


def sync_channels(runtime):
    """Keep the room's channel list a superset of every project's sessions (store compatibility).
    Returns True if it changed. Channels are never removed: their history stays readable."""
    channels = runtime.host.room_settings['channels']
    missing = [s['id'] for folder in _folders(runtime) for s in sessions(folder) if s['id'] not in channels]
    channels.extend(dict.fromkeys(missing))
    return bool(missing)


def ensure_session(runtime):
    """Every active project has at least one chat to talk in."""
    folder = active_folder(runtime)
    if folder is not None and not sessions(folder):
        _add_session(runtime, folder, 'General')


def open_sessions(folder):
    """A closed chat keeps its id and history but is off the tab bar and never routed to agents."""
    return [s for s in sessions(folder) if not s.get('closed')]


def owned(runtime):
    folder = active_folder(runtime)
    return [s['id'] for s in open_sessions(folder)] if folder is not None else []


def default_session(runtime):
    folder = active_folder(runtime)
    if folder is None:
        return SHARED_CHANNEL
    ids = [s['id'] for s in open_sessions(folder)]
    last = record(folder).get('last_session')
    return last if last in ids else (ids[0] if ids else SHARED_CHANNEL)


def sessions_payload(runtime):
    folder = active_folder(runtime)
    if folder is None:
        return {}
    value = record(folder)
    path = str(runtime.project or '')
    mine = [{'id': s['id'], 'title': s.get('title') or s['id'], 'readonly': False} for s in open_sessions(folder)]
    closed = [{'id': s['id'], 'title': s.get('title') or s['id']} for s in sessions(folder) if s.get('closed')]
    claimed = claimed_channels(runtime)
    # Channels no project owns predate projects: read-only history, offered from a menu, never a tab.
    history = [{'id': c, 'title': c} for c in runtime.host.room_settings['channels'] if c not in claimed]
    return {'project_id': folder.name, 'project_name': _name(folder, value, path),
            'last_session': default_session(runtime), 'sessions': mine, 'closed_sessions': closed,
            'history_sessions': history}


def create_session(runtime, title):
    session = _add_session(runtime, active_folder(runtime), title)
    sync_channels(runtime)
    return session


def rename_session(runtime, identifier, title):
    folder = active_folder(runtime)
    value = record(folder)
    items = value.get('sessions', [])
    session = next((s for s in items if s.get('id') == identifier), None)
    if session is None:
        raise ValueError('Choose a chat in the current project')
    title = _title(title)
    if any(s is not session and s.get('title', '').lower() == title.lower() for s in items):
        raise ValueError('This project already has a chat with that name')
    session['title'] = title
    _save_record(folder, value)
    return {'id': identifier, 'title': title}


def close_session(runtime, identifier):
    folder = active_folder(runtime)
    value = record(folder)
    items = value.get('sessions', [])
    session = next((s for s in items if s.get('id') == identifier and not s.get('closed')), None)
    if session is None:
        raise ValueError('Choose an open chat in the current project')
    if sum(not s.get('closed') for s in items if isinstance(s, dict)) <= 1:
        raise ValueError('A project needs at least one open chat. Create another chat before closing this one.')
    # A turn in this chat may be running, held by the worker while it waits for capacity (deploy_held),
    # or still queued. Closing would strand it: it would start in a chat it can no longer post in.
    # This check and the save below run without an await, and enqueue refuses closed chats afterwards.
    working = sorted(name for name, run in getattr(runtime, 'running', {}).items() if getattr(run, 'channel', '') == identifier)
    if working:
        raise ValueError(f"{', '.join(working)} is working in this chat. Wait for the turn to finish or stop it first.")
    waiting = {name for name, entry in getattr(runtime, 'deploy_held', {}).items() if (entry or {}).get('channel') == identifier}
    waiting |= {name for name, queue in getattr(runtime, 'queues', {}).items()
                for entry in list(getattr(queue, '_queue', ())) if entry.get('channel') == identifier}
    if waiting:
        raise ValueError(f"{', '.join(sorted(waiting))} has work waiting to start in this chat. "
                         'Let it run or stop the agent before closing.')
    goal = getattr(getattr(runtime, 'goals', None), 'goal', None)
    if goal and goal.get('channel') == identifier and goal.get('status') in ('active', 'paused', 'blocked'):
        raise ValueError('The shared goal runs in this chat. Finish or stop the goal before closing it.')
    session.update(closed=True, closed_at=time.time())
    if value.get('last_session') == identifier:
        value['last_session'] = next(s['id'] for s in items if not s.get('closed'))
    _save_record(folder, value)
    return {'id': identifier, 'closed': True, 'last_session': value.get('last_session', '')}


def reopen_session(runtime, identifier):
    folder = active_folder(runtime)
    value = record(folder)
    session = next((s for s in value.get('sessions', []) if s.get('id') == identifier and s.get('closed')), None)
    if session is None:
        raise ValueError('Choose a closed chat in the current project')
    session.pop('closed', None)
    session.pop('closed_at', None)
    _save_record(folder, value)
    return {'id': identifier, 'title': session.get('title') or identifier, 'closed': False}


def open_session(runtime, identifier):
    folder = active_folder(runtime)
    if identifier not in owned(runtime):
        raise ValueError('Choose a chat in the current project')
    value = record(folder)
    if value.get('last_session') != identifier:
        value['last_session'] = identifier
        _save_record(folder, value)
    return {'last_session': identifier}


# --- repositories ---

def repository_key(path):
    resolved = os.path.normcase(str(Path(path).resolve()))
    slug = re.sub(r'[^a-z0-9]+', '-', Path(path).name.lower()).strip('-')[:24] or 'repo'
    return slug + '-' + hashlib.sha256(resolved.encode()).hexdigest()[:12]


def repositories(runtime, folder=None):
    folder = folder or active_folder(runtime)
    if folder is None:
        return []
    value = record(folder)
    primary = path_of(runtime, folder)
    paths = [primary] if primary else []
    paths += [item['path'] for item in value.get('repositories', []) if item.get('path')]
    result = {}
    for path in paths:
        key = repository_key(path)
        result.setdefault(key, {'key': key, 'path': path, 'primary': _same_path(path, primary)})
    return list(result.values())


def repository_plan(runtime, path, action):
    current = repositories(runtime)
    key = repository_key(path)
    if action == 'remove':
        target = next((r for r in current if r['key'] == key), None)
        if not target or target['primary']:
            raise ValueError('Only an additional repository can be removed; choose another primary first')
        return [r for r in current if r['key'] != key]
    if action == 'replace':
        current = [r for r in current if not r['primary'] or r['key'] == key]
    if not any(r['key'] == key for r in current):
        current.append({'key': key, 'path': str(path), 'primary': False})
    primary = key if action in ('replace', 'primary') or not runtime.project else repository_key(runtime.project)
    return [{**r, 'primary': r['key'] == primary} for r in current]


def commit_repository(runtime, path, workspaces, selected=None):
    """Publish selection/history together, keeping the rest of the project record."""
    folder = active_folder(runtime)
    value = record(folder)
    now = time.time()
    history = value.setdefault('repository_history', [])
    selected = selected or [{'key': repository_key(path), 'path': str(path), 'primary': True}]
    for old in repositories(runtime):
        item = next((item for item in history if _same_path(item.get('path'), old['path']) and item.get('detached') is None), None)
        if item is None:
            item = {'path': old['path'], 'attached': None, 'detached': None}
            history.append(item)
        roots = {n: (s.get('repositories', {}).get(old['key']) or (s if old['primary'] else {})).get('worktree')
                 for n, s in runtime.states.items()}
        item['worktrees'] = {n: root for n, root in roots.items() if root}
    keys = {r['key'] for r in selected}
    for item in history:
        item['key'] = repository_key(item['path'])
        if item.get('detached') is None and item['key'] not in keys:
            item['detached'] = now
    for repo in selected:
        if not any(item['key'] == repo['key'] and item.get('detached') is None for item in history):
            roots = {n: (s.get('repositories', {}).get(repo['key']) or s)['worktree'] for n, s in workspaces.items()}
            history.append({'key': repo['key'], 'path': repo['path'], 'attached': now,
                            'detached': None, 'by': 'user', 'worktrees': roots})
    value.update(path=str(path), repository_history=history, repositories=selected)
    value.pop('repo_request', None)
    _save_record(folder, value)

def repository_root(raw):
    """Resolve an existing repository root; agents get worktrees from its committed HEAD."""
    from room_runtime import git
    path = Path(str(raw or '')).expanduser()
    if not str(raw or '').strip() or not path.is_absolute():
        raise ValueError('Give the absolute path of the repository folder')
    path = path.resolve()
    if not path.is_dir():
        raise ValueError('That folder does not exist')
    try:
        top = Path(git(path, 'rev-parse', '--show-toplevel')).resolve()
    except RuntimeError:
        raise ValueError('That folder is not a Git repository')
    if top != path:
        raise ValueError('Choose the exact root of a Git repository')
    try:
        git(path, 'rev-parse', 'HEAD')
    except RuntimeError:
        raise ValueError('The repository needs at least one commit before agents can get worktrees')
    return path


def new_repository_target(raw):
    path = Path(str(raw or '')).expanduser()
    if not str(raw or '').strip() or not path.is_absolute():
        raise ValueError('Give an absolute folder path for the new repository')
    path = path.resolve()
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError('Choose a new or empty folder for the new repository')
    return path


def create_repository(raw):
    """git init plus an empty initial commit, so agent worktrees have a HEAD to branch from."""
    from room_runtime import git
    path = new_repository_target(raw)
    path.mkdir(parents=True, exist_ok=True)
    git(path, 'init')
    try:
        git(path, 'config', 'user.email')
        identity = []
    except RuntimeError:
        # No configured Git identity; the room still needs a first commit.
        identity = ['-c', 'user.name=ZeroCollab', '-c', 'user.email=zerocollab@localhost']
    git(path, *identity, 'commit', '--allow-empty', '-m', 'Initial commit')
    return path


def check_unowned(runtime, path, exclude=''):
    owner = owner_of(runtime, path, exclude)
    if owner:
        raise Conflict('That repository already belongs to another project. Switch to that project instead.', owner)


def request_repository(runtime, actor, path='', create=False, reason='', action='replace'):
    """An agent proposes an attachment or replacement; only the user applies it."""
    if not isinstance(create, bool):
        raise ValueError('create must be true or false')
    if action not in ('add', 'replace'):
        raise ValueError('Choose add or replace')
    if create:
        target = new_repository_target(path)
    else:
        target = repository_root(path)
        check_unowned(runtime, target, active_folder(runtime).name)
    reason = str(reason or '').strip()
    if not reason:
        raise ValueError('Explain why this project needs a repository')
    folder = active_folder(runtime)
    value = record(folder)
    value['repo_request'] = {'by': actor, 'path': str(target), 'create': create, 'reason': reason[:600], 'time': time.time(),
                             'replace': action == 'replace' and bool(runtime.project), 'action': action,
                             'current': str(runtime.project or '')}
    _save_record(folder, value)
    return value['repo_request']


def dismiss_request(runtime, identity):
    folder = _folder(runtime, identity)
    value = record(folder)
    if value.pop('repo_request', None) is not None:
        _save_record(folder, value)
    return summarize(runtime, folder)


# --- projects ---

def create_project(runtime, name, team='current', path=''):
    name = ' '.join(str(name or '').split())
    if not name or len(name) > 60:
        raise ValueError('Give the project a name between 1 and 60 characters')
    if team not in ('current', 'default'):
        raise ValueError("Choose team 'current' or 'default'")
    repository = ''
    if path:
        repository = repository_root(path)
        check_unowned(runtime, repository)
    identity = _new_identity(runtime)
    folder = _root(runtime) / identity
    write_json(folder / 'coordination.json', {'spec': '', 'tasks': [], 'notes': []})
    if team == 'current':
        # Same people, fresh start: no pause state, no archived members.
        profiles = {k: {**{f: v for f, v in p.items() if f != 'paused'}, 'archived': False}
                    for k, p in runtime.profiles.items.items() if not p.get('archived')}
        write_json(folder / 'agents.json', profiles)
    _save_record(folder, {'name': name, 'path': str(repository), 'sessions': []})
    _add_session(runtime, folder, 'General')
    sync_channels(runtime)
    return summarize(runtime, folder)


def summarize(runtime, folder):
    identity = folder.name
    active = _is_active(runtime, folder)
    state = _state(runtime, folder)
    value = record(folder)
    path = path_of(runtime, folder)
    tasks = state.get('tasks', [])
    counts = dict.fromkeys(STATUSES, 0)
    for task in tasks:
        if task.get('status') in counts:
            counts[task['status']] += 1
    reviews = state.get('reviews', [])
    followups = state.get('followups', [])
    goal = _goal(runtime, folder)
    team = []
    for profile in _read(folder / 'agents.json', {}).values():
        member = {k: profile.get(k, '') for k in ('id', 'name', 'color', 'template')}
        member['archived'] = bool(profile.get('archived'))
        live = runtime.states.get(member['id']) if active else None
        if live:
            member.update(state=live.get('state', ''), paused=bool(live.get('paused')), branch=live.get('branch', ''))
        team.append(member)
    times = [t.get('updated', 0) for t in tasks] + [n.get('time', 0) for n in state.get('notes', [])]
    times += [r.get('updated', 0) for r in reviews + followups] + [(goal or {}).get('updated', 0)]
    updated = max(times, default=0) or max((p.stat().st_mtime for p in folder.iterdir()), default=0)
    chats = [{'id': s['id'], 'title': s.get('title') or s['id'], 'readonly': False} for s in open_sessions(folder)]
    ids = [c['id'] for c in chats]
    available = _available(folder, value, path)
    return {'id': identity, 'name': _name(folder, value, path), 'path': path, 'active': active,
            'conversation': available and not path, 'available': available,
            'tasks': counts, 'total_tasks': len(tasks),
            'pending_reviews': sum(r.get('status') == 'pending' for r in reviews),
            'open_followups': sum(f.get('status') == 'open' for f in followups),
            'goal': {k: goal.get(k) for k in ('id', 'text', 'status', 'owner', 'channel')} if goal else None,
            # Channels this project's records used (evidence); sessions are the chats it owns.
            'channels': sorted(_used_channels(state, goal)), 'sessions': chats,
            'last_session': value.get('last_session') if value.get('last_session') in ids else (ids[0] if ids else ''),
            'repo_request': value.get('repo_request'),
            'repositories': repositories(runtime, folder),
            'repository_history': [{**item, 'key': repository_key(item['path'])} for item in value.get('repository_history', [])],
            'team': team, 'spec': bool(state.get('spec')), 'updated': updated}


def listing(runtime):
    projects = [summarize(runtime, f) for f in _folders(runtime)]
    projects.sort(key=lambda p: (not p['active'], -p['updated']))
    return {'active': next((p['id'] for p in projects if p['active']), ''), 'projects': projects}


def detail(runtime, identity):
    """A read-only view of one project's board, including projects that are not attached right now."""
    folder = _folder(runtime, identity)
    state = _state(runtime, folder)
    board = {k: v for k, v in state.items() if k not in PRIVATE}
    board['notes'] = board.get('notes', [])[-30:]
    return {**summarize(runtime, folder), 'board': board}


def install(host, runtime):
    def failure(exc):
        if isinstance(exc, Conflict):
            return JSONResponse({'error': str(exc), 'project_id': exc.project_id}, 409)
        return JSONResponse({'error': str(exc)}, 409 if isinstance(exc, RepositoryInUse) else 400)

    async def announce():
        await host.broadcast_settings()
        await runtime.emit('project_state', runtime.project_room.snapshot())

    @host.app.get('/api/runtime/projects')
    async def projects():
        return listing(runtime)

    @host.app.post('/api/runtime/projects')
    async def new_project(request: Request):
        try:
            body = await request.json()
            async with runtime.profile_lock:
                result = create_project(runtime, body.get('name'), body.get('team', 'current'), body.get('path') or '')
            host._save_settings()
            await announce()
            return result
        except (ValueError, RuntimeError, TypeError, AttributeError) as exc:
            return failure(exc)

    @host.app.get('/api/runtime/projects/{identity}')
    async def project_detail(identity: str):
        try:
            return detail(runtime, identity)
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, 404)

    @host.app.post('/api/runtime/projects/{identity}')
    async def rename_project(identity: str, request: Request):
        try:
            folder = _folder(runtime, identity)
            body = await request.json()
            name = ' '.join(str(body.get('name', '')).split())
            if len(name) > 60:
                raise ValueError('Use a project name of 60 characters or fewer')
            value = record(folder)
            _pin_path(runtime, folder, value)
            if name:
                value['name'] = name
            else:
                value.pop('name', None)
            _save_record(folder, value)
            if _is_active(runtime, folder):
                await host.broadcast_settings()
            return summarize(runtime, folder)
        except (ValueError, AttributeError) as exc:
            return JSONResponse({'error': str(exc)}, 400)

    @host.app.post('/api/runtime/projects/{identity}/repository')
    async def attach_repository(identity: str, request: Request):
        try:
            folder = _folder(runtime, identity)
            if not _is_active(runtime, folder):
                return JSONResponse({'error': 'Switch to this project before adding its repository'}, 409)
            body = await request.json()
            if runtime.repository_transition:
                return JSONResponse({'error': 'A repository change is already in progress'}, 409)
            job = runtime.start_repository_change(identity, body.get('path'), body.get('create', False), body.get('interrupt', False),
                                                  action=body.get('action', 'replace'), key=body.get('key', ''))
            result = await asyncio.shield(job)
            await announce()
            return result
        except (ValueError, RuntimeError, TypeError, AttributeError, OSError) as exc:
            return failure(exc)

    @host.app.post('/api/runtime/projects/{identity}/repository/transition')
    async def repository_transition(identity: str, request: Request):
        try:
            body = await request.json()
            return runtime.repository_control(identity, body.get('action'))
        except (ValueError, AttributeError) as exc:
            return JSONResponse({'error': str(exc)}, 409)

    @host.app.post('/api/runtime/projects/{identity}/repository/dismiss')
    async def dismiss(identity: str):
        try:
            result = dismiss_request(runtime, identity)
            await runtime.emit('project_state', runtime.project_room.snapshot())
            return result
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, 400)

    async def session_body(request):
        """Session requests may name the project they were made for; a stale one is refused."""
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            raise ValueError('Send a JSON object')
        if body.get('project_id') and body['project_id'] != runtime.identity:
            raise Stale('The project changed; reload the chats for the current project.')
        return body

    @host.app.post('/api/runtime/sessions')
    async def new_session(request: Request):
        try:
            body = await session_body(request)
            session = create_session(runtime, body.get('title'))
            host._save_settings()
            await host.broadcast_settings()
            await host.broadcast_session_created(session, body.get('ref'))
            return session
        except (ValueError, TypeError, AttributeError) as exc:
            return JSONResponse({'error': str(exc)}, 409 if isinstance(exc, Stale) else 400)

    @host.app.post('/api/runtime/sessions/{identifier}')
    async def retitle_session(identifier: str, request: Request):
        try:
            body = await session_body(request)
            result = rename_session(runtime, identifier, body.get('title'))
            await host.broadcast_settings()
            return result
        except (ValueError, TypeError, AttributeError) as exc:
            return JSONResponse({'error': str(exc)}, 409 if isinstance(exc, Stale) else 400)

    async def session_change(change, identifier, request):
        try:
            await session_body(request)
            result = change(runtime, identifier)
            await host.broadcast_settings()
            return result
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, 409 if isinstance(exc, Stale) else 400)

    @host.app.post('/api/runtime/sessions/{identifier}/close')
    async def close_chat(identifier: str, request: Request):
        return await session_change(close_session, identifier, request)

    @host.app.post('/api/runtime/sessions/{identifier}/reopen')
    async def reopen_chat(identifier: str, request: Request):
        return await session_change(reopen_session, identifier, request)

    @host.app.post('/api/runtime/sessions/{identifier}/open')
    async def select_session(identifier: str, request: Request):
        try:
            await session_body(request)
            return open_session(runtime, identifier)
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, 409 if isinstance(exc, Stale) else 400)
