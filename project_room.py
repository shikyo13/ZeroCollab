"""Durable project coordination and bounded room context, without model calls."""
import collections
import json
import hashlib
import math
import os
import re
import statistics
import time
import urllib.parse
import uuid
from collections import namedtuple
from coordination_records import MAX_BATCH_RESOLVE, PIPELINE_CLASSES, SEVERITY_ITEMS, TASK_HISTORY
from pathlib import Path, PurePosixPath
from coordination_records import CoordinationRecords, bounded, review_rounds
from coordination_queue import receipt_key
from durable import atomic_write
from coordination_state import update_execution, execution_state, pending_human_wait
import roomdb

DEFAULT_LIMITS = {'input_chars': 16000, 'agent_turns': 0,
                  'turn_seconds': 0, 'turn_max_seconds': 0, 'parallel_agents': 0, 'local_tool_rounds': 3,
                  'native_tool_calls': 0,
                  'reported_token_budget': 0,
                  # Ready-task wakes and work continuity (ready_wakes.py, work_continuity.py): 1 = on (default,
                  # Zero #16289), 0 = off. Only an unset value takes the default; a saved 0 stays off (K4).
                  'ready_task_wakes': 1}
# Retired limits: still accepted from stored settings and older clients, then ignored. The chat
# @mention handoff cap was removed at the user's direction; agent turns per chain (agent_turns)
# and the reported token budget remain the guards against runaway conversations.
RETIRED_LIMITS = {'hops', 'chat_handoffs'}
LIMIT_RANGES = {'input_chars': (4000, 48000), 'agent_turns': (0, 32),
                'turn_seconds': (0, 3600), 'turn_max_seconds': (0, 14400), 'parallel_agents': (0, 64),
                'local_tool_rounds': (0, 4), 'reported_token_budget': (0, 200000)}
LIMIT_RANGES['native_tool_calls'] = (0, 64)
LIMIT_RANGES['ready_task_wakes'] = (0, 1)
TASK_STATES = {'open', 'working', 'blocked', 'review', 'done'}
# A closure ends a task without claiming delivery (opus 56d0015c): it skips the review and installation gates.
# duplicate and superseded point at the task that carries the work on; parked and wont_do point at the decision.
CLOSE_REASONS = ('duplicate', 'superseded', 'parked', 'wont_do')
CARRIED_ON = ('duplicate', 'superseded')
# Owner-set readiness for ready-task wakes; never inferred from text, status or labels ('' clears it).
NEXT_ACTION_STATES = {'ready', 'deferred', 'waiting', ''}
# C6 (Docs/work-continuity.md r4): what a waiting task may name as its wait.
MAX_WAIT_ON = 4
WAIT_COLLECTIONS = {'task': 'tasks', 'review': 'reviews', 'followup': 'followups'}
RELEASE_VERSION = re.compile(r'\d+\.\d+\.\d+')  # x.y.z only, compared as integers (security 9701e700 W2)
# Changes to these fields by anyone other than the owner are new work for the owner (work_rev).
SEMANTIC_FIELDS = ('status', 'owner', 'transfer', 'depends_on', 'wait', 'next_action', 'next_action_state', 'paths')
# The board holds at most TASK_LIMIT tasks. Finished tasks that nothing open still references move,
# oldest first and ARCHIVE_BATCH at a time, to tasks-archive.jsonl beside coordination.json.
TASK_LIMIT = 200
ARCHIVE_BATCH = 20
# Conversation records (budgets) kept in memory and in coordination.json's chain_records: the newest CHAINS_KEPT plus
# every chain still in use; memory prunes to that once it passes CHAINS_PRUNE_AT (task 70f48e0a, harness 4b5bbefe).
CHAINS_KEPT = 60
CHAINS_PRUNE_AT = 100
CHAIN_COUNTERS = ('turns', 'handoffs', 'reported_tokens', 'input_chars')
# Owned work without a check-in for this long is flagged as stale in context and on the board.
# This is visibility only; turn cutoffs stay governed by the optional limits (0 = off).
STALE_SECONDS = 600
# Providers whose turns get the room MCP server (native_adapters: codex/claude flags, antigravity config).
ROOM_TOOL_PROVIDERS = {'codex', 'claude', 'antigravity'}
# The one profile the release grant can be bound to (Astra 15859); room_release is named only in its turns.
RELEASE_GRANTEE = 'astra'
# The per-turn live context (Docs/agent-profiles.md layer 3): its size budget and how many waiting items it lists.
LIVE_CONTEXT_CHARS = 2400
LIVE_CONTEXT_ITEMS = 8
# Manifest-bound live checks (Astra 93fed9db/78336e2a): a manager decision pins the tasks and owners that must accept
# an installed manifest; each owner (or the user) records passed or failed for that exact manifest.
MANIFEST_SHA = re.compile(r'[0-9a-f]{64}')
MAX_RELEASE_CHECK_TASKS = 32
MAX_ACCEPTANCE_LOG = 20
ACCEPTANCE_RESULTS = ('passed', 'failed')
# The lead's load line flags anyone above these (opus 02fb548e).
LOAD_WORKING_LIMIT = 1
LOAD_REVIEW_LIMIT = 3
# The lead's delivery lines (opus 70947bb7): cut a release when user-visible approved work waits this long after an
# install; flag tasks past these review-round or open-time limits; cycle time over the last day.
RELEASE_TRAIN_HOURS = 2
USER_VISIBLE_LABELS = frozenset({'frontend', 'design', 'motion', 'desktop', 'product'})
SCOPE_REVIEW_ROUNDS = 8
SCOPE_OPEN_HOURS = 4
SCOPE_LISTED = 4
CYCLE_WINDOW_SECONDS = 24 * 3600


def age(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 60}m' if seconds < 3600 else f'{seconds // 3600}h{seconds % 3600 // 60:02d}m'


def stale(task, now):
    return bool(task['owner']) and task['status'] in ('working', 'blocked') and now - task['updated'] > STALE_SECONDS


def write_json(path, value):
    content = json.dumps(value, ensure_ascii=False, indent=2)
    atomic_write(path, lambda stream: stream.write(content))


def cursor_only(delivery):
    """A session's saved context after its provider compacted it: the summary may lack the blocks the fingerprints
    vouch for, so keep only the message cursor; the next turn sends every block in full, and no chat is replayed
    (task 437f53ee)."""
    return {key: value for key, value in delivery.items() if key == 'message_id'}


def clipped(text, limit):
    text = str(text)
    return text if len(text) <= limit else text[:max(0, limit - 90)] + '\n[Excerpt limited. Use room_read for the next section.]'


def addressed(text, name):
    # Same mention grammar as the router: @name, @all or @both, not followed by a name character.
    return bool(re.search(rf'@({re.escape(name)}|all|both)(?![\w-])', str(text), re.IGNORECASE))


def continued(message, origin, limit, uploads=None):
    # A message addressed to the reader is cut only when it cannot fit at all, and then the cut
    # names the exact room_read call that returns the rest, so nobody acts on half a message.
    full = message_text(message, origin, uploads)
    if len(full) <= limit:
        return full
    shown = max(0, limit - 200)
    return (full[:shown] + f"\n[Message #{message['id']} from {message['sender']} continues for {len(full) - shown} more characters. "
            f"Read the rest before acting: room_read(section=\"messages\", after_id={message['id'] - 1}, offset={shown})]")


def scope(raw):
    raw = str(raw).strip().replace('\\', '/').removeprefix('./').rstrip('/')
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ':' in raw or '..' in path.parts:
        raise ValueError('Task scopes must be relative project paths')
    return raw


def task_labels(values):
    # Short area tags (frontend, backend, docs) that group tasks on the board.
    if not isinstance(values, list) or any(not isinstance(x, str) for x in values):
        raise ValueError('labels must be a list of short area names such as frontend, backend or docs')
    result = []
    for value in values:
        raw = ''.join(c if c.isascii() and (c.isalnum() or c in '._') else '-' for c in value.strip().lower())
        label = '-'.join(filter(None, raw.split('-')))[:24]
        if label and label not in result:
            result.append(label)
    return result[:4]


# Formats every provider's image input accepts, so room_image can always deliver them.
RASTER_IMAGES = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
# Videos an agent rendered with room_motion and published (Zero #14291: videos arrive in chat and on tasks).
VIDEOS = {'webm', 'mp4'}
IMAGES = RASTER_IMAGES | {'svg', 'bmp'}  # what the upload API calls kind 'image'
# A pasted original served in place (backend local_files, Zero #14949): /api/files/ref/<16 hex id>/<name>.
REFERENCE_LINK = r'/api/files/ref/([0-9a-f]{16})/([^\s)\]]+)'
MAX_FILE_LABEL = 120  # same bound as the composer's [label](link) match
# What agents can resolve for a message: the upload folder and the pasted-reference registry (either may be None).
Files = namedtuple('Files', 'uploads references')


def upload_url(raw, video=False):
    """A room upload link. Annotation sources stay raster-only; a task attachment may also be a video."""
    # Accept the room's own upload links, including the absolute form agents see in chat excerpts.
    raw = str(raw).strip()
    path = '/uploads/' + raw.split('/uploads/', 1)[1] if '/uploads/' in raw else ''
    name = path[len('/uploads/'):]
    if not name or not all(c.isascii() and (c.isalnum() or c in '._-') for c in name) or name.startswith('.'):
        raise ValueError('Attachments must be uploaded images (/uploads/<file>)')
    extension = name.rsplit('.', 1)[-1].lower()
    if extension not in RASTER_IMAGES and not (video and extension in VIDEOS):
        # SVG can carry script; board images and annotation sources are raster only.
        raise ValueError('Attach a PNG, JPEG, GIF or WebP image' + (', or a WebM or MP4 video' if video else ''))
    return path


# A task card shows its CARD_IMAGES newest images; older ones stay viewable as earlier images.
# Every earlier reference is kept; project_state carries the newest EARLIER_SHOWN of them plus the
# total, and room_task get (or the Board's Show all) returns the rest.
CARD_IMAGES = 8
EARLIER_SHOWN = 40
# One save carries the card plus at most this many images in total; larger batches are split by the caller.
ATTACHMENT_INPUT = CARD_IMAGES + 40


def task_attachments(values):
    # Images shown on the board card; prompts carry only a count, agents fetch details and pixels on demand.
    if not isinstance(values, list) or len(values) > ATTACHMENT_INPUT:
        raise ValueError(f'attachments must be a list of up to {ATTACHMENT_INPUT} uploaded images per call; '
                         'send larger batches in several calls (each keeps the card at its newest images)')
    result = []
    for value in values:
        item = {'url': value} if isinstance(value, str) else value
        if not isinstance(item, dict):
            raise ValueError('Each attachment needs a url')
        attachment = {'url': upload_url(item.get('url', ''), video=True), 'name': str(item.get('name') or 'image')[:120]}
        if item.get('note'):
            attachment['note'] = bounded(item['note'], 'attachment note')
        if item.get('source'):
            attachment['source'] = upload_url(item['source'])
        if attachment['url'] not in [a['url'] for a in result]:
            result.append(attachment)
    return result


def arrange_attachments(wanted, previous, earlier):
    """Fit a task's images to the card without refusing any.

    wanted is the new list in order (oldest first); previous and earlier are the stored card and
    earlier lists. Images just added always stay on the card. When more than CARD_IMAGES remain,
    older images move to earlier images: first an annotation whose original has a newer annotation
    on the card (superseded), then the oldest. An image explicitly left out of wanted is removed,
    not archived. Returns (card, earlier, moved), each moved image carrying why it moved."""
    before = {a['url'] for a in previous}
    added = [a for a in wanted if a['url'] not in before]
    card = list(wanted)
    moved = []

    def move(item, reason, by=''):
        card.remove(item)
        moved.append({**item, 'moved': time.time(), 'reason': reason, **({'superseded_by': by} if by else {})})

    # Many images added at once: only the newest CARD_IMAGES of them can stay.
    for item in added[:max(0, len(added) - CARD_IMAGES)]:
        move(item, 'overflow')
    added = added[-CARD_IMAGES:]
    while len(card) > CARD_IMAGES:
        keep = {a['url'] for a in added}
        older = [a for a in card if a['url'] not in keep]
        newer_of = {}
        for a in card:
            if a.get('source'):
                newer_of[a['source']] = a  # the last (newest) annotation of each original
        superseded = next((a for a in older if a.get('source') and newer_of.get(a['source']) is not a), None)
        if superseded:
            move(superseded, 'superseded', newer_of[superseded['source']]['url'])
        else:
            move(older[0], 'overflow')
    on_card = {a['url'] for a in card}
    kept = [a for a in earlier if a['url'] not in on_card and a['url'] not in {m['url'] for m in moved}]
    # Newest first. Every reference is kept, like the files themselves.
    return card, [*reversed(moved), *kept], moved


def overlaps(a, b):
    # Directory scopes and globs reserve the directory prefix conservatively.
    a, b = a.split('*', 1)[0].rstrip('/'), b.split('*', 1)[0].rstrip('/')
    return a in ('', '.') or b in ('', '.') or a == b or a.startswith(b + '/') or b.startswith(a + '/')


PATCH_OVERLAP_CHARS = 3000
# The chat line room_task posts after a task change (room_runtime coordination); the board carries it.
TASK_RECEIPT = re.compile(r'Task [0-9a-f]{8}: [^\n]* \| owner \S+ \| \w+')


def patch_view(name, diff, reserved):
    """A one-line summary of a peer worktree (files and line counts) and the hunks that touch the
    reader's reserved (repository_key, path) scopes. Counts are lower bounds when the diff was capped."""
    sections, current, key = [], None, ''
    for text in diff.splitlines():
        repository = re.fullmatch(r'\[Repository ([^\]]+)\]', text)
        header = re.match(r'diff --git a/.* b/(.+)$', text) or re.fullmatch(r'New file (.+):', text)
        if repository:
            key, current = repository.group(1), None
        elif header:
            current = {'key': key, 'path': header.group(1), 'new': text.startswith('New file'), 'lines': [text], 'add': 0, 'del': 0}
            sections.append(current)
        elif current is not None:
            current['lines'].append(text)
            if current['new'] or (text.startswith('+') and not text.startswith('+++')):
                current['add'] += 1
            elif text.startswith('-') and not text.startswith('---'):
                current['del'] += 1
    if not sections:
        # An unavailable checkout or unrecognised output: a short note, never raw bulk.
        return {'name': name, 'files': [], 'summary': f"{name}: {clipped(diff, 300)}", 'overlap': ''}
    label = lambda s: (s['key'] + ':' if s['key'] else '') + s['path'] + (' (new)' if s['new'] else '')
    files = [label(s) for s in sections]
    listed = ', '.join(files[:8]) + (f' and {len(files) - 8} more' if len(files) > 8 else '')
    count = f"{len(files)} file{'s' if len(files) != 1 else ''}"
    summary = f"{name}: {count}, +{sum(s['add'] for s in sections)}/-{sum(s['del'] for s in sections)}: {listed}"
    touching = [s for s in sections if any((not s['key'] or not rk or s['key'] == rk) and overlaps(s['path'], p)
                                           for rk, p in reserved)]
    overlap = (f"{name}'s worktree:\n" + clipped('\n'.join('\n'.join(s['lines']) for s in touching), PATCH_OVERLAP_CHARS)
               if touching else '')
    return {'name': name, 'files': files, 'summary': summary, 'overlap': overlap}


class ProjectRoom:
    def __init__(self, runtime):
        self.runtime = runtime
        self.limits_path = runtime.data / 'room_limits.json'
        self.limits = dict(DEFAULT_LIMITS)
        if self.limits_path.exists():
            stored = json.loads(self.limits_path.read_text('utf-8'))
            self.change_limits(stored, save=False)
        self.path = None
        self.store = None  # roomdb.Store for the active project
        self.state = {'spec': '', 'tasks': [], 'notes': []}
        self.chains = {}
        self.current_chain = {}
        self.records = CoordinationRecords(self)

    def archive_path(self):
        return self.path.parent / 'tasks-archive.jsonl'

    def archivable(self):
        """Done tasks no live record points at, oldest first: not a dependency of unfinished work,
        not the subject of a pending review or open follow-up, with no transfer or wait."""
        tasks = self.state['tasks']
        held = {d for t in tasks if t['status'] != 'done' for d in t.get('depends_on', [])}
        held |= {r['task_id'] for r in self.state.get('reviews', []) if r.get('status') in ('pending', 'changes_requested')}
        held |= {f['task_id'] for f in self.state.get('followups', []) if f.get('status') == 'open'}
        return sorted((t for t in tasks if t['status'] == 'done' and t['id'] not in held
                       and not t.get('transfer') and not t.get('wait')), key=lambda t: t.get('updated', 0))

    def archive_done(self, count):
        """Append up to count archivable tasks to the archive (flushed to disk first), then drop them
        from the board in place. A crash in between leaves a task in both; the board copy wins."""
        moving = self.archivable()[:count]
        if not moving:
            return 0
        with open(self.archive_path(), 'a', encoding='utf-8') as archive:
            archive.write(''.join(json.dumps(t, ensure_ascii=False) + '\n' for t in moving))
            archive.flush()
            os.fsync(archive.fileno())
        ids = {t['id'] for t in moving}
        self.state['tasks'][:] = [t for t in self.state['tasks'] if t['id'] not in ids]
        self.state['archived_tasks'] = self.state.get('archived_tasks', 0) + len(moving)
        return len(moving)

    def archived_task(self, task_id):
        """An archived task by id (the last copy written), or None."""
        path = self.archive_path() if self.path else None
        if not task_id or not path or not path.exists():
            return None
        found = None
        with open(path, encoding='utf-8') as archive:
            for line in archive:
                if task_id in line:
                    try:
                        task = json.loads(line)
                    except ValueError:
                        continue
                    if task.get('id') == task_id:
                        found = task
        return found

    def use_project(self, identity):
        folder = self.runtime.data / 'projects' / identity
        # The records live in room.db (roomdb.py; a coordination.json is imported on first open). path keeps naming
        # coordination.json: it is the project's identity for its folder and in saved claims (manager_wakes).
        self.store, self.state = roomdb.Store.open(folder)
        self.path = folder / roomdb.JSON_NAME
        # Legacy bare reservations keep their original repository after a primary change.
        self.state.setdefault('scope_repository', self.primary_repository_key())
        for note in self.state.get('notes', []):
            if note.get('kind') == 'decision':
                note.setdefault('id', uuid.uuid4().hex[:8])
                note.setdefault('status', 'active')
        # This project's budgets replace the previous project's before anything is saved (harness 4b5bbefe 1).
        self.chains.clear()
        self.current_chain.clear()
        self.restore_chains(self.state.pop('chain_records', None))
        self.save()

    def restore_chains(self, records):
        """Conversation records saved by save(), so a restart keeps each budget, turn count and pause (K1: no fresh
        budget is created) and the room's automatic wakes still find the chain they continue (task 70f48e0a). A
        malformed entry is skipped, never fatal to loading (harness 4b5bbefe 4)."""
        for record in records if isinstance(records, list) else []:
            if not isinstance(record, dict) or not isinstance(record.get('id'), str) or not record['id']:
                continue
            chain = self.fresh_chain(record['id'], record['channel'] if isinstance(record.get('channel'), str) else '')
            for key in CHAIN_COUNTERS:
                value = record.get(key)
                chain[key] = max(0, int(value)) if isinstance(value, (int, float)) and not isinstance(value, bool) \
                    and math.isfinite(value) else 0
            chain['paused'] = bool(record.get('paused'))  # anything truthy stays paused
            self.chains[chain['id']] = chain

    def snapshot(self):
        review_hashes = {}
        live_tasks = {t['id'] for t in self.state['tasks'] if t.get('status') != 'done'}
        return {**{k: v for k, v in self.state.items() if k not in ('sessions', 'cursors', 'receipts', 'manager_audit')},
                'tasks': [self.broadcast_view(t) for t in self.state['tasks']],
                'reviews': [self.records.review_view(r, review_hashes, self.records.drift, live_tasks)
                            for r in self.state.get('reviews', [])],
                'limits': dict(self.limits), 'chains': list(self.chains.values())[-12:],
                'stale_seconds': STALE_SECONDS}

    def task_view(self, task):
        # Planner origin is server-owned, even when an owner later edits the details.
        if task.get('planning_origin'):
            from manager_wakes import planning_prefix
            prefix = planning_prefix(task['planning_origin'])
            details = task.get('details', '')
            task = {**task, 'details': details if details.startswith(prefix) else prefix + details}
        activity = (getattr(self.runtime, 'activity', lambda name: {})(task['owner']) or {}) if task.get('owner') else {}
        state = execution_state(task, self.state,
            running=task['id'] in activity.get('running_task_ids', []),
            queued=task['id'] in activity.get('queued_task_ids', []))
        return {**task, 'repository_key': self.task_repository_key(task), 'execution_state': state, 'owner_activity': activity,
                'observed_at': time.time(),
                'stale': stale(task, time.time()) and not pending_human_wait(task, self.state)}

    def broadcast_view(self, task):
        """task_view for project_state: the newest EARLIER_SHOWN earlier images and their total."""
        view = self.task_view(task)
        earlier = task.get('earlier_attachments', [])
        if len(earlier) > EARLIER_SHOWN:
            view['earlier_attachments'] = earlier[:EARLIER_SHOWN]
            view['earlier_total'] = len(earlier)
        return view

    def task_summary(self, task, check_in=False):
        """An agent's receipt or inbox entry: state and next step without the history, details and
        attachments, which can run to tens of thousands of characters. room_task get returns the full record."""
        view = self.task_view(task)
        # repository_key qualifies the bare reserved paths, which are pinned to the repository primary when claimed.
        summary = {key: view[key] for key in ('id', 'title', 'owner', 'status', 'execution_state', 'stale', 'updated', 'repository_key')}
        summary.update({key: view[key] for key in ('priority', 'next_action', 'wait', 'transfer', 'depends_on', 'paths', 'labels',
                                                   'closure') if view.get(key)})
        summary['history_entries'] = len(task['updates'])
        if task.get('attachments'):
            summary['images'] = len(task['attachments'])
        if task.get('earlier_attachments'):
            summary['earlier_images'] = len(task['earlier_attachments'])
        if task.get('moved_to_earlier'):
            summary['moved_to_earlier'] = task['moved_to_earlier']
        if check_in:
            latest = next((u for u in reversed(task['updates']) if not u.get('auto')), None)
            if latest:
                summary['last_check_in'] = {'by': latest['by'], 'time': latest['time'], 'text': clipped(latest['text'], 400)}
        return summary

    def save(self, touched=None):
        """Writes the board and, under chain_records, the conversation records (task 70f48e0a). Their counters move
        in memory between saves, so a crash undercounts a budget by at most what was spent since the last save
        (harness 4b5bbefe 3). chain_records lives only on disk, never in self.state or the snapshot.
        With no argument every record is compared with room.db and the changed ones written. touched names
        everything the caller mutated since the last save, as (section, id) or a section name (roomdb.plan_touched);
        chain_records is always compared, because counters move between saves."""
        if touched is not None:
            touched = [*touched, 'chain_records']
        self.store.save({**self.state, 'chain_records': self.chain_records()}, touched)

    def chain_records(self):
        """The conversation records worth keeping across a restart: those in use plus the newest CHAINS_KEPT. The
        manager audit chain is left out: its own record in manager_audit restores it and its reserve_turn fence,
        which permit() still consults for every turn (harness 4b5bbefe 7)."""
        keep = self.chains_in_use() | set(list(self.chains)[-CHAINS_KEPT:])
        audit = (self.state.get('manager_audit', {}).get('chain') or {}).get('id')
        return [{'id': chain['id'], 'channel': chain.get('channel', ''), 'paused': bool(chain.get('paused')),
                 **{key: chain.get(key, 0) for key in CHAIN_COUNTERS}}
                for identifier, chain in self.chains.items()
                if identifier in keep and identifier != audit and chain.get('kind') != 'manager_audit']

    def chains_in_use(self):
        """Chains pruning must keep: running turns, an open shared goal, the manager audit and each channel's
        current conversation (harness 4b5bbefe 2)."""
        # Every save() reads this, so a run without an entry yet must not fail the board's write.
        active = {(getattr(r, 'entry', None) or {}).get('chain') for r in getattr(self.runtime, 'running', {}).values()}
        goal = getattr(getattr(self.runtime, 'goals', None), 'goal', None)
        if goal and goal.get('status') in ('active', 'paused', 'blocked'):
            active.add(goal.get('chain'))
        audit = self.state.get('manager_audit', {}).get('chain') or {}
        if audit.get('id'):
            active.add(audit['id'])
        return (active | set(self.current_chain.values())) - {None}

    def change_limits(self, updates, save=True):
        values = dict(self.limits)
        for name, value in updates.items():
            if name in RETIRED_LIMITS:
                continue
            if name not in LIMIT_RANGES or isinstance(value, bool) or not isinstance(value, int):
                raise ValueError('Choose integer values for supported room limits')
            low, high = LIMIT_RANGES[name]
            if not low <= value <= high:
                raise ValueError(f'{name} must be between {low} and {high}')
            values[name] = value
        if values['turn_seconds'] and values['turn_max_seconds'] and values['turn_max_seconds'] < values['turn_seconds']:
            raise ValueError('turn_max_seconds must be 0 (off) or at least turn_seconds')
        self.limits = values
        if save:
            write_json(self.limits_path, values)
        return dict(values)

    def set_spec(self, text):
        if not isinstance(text, str) or len(text) > 64000:
            raise ValueError('The shared spec must be text under 64,000 characters')
        self.state['spec'] = text.strip()
        self.save()

    @staticmethod
    def fresh_chain(identifier, channel):
        return {'id': identifier, 'channel': channel, 'turns': 0,
                'handoffs': 0, 'reported_tokens': 0, 'input_chars': 0, 'paused': False}

    def adopt_chain(self, identifier, channel):
        """Conversation records survive a restart through chain_records (task 70f48e0a). Admitted work that names
        one they don't hold (from before that upgrade, or pruned) gets a fresh record under the same id instead of
        being refused as if its budget ran out (harness 8016e379). Never used for manager check-ins, whose fences
        must hold."""
        if identifier and identifier not in self.chains:
            self.chains[identifier] = self.fresh_chain(identifier, channel)

    def new_chain(self, channel):
        identifier = uuid.uuid4().hex[:12]
        self.current_chain[channel] = identifier
        self.chains[identifier] = self.fresh_chain(identifier, channel)
        # Completed old conversations need not stay in memory; audit retains events. An open shared goal keeps
        # continuing on its chain, so its budget survives pruning with the others in use.
        if len(self.chains) > CHAINS_PRUNE_AT:
            active = self.chains_in_use()
            for old in list(self.chains)[:-CHAINS_KEPT]:
                if old not in active:
                    self.chains.pop(old, None)
        return identifier

    def chain_for(self, channel):
        return self.current_chain.get(channel) or self.new_chain(channel)

    def permit(self, identifier, audit_reserved=None):
        chain = self.chains.get(identifier)
        if chain is None:
            return False
        if chain['paused'] or (self.limits['agent_turns'] and chain['turns'] - bool(audit_reserved) >= self.limits['agent_turns']) or (self.limits['reported_token_budget'] and chain['reported_tokens'] >= self.limits['reported_token_budget']):
            chain['paused'] = True
            return False
        audit = getattr(self.runtime, 'manager_wakes', None)
        if audit_reserved and not audit:
            return False
        if audit and not audit.reserve_turn(identifier, audit_reserved):
            return False
        if not audit_reserved:
            chain['turns'] += 1
        if audit and (audit.record.get('chain') or {}).get('id') == identifier:
            self.save()
        return True

    def handoffs_spent(self, identifier):
        # The chat @mention handoff cap is retired; handoffs are still counted for the chain record.
        return False

    def handoff(self, identifier):
        chain = self.chains.get(identifier)
        if chain is None or chain['paused']:
            return False
        chain['handoffs'] += 1
        return True

    def record_usage(self, run, usage):
        total = usage.get('total_tokens')
        if not isinstance(total, (int, float)):
            total = sum(usage.get(k, 0) for k in ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'))
        total = max(0, int(total))
        delta = max(0, total - run.reported_tokens)
        run.reported_tokens = max(run.reported_tokens, total)
        chain = self.chains.get(run.entry.get('chain'))
        if chain:
            chain['reported_tokens'] += delta
            audit = getattr(self.runtime, 'manager_wakes', None)
            if audit and (audit.record.get('chain') or {}).get('id') == chain['id']:
                self.save()
        return {'reported_tokens': run.reported_tokens, 'provider_usage': usage}

    def previous_session(self, run):
        saved = self.state.get('sessions', {}).get(run.session_key, {})
        template = self.runtime.states[run.name].get('template')
        if (saved.get('settings') == run.settings
                and saved.get('provider') == self.runtime.states[run.name]['provider']
                and saved.get('template', template) == template
                and saved.get('instruction_hash') == run.instruction_hash):
            return saved
        return {}

    def forget_provider_sessions(self, name):
        """Persist the reset before switching connections, including legacy sessions without a template."""
        sessions = self.state.get('sessions', {})
        for key in list(sessions):
            if key.startswith(name + ':'):
                del sessions[key]
        self.save(touched=())

    def complete(self, run):
        # Every turn ends here. It changes only value sections (cursors, receipts, sessions), which a touched save
        # always compares, so the 3,400 list records are skipped (roomdb.plan_touched).
        if getattr(run, 'manager_audit', None):
            self.save(touched=())
            return  # Timer sessions never replace the ordinary conversation continuation.
        delivery = run.context_delivery
        if getattr(run, 'compacted', False):
            delivery = cursor_only(delivery)
        self.state.setdefault('cursors', {})[run.session_key] = delivery
        receipts = self.state.setdefault('receipts', {}).get(run.session_key, [])
        delivered = run.receipts
        receipts = [r for r in receipts if r not in delivered] + sorted(delivered - {''})
        self.state['receipts'][run.session_key] = receipts[-512:]
        if run.provider_session:
            self.state.setdefault('sessions', {})[run.session_key] = {'id': run.provider_session,
                'settings': run.settings, 'provider': self.runtime.states[run.name]['provider'],
                'template': self.runtime.states[run.name].get('template'),
                'instruction_hash': run.instruction_hash,
                'context': delivery, 'usage_total': getattr(run, 'provider_usage_total', 0)}
        self.save(touched=())

    def forget_context(self, run):
        """A turn whose session compacted but that did not complete (cancelled, timed out, failed): complete() never
        ran, so the fingerprints saved by the last completed turn still vouch for blocks the compacted summary may
        have dropped. Cut them to the message cursor, as complete() does (harness 0a48a156)."""
        if getattr(run, 'manager_audit', None):
            return
        cursors = self.state.get('cursors', {})
        if isinstance(cursors.get(run.session_key), dict):
            cursors[run.session_key] = cursor_only(cursors[run.session_key])
        session = self.state.get('sessions', {}).get(run.session_key)
        if isinstance(session, dict) and isinstance(session.get('context'), dict):
            session['context'] = cursor_only(session['context'])
        self.save(touched=())  # cursors and sessions are value sections: a touched save compares them

    def task(self, actor, action, task_id='', title='', details='', paths=None, owner='', status='', update='', depends_on=None, labels=None, attachments=None,
             next_action=None, priority=None, wait=None, evidence=None, next_action_state=None,
             close_reason='', close_pointer='', acceptance=None, wait_on=None):
        tasks = self.state['tasks']
        closing = None
        task = next((t for t in tasks if t['id'] == task_id), None)
        previous = task
        human = actor == 'user'
        # Refuse over-limit text before any ownership or status change is applied.
        title, details = bounded(title, 'title'), bounded(details, 'details')
        update = bounded(update, 'update') if update else update
        if action == 'get':
            if not task:
                task = self.archived_task(task_id)
                if not task:
                    raise ValueError('Task was not found')
                return {**self.task_view(task), 'archived': True,
                        'reviews': [self.records.review_view(r) for r in self.state.get('reviews', []) if r['task_id'] == task_id],
                        'followups': [f for f in self.state.get('followups', []) if f['task_id'] == task_id]}
            return {**self.task_view(task),'reviews': [self.records.review_view(r) for r in self.state.get('reviews', []) if r['task_id'] == task_id],
                    'followups': [f for f in self.state.get('followups', []) if f['task_id'] == task_id]}
        if action == 'create':
            duplicate = next((t for t in tasks if t['status'] != 'done' and t['title'].strip().casefold() == title.casefold()), None)
            if duplicate:
                raise ValueError('Duplicate task; existing task: ' + json.dumps({k: duplicate[k] for k in ('id', 'title', 'owner', 'status')}))
            if not title:
                raise ValueError('Provide a task title')
            if len(tasks) >= TASK_LIMIT:
                # Finished history must not block new work (Zero #12082): move the oldest
                # unreferenced done tasks to the archive file, in batches.
                self.archive_done(max(ARCHIVE_BATCH, len(tasks) - TASK_LIMIT + 1))
                if len(tasks) >= TASK_LIMIT:
                    raise ValueError(f'The board holds {TASK_LIMIT} tasks that are unfinished or still referenced '
                                     'by open work; finish, release or merge some before creating more')
            task = {'id': uuid.uuid4().hex[:8], 'title': title,
                    'details': details, 'paths': [self.task_scope(p) for p in (paths or [])[:20]],
                    'repository_key': self.primary_repository_key(),
                    'owner': owner or ('' if human else actor), 'status': 'open', 'updates': [], 'updated': time.time()}
        elif not task:
            if self.archived_task(task_id):
                raise ValueError('That task is archived and read-only; create a new task instead')
            raise ValueError('Task was not found')
        else:
            task = {**task, 'updates': list(task['updates'])}
            accepting = action == 'accept' and task.get('transfer', {}).get('to') == actor
            # The lead closes or parks any task, as the user can, instead of asking each owner to (task e14159ba).
            lead_closing = action == 'update' and bool(close_reason) and actor == self.room_lead()
            if not human and task['owner'] and task['owner'] != actor and not accepting and not lead_closing:
                raise ValueError(f"Task is owned by {task['owner']}; ask its owner to delegate it")
            if lead_closing and task['owner'] not in ('', actor) and (
                    title or details or status or owner or paths is not None or depends_on is not None
                    or labels is not None or attachments is not None or next_action is not None or priority is not None
                    or wait is not None or evidence is not None or next_action_state is not None
                    or acceptance is not None or wait_on is not None):
                raise ValueError(f"Task is owned by {task['owner']}; the lead may only close it here "
                                 '(close_reason, close_pointer and an update note)')
            if paths is not None:
                task['paths'] = [self.task_scope(p) for p in paths[:20]]
                task['repository_key'] = self.primary_repository_key()
            if action == 'claim':
                task.update(owner=actor if not human else owner, status='working')
                task.pop('transfer', None)
            elif action == 'delegate':
                if not owner:
                    raise ValueError('Choose the recipient who will own this task')
                if owner == task['owner']:
                    raise ValueError('Choose a different recipient')
                if owner not in self.runtime.states:
                    raise ValueError('Choose a registered participant')
                if human:
                    task.update(owner=owner, status='open')
                    task.pop('transfer', None)
                else:
                    task['transfer'] = {'from': actor, 'to': owner, 'requested_at': time.time()}
            elif action == 'accept':
                transfer = task.get('transfer')
                if not transfer or (not human and transfer['to'] != actor):
                    raise ValueError('Only the proposed recipient or user can accept the transfer')
                task.update(owner=transfer['to'], status='open')
                task.pop('transfer', None)
            elif action == 'release':
                task.update(owner='', status='open')
                task.pop('transfer', None)
            elif action == 'update':
                if human and (task.get('scope_cap') or {}).get('stopped'):
                    # The user lifts an E5 stop by touching the task (harness e917f6d0 note 1). No rounds are
                    # granted: the next agent request past the cap opens a new continue-or-stop item.
                    task['scope_cap'] = {k: v for k, v in task['scope_cap'].items() if k != 'stopped'}
                if title:
                    task['title'] = title
                if details:
                    task['details'] = details
                if status:
                    if status not in TASK_STATES:
                        raise ValueError('Choose open, working, blocked, review, or done')
                    task['status'] = status
                    if status != 'done':
                        task.pop('closure', None)  # reopening a closed task clears its closure
                if close_reason or close_pointer:
                    if status or wait:
                        raise ValueError('Close with close_reason and close_pointer, without a status or wait')
                    closing = self.closure(task, tasks, actor, close_reason, close_pointer)
                    task.update(status='done', closure=closing)
                    # A closed task waits on nobody and moves to nobody (backend 9dd7990a note 1).
                    task.pop('transfer', None)
                    task.pop('wait', None)
            else:
                raise ValueError('Choose get, create, claim, delegate, accept, release, or update')
            if previous['status'] == 'done' and task['status'] != 'done' and task.get('depends_on'):
                # Reopening: a dependency missing from the board was archived, and only done tasks
                # are archived, so it is finished. Dropping it keeps the task from waiting forever
                # on an id every done-set check would treat as unfinished (backend 86d7fe0e F1).
                board = {t['id'] for t in tasks}
                task['depends_on'] = [d for d in task['depends_on'] if d in board]
            if not human and task['status'] == 'working' and previous['status'] != 'working':
                # E1, one working task per agent (Docs/process-enforcement.md, Zero #16116). Only claim and update
                # move an agent's task into working; re-claiming a working task passes. The user is never refused.
                if not task['owner']:
                    # An unowned working task would sit outside every agent's count (security a5ef28ee).
                    raise ValueError('E1: this task has no owner. Claim it instead of setting it to working.')
                other = next((t for t in tasks if t['id'] != task['id'] and t['owner'] == task['owner']
                              and t['status'] == 'working'), None)
                if other:
                    raise ValueError(f"E1: you already have {other['id']} working. Move it to open, blocked or review "
                                     '(with next_action_state deferred and a reason), then try again.')
        task = update_execution(task, self.state, next_action=next_action, priority=priority, wait=wait)
        if previous and task['owner'] != previous['owner']:
            # A new owner opts in to ready-task wakes for themselves; readiness never transfers.
            task.pop('next_action_state', None)
        if next_action_state is not None:
            if next_action_state not in NEXT_ACTION_STATES:
                raise ValueError('next_action_state must be ready, deferred, waiting, or empty to clear')
            if not human and actor != task['owner']:
                raise ValueError("Only the task's owner or the user can set next_action_state")
            if next_action_state:
                task['next_action_state'] = next_action_state
            else:
                task.pop('next_action_state', None)
        if previous and task['owner'] != previous['owner']:
            task.pop('wait_on', None)  # a wait is its owner's; a new owner names their own
        if wait_on is not None:
            if not human and actor != task['owner']:
                raise ValueError("Only the task's owner or the user can set wait_on")
            refs = self.wait_refs(wait_on, task['id'])
            if refs:
                task['wait_on'] = refs
            else:
                task.pop('wait_on', None)
        if not previous or task['owner'] != previous['owner'] or ((human or actor == task['owner']) and any(
                task.get(k) != previous.get(k) for k in ('next_action', 'next_action_state', 'wait_on'))):
            # C6 (Docs/work-continuity.md r4): when this task's wait last changed. A wait's checks are keyed on it,
            # so re-deferring with a new reason or new references re-arms them, and an unchanged wait never repeats.
            # Only the owner, the user or a new owner starts a new wait (security 9701e700 W1): nobody else can
            # re-arm a met wait against the owner's budget or push back its re-check.
            task['wait_since'] = time.time()
        if evidence is not None:
            if not isinstance(evidence, dict) or set(evidence) - {'implementation', 'review', 'installation', 'acceptance'}:
                raise ValueError('Evidence keys: implementation, review, installation, acceptance')
            if any(not isinstance(v, str) or len(v) > 2400 for v in evidence.values()):
                raise ValueError('Evidence entries must be text of at most 2400 characters')
            task['evidence'] = {**task.get('evidence', {}), **evidence}
        accepted = None
        if acceptance is not None:
            accepted = self.acceptance_entry(task, actor, action, acceptance)
            task['acceptance_log'] = (task.get('acceptance_log', []) + [accepted])[-MAX_ACCEPTANCE_LOG:]
        if not human and previous and (action in ('delegate', 'accept', 'release')
                                       or (task['status'] == 'done' and previous['status'] != 'done')):
            # L1: a pinned task isn't finished, closed or handed on before its live check (backend 7a62b62a C2);
            # release_request.acceptance would otherwise refuse every later agent release. A pass recorded in this
            # same call counts.
            due = self.live_check_due(task)
            if due:
                raise ValueError(due)
        if labels is not None:
            task['labels'] = task_labels(labels)
        moved_images = []
        if attachments is not None:
            wanted = task_attachments(attachments)
            store = getattr(getattr(self.runtime, 'host', None), 'store', None)
            upload_dir = getattr(store, 'upload_dir', None)
            missing = [a['url'] for a in wanted if upload_dir and not (upload_dir / a['url'][len('/uploads/'):]).is_file()]
            if missing:
                raise ValueError(f'Upload not found: {missing[0]}')
            task['attachments'], earlier, moved_images = arrange_attachments(
                wanted, (previous or {}).get('attachments', []), task.get('earlier_attachments', []))
            if earlier:
                task['earlier_attachments'] = earlier
            else:
                task.pop('earlier_attachments', None)
        if depends_on is not None:
            if not isinstance(depends_on, list) or any(not isinstance(x, str) for x in depends_on):
                raise ValueError('depends_on must be a list of task IDs')
            dependencies = list(dict.fromkeys(depends_on))
            known = {t['id']: t for t in tasks}
            if any(x not in known or x == task['id'] for x in dependencies):
                raise ValueError('Dependencies must reference other existing tasks')
            pending, seen = list(dependencies), set()
            while pending:
                dependency = pending.pop()
                if dependency == task['id']:
                    raise ValueError('Task dependencies cannot form a cycle')
                if dependency not in seen:
                    seen.add(dependency)
                    pending.extend(known.get(dependency, {}).get('depends_on', []))
            task['depends_on'] = dependencies
        if not human and task['status'] == 'done' and not closing:
            if task.get('transfer') or task.get('wait'):
                raise ValueError('Resolve pending ownership transfer or human wait before marking done')
            unfinished = [t['id'] for t in tasks if t['id'] in task.get('depends_on', []) and t['status'] != 'done']
            reviews = [r['id'] for r in self.state.get('reviews', []) if r['task_id'] == task['id'] and r['status'] in ('pending', 'changes_requested')]
            # An approval whose files moved on afterwards blocks, unless the task names the release it shipped in:
            # then the approved work is what was installed and the later bytes belong to newer work (opus 64b79f99).
            shipped = bool((task.get('evidence') or {}).get('installation', '').strip())
            changed_reviews = [] if shipped else [
                r['id'] for r in self.state.get('reviews', []) if r['task_id'] == task['id']
                and r['status'] == 'approved' and self.records.changed_files(r)]
            if unfinished or reviews or changed_reviews:
                hint = (f' Approvals {changed_reviews} were given before their files changed: record evidence.installation '
                        'if that work shipped, or ask for a re-review.') if changed_reviews else ''
                raise ValueError(f'Resolve requested reviews {reviews + changed_reviews} and unfinished dependencies '
                                 f'{unfinished} before marking done; the user can override.{hint}')
        if task['owner'] and task['owner'] not in self.runtime.states:
            raise ValueError('Choose a registered project participant as owner')
        if not human and task['owner'] == actor and task['status'] in ('working', 'review') and not task['paths']:
            # Discussion/research tasks can have no file scope; edits still need a scoped task.
            task['details'] = task['details'] or 'Discussion/research task; claim file paths before editing.'
        if task['owner'] and task['status'] != 'done':
            for other in tasks:
                if other['id'] == task['id'] or not other['owner'] or other['status'] == 'done':
                    continue
                if any(self.scopes_overlap(task, a, other, b) for a in task['paths'] for b in other['paths']):
                    raise ValueError('Scope already reserved; existing task: ' + json.dumps({k: other[k] for k in ('id', 'title', 'owner', 'status')}) + '. Coordinate or delegate it first.')
        # Ownership and status moves are recorded automatically so the board history
        # shows who did what even when no check-in text accompanies the change.
        events = []
        if action == 'create':
            events.append(f"created for {task['owner']}" if task['owner'] and task['owner'] != actor else 'created')
        elif action == 'delegate' and task.get('transfer'):
            events.append(f"transfer proposed to {task['transfer']['to']}; current owner retained until accepted")
        elif task['owner'] != previous['owner']:
            events.append('claimed' if task['owner'] == actor else f"assigned to {task['owner']}" if task['owner'] else 'released')
        if accepted:
            events.append(f"live check {accepted['result']} on manifest {accepted['manifest_sha'][:12]}")
        if closing:
            events.append(f"closed as {closing['reason']}: {closing['pointer']}")
        elif previous and task['status'] != previous['status']:
            events.append(f"moved to {task['status']}")
        before = {a['url'] for a in (previous or {}).get('attachments', [])}
        after = {a['url'] for a in task.get('attachments', [])}
        moved_urls = {a['url'] for a in moved_images}
        attached = (after | moved_urls) - before
        removed = before - after - moved_urls
        if attached:
            events.append(f"attached {len(attached)} image{'s' if len(attached) > 1 else ''}")
        if moved_images:
            events.append(f"moved {len(moved_images)} older image{'s' if len(moved_images) > 1 else ''} to earlier images")
        if removed:
            events.append(f"removed {len(removed)} image{'s' if len(removed) > 1 else ''}")
        if update or events:
            entry = {'by': actor, 'text': update if update else '; '.join(events), 'time': time.time()}
            if events:
                entry['event'] = '; '.join(events)
            if not update:
                # Lifecycle-only entries are history, not progress check-ins.
                entry['auto'] = True
            task['updates'].append(entry)
            task['updates'] = task['updates'][-TASK_HISTORY:]
        task['updated'] = time.time()
        self.track_work(task, previous, actor)
        if action == 'create':
            tasks.append(task)
        else:
            tasks[tasks.index(next(t for t in tasks if t['id'] == task['id']))] = task
        if closing:
            self.apply_closure(task, tasks, closing)
        elif previous and task['status'] == 'done' and previous['status'] != 'done':
            # A finished dependency is new work for each waiting dependent's owner.
            for other in tasks:
                if task['id'] in other.get('depends_on', []) and other['status'] != 'done':
                    self.bump_work(other, actor, f"dependency {task['id']} finished")
            # E5: a finished task takes no more review rounds, so its open scope-cap item asks the user nothing
            # (opus dc60d194). Withdrawn, not answered: no rounds are granted and no stop is recorded. A closure
            # already cancels it with the task's other open follow-ups (apply_closure).
            now = time.time()
            for item in self.state.get('followups', []):
                if item['task_id'] == task['id'] and item['status'] == 'open' and item.get('kind') == 'scope_cap':
                    item.update(status='cancelled', result=f"Task {task['id']} is done; no decision needed.", updated=now)
        self.save()
        view = self.task_view(task)
        if moved_images:
            # Reported once, in this call's receipt; the stored record keeps them in earlier_attachments.
            view['moved_to_earlier'] = [{'url': a['url'], 'reason': a['reason'],
                                         **({'superseded_by': a['superseded_by']} if a.get('superseded_by') else {})} for a in moved_images]
        return view

    def track_work(self, task, previous, actor):
        """work_rev counts semantic changes made by anyone other than the owner (ready-task wake r2.1 point 3).
        The owner's own edits never advance it, so an owner cannot re-arm its own consumed wake."""
        task['work_rev'] = (previous or {}).get('work_rev', 0)
        if previous is None:
            return
        changed = [f for f in SEMANTIC_FIELDS if task.get(f) != previous.get(f)]
        if not changed:
            return
        # External means neither the previous nor the new owner (security 5a6ba981): an owner that releases
        # and re-claims its own task, or a recipient accepting a transfer, is not new work from someone else.
        if actor not in {previous.get('owner'), task.get('owner')}:
            self.bump_work(task, actor, f"external change by {actor}: {', '.join(changed)}")
        elif task.get('next_action_state') == 'ready' and previous.get('next_action_state') != 'ready':
            task['work_cause'] = 'initial readiness'

    @staticmethod
    def acceptance_entry(task, actor, action, value):
        """One live-check result for one installed manifest, by the task's current owner or the user; the writer and
        time are the room's, never the caller's. Unstructured evidence.acceptance is left as it is."""
        if action != 'update':
            raise ValueError('Record acceptance with an update')
        if actor != 'user' and actor != task.get('owner'):
            raise ValueError("Only the task's owner or the user can record its live check")
        if not isinstance(value, dict) or not {'manifest_sha', 'result'} <= set(value) <= {'manifest_sha', 'result', 'text'}:
            raise ValueError('acceptance needs manifest_sha and result, and may add text')
        if not isinstance(value['manifest_sha'], str) or not MANIFEST_SHA.fullmatch(value['manifest_sha']):
            raise ValueError('acceptance.manifest_sha is the 64-hex installed manifest')
        if value['result'] not in ACCEPTANCE_RESULTS:
            raise ValueError('acceptance.result is passed or failed')
        entry = {'manifest_sha': value['manifest_sha'], 'result': value['result'], 'by': actor, 'time': time.time()}
        if value.get('text') is not None:
            if not isinstance(value['text'], str) or len(value['text']) > 2400:
                raise ValueError('acceptance.text is at most 2400 characters')
            entry['text'] = value['text']
        return entry

    def closure(self, task, tasks, actor, reason, pointer):
        """Check a closure without changing anything; apply_closure makes the changes once the update is accepted.
        A closure claims no delivery, so it needs a reason and a pointer instead of reviews or installation."""
        pointer = str(pointer or '').strip()
        if reason not in CLOSE_REASONS:
            raise ValueError('close_reason must be one of ' + ', '.join(CLOSE_REASONS))
        board = {t['id']: t for t in tasks}
        is_task = pointer in board or bool(self.archived_task(pointer))
        is_decision = any(n.get('id') == pointer and n.get('kind') == 'decision' for n in self.state.get('notes', []))
        if not pointer or pointer == task['id'] or not (is_task or is_decision):
            raise ValueError('close_pointer must name another task or a decision note')
        if reason in CARRIED_ON and not is_task:
            raise ValueError(f'A {reason} task points at the task that carries the work on')
        dependents = {t['id'] for t in tasks if task['id'] in t.get('depends_on', []) and t['status'] != 'done'}
        if dependents:
            if reason not in CARRIED_ON or pointer not in board:
                raise ValueError(f'Tasks {sorted(dependents)} depend on this one; close it as duplicate or superseded '
                                 'pointing at the task they should wait on, or update them first')
            # Re-pointing them must not make the carrying task wait on its own dependents.
            pending, seen = [pointer], set()
            while pending:
                current = pending.pop()
                if current in dependents:
                    raise ValueError(f'{pointer} already depends on {current}, which waits on this task')
                if current not in seen:
                    seen.add(current)
                    pending.extend(board.get(current, {}).get('depends_on', []))
        return {'reason': reason, 'pointer': pointer, 'by': actor, 'time': time.time()}

    def apply_closure(self, task, tasks, closing):
        """Dependents wait on the task that carries the work on; open requests on the closed task are cancelled."""
        note = f"Task {task['id']} closed as {closing['reason']}: {closing['pointer']}"
        for other in tasks:
            if task['id'] in other.get('depends_on', []) and other['status'] != 'done':
                other['depends_on'] = list(dict.fromkeys(
                    closing['pointer'] if d == task['id'] else d for d in other['depends_on']))
                self.bump_work(other, closing['by'], note)
        now = time.time()
        for review in self.state.get('reviews', []):
            if review['task_id'] == task['id'] and review['status'] in ('pending', 'changes_requested'):
                review.update(status='cancelled', cancellation=note, updated=now)
        for item in self.state.get('followups', []):
            if item['task_id'] == task['id'] and item['status'] == 'open':
                item.update(status='cancelled', result=note, updated=now)

    def bump_work(self, task, actor, cause):
        """New work for the task's owner from someone else. Accepts a task record or a task id; callers save."""
        if isinstance(task, str):
            task = next((t for t in self.state['tasks'] if t['id'] == task), None)
        if not task or task['status'] == 'done' or not actor or actor == task.get('owner'):
            return False
        task['work_rev'] = task.get('work_rev', 0) + 1
        task['work_cause'] = cause
        return True

    def markup(self, actor, channel, chain, image, marks=None, summary='', task_id='', post=True):
        """Share an uploaded image, optionally with numbered marks drawn onto a new PNG."""
        url = upload_url(image)
        summary = bounded(summary or '', 'markup')
        upload_dir = self.runtime.host.store.upload_dir
        source = upload_dir / url[len('/uploads/'):]
        if not source.is_file():
            raise ValueError(f'Upload not found: {url}')
        if not isinstance(post, bool):
            raise ValueError('post must be true or false')
        requested_url = url
        if marks:
            # Prefer a clean original, retaining an explicit account of any change of source.
            # Existing task metadata also recovers originals for older chat posts that lost source.
            known, invalid_sources = {}, set()
            records = [*self.state.get('tasks', []), *self.runtime.host.store.get_recent(500, channel=channel)]
            for record in records:
                for item in record.get('attachments', []):
                    if not isinstance(item, dict) or not item.get('source'):
                        continue
                    try:
                        derived = upload_url(item.get('url', ''))
                    except ValueError:
                        continue
                    try:
                        original = upload_url(item['source'])
                    except ValueError:
                        invalid_sources.add(derived)
                        continue  # Unchecked chat metadata must not replace a valid source link.
                    known[derived] = original
            seen = set()
            fallback = ''
            while url in known:
                if url in seen or len(seen) >= 16:
                    raise ValueError('Image source chain is invalid; share the unmarked original.')
                seen.add(url)
                next_url = known[url]
                next_source = upload_dir / next_url[len('/uploads/'):]
                if not next_source.is_file():
                    fallback = 'a saved original is missing'
                    break
                url, source = next_url, next_source
            if not fallback and url in invalid_sources and url not in known:
                fallback = 'the saved original reference is invalid'
            # Imported on use so the room still starts if Pillow is missing; only markup fails.
            from room_markup import render_markup
            data, note = render_markup(source.read_bytes(), marks, summary)
            provenance = []
            if url != requested_url:
                base = 'available source' if fallback else 'original'
                provenance.append(f'Marked on the {base} of {requested_url}; earlier marks remain in {requested_url}.')
            if fallback:
                provenance.append(f'Original unavailable: {fallback}. Marked on the last available image {url}; existing marks in it remain.')
            note = '\n'.join([*provenance, note])
            filename = f'{uuid.uuid4().hex[:8]}.png'
            (upload_dir / filename).write_bytes(data)
            attachment = {'url': '/uploads/' + filename, 'name': f'{source.stem}-markup.png', 'note': note, 'source': url}
        else:
            # Plain sharing keeps the original upload; the summary travels as its note.
            note = str(summary or '').strip()
            attachment = {'url': url, 'name': source.name, **({'note': note} if note else {})}
        result = {'attachment': attachment}
        if url != requested_url:
            result['based_on'] = requested_url
        if task_id:
            task = next((t for t in self.state['tasks'] if t['id'] == task_id), None)
            if not task:
                raise ValueError('Task was not found')
            # task() applies ownership, the card limit and history like any other attachment change.
            updated = self.task(actor, 'update', task_id=task_id, attachments=[*task.get('attachments', []), attachment])
            result['task_id'] = task_id
            if updated.get('moved_to_earlier'):
                result['moved_to_earlier'] = updated['moved_to_earlier']
                result['card_note'] = (f'The card shows the {CARD_IMAGES} newest images; the listed older ones moved to earlier images '
                                       '(room_task get lists them). No file was deleted.')
        if post:
            text = attachment.get('note') or f"Shared {attachment['name']}"
            # Color metadata belongs to the attachment viewer, not the chat prose.
            text = re.sub(r' \[color=#[0-9a-fA-F]{6}\](?=\n|$)', '', text)
            message = self.runtime.host.store.add(actor, text, channel=channel,
                attachments=[dict(attachment)],
                metadata={'no_route': True, 'chain': chain})
            result['message_id'] = message['id']
        return result

    def release_binding(self, actor, value, supersedes=''):
        """A decision's release_checks (Astra 93fed9db/78336e2a): one installed manifest and the tasks whose owners
        must check it live. Only the room's selected manager or the user binds; each task's owner is pinned now,
        so a later transfer cannot substitute who accepts."""
        manager = getattr(getattr(self.runtime, 'manager_wakes', None), 'selected_manager', lambda: None)()
        if actor != 'user' and actor != manager:
            raise ValueError("Only the room's manager or the user can bind release checks")
        return self.release_checks(value, supersedes)

    def room_lead(self):
        """The room's selected manager (Team > lead), or None."""
        return (self.state.get('responders') or {}).get('lead') or None

    def active_release_checks(self, manifest_sha):
        """The one active decision binding this manifest's live checks, or None."""
        return next((n for n in self.state['notes'] if n.get('kind') == 'decision'
                     and n.get('status', 'active') == 'active'
                     and (n.get('release_checks') or {}).get('manifest_sha') == manifest_sha), None)

    def release_checks(self, value, supersedes=''):
        """release_checks' shared validation, for a person's binding and the room's own (bind_installed)."""
        if not isinstance(value, dict) or set(value) != {'manifest_sha', 'tasks'}:
            raise ValueError('release_checks needs exactly manifest_sha and tasks')
        sha, ids = value['manifest_sha'], value['tasks']
        if not isinstance(sha, str) or not MANIFEST_SHA.fullmatch(sha):
            raise ValueError('release_checks.manifest_sha is the 64-hex installed manifest')
        if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_RELEASE_CHECK_TASKS
                or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids)):
            raise ValueError(f'release_checks.tasks lists 1-{MAX_RELEASE_CHECK_TASKS} distinct task ids')
        active = self.active_release_checks(sha)
        if active and active['id'] != supersedes:
            # release_request.acceptance needs exactly one active checklist per manifest (backend 7a62b62a C1).
            raise ValueError(f"Decision {active['id']} already binds this manifest's live checks; "
                             'supersede it to replace them')
        board = {t['id']: t for t in self.state['tasks']}
        pinned = []
        for task_id in ids:
            task = board.get(task_id)
            if task is None or task.get('closure'):
                raise ValueError(f'Release check task {task_id} is not an open or delivered task on the board')
            if not task.get('owner'):
                raise ValueError(f'Release check task {task_id} has no owner to accept it')
            pinned.append({'id': task_id, 'owner': task['owner']})
        return {'manifest_sha': sha, 'tasks': pinned}

    def note(self, actor, kind, text, supersedes='', resolves='', release_checks=None):
        if kind not in ('progress', 'decision', 'reference', 'blocker', 'handoff') or not str(text).strip():
            raise ValueError('Provide a progress, decision, reference, blocker, or handoff update')
        if release_checks is not None:
            if kind != 'decision':
                raise ValueError('release_checks belong on a decision')
            release_checks = self.release_binding(actor, release_checks, supersedes)
        text = bounded(text, 'note')
        if supersedes and resolves:
            raise ValueError('Choose supersedes or resolves, not both')
        old = None
        if supersedes or resolves:
            old = next((n for n in self.state['notes'] if n.get('id') == (supersedes or resolves)), None)
            if not old or old['kind'] not in ('decision', 'reference') or old.get('status', 'active') != 'active':
                raise ValueError('Choose an active decision or reference id')
            if old.get('by') == 'user' and actor != 'user':
                raise ValueError('Only the user can retire a user decision or reference')
            if kind != old['kind']:
                raise ValueError('Replace or resolve a note with the same kind')
        note = {'id': uuid.uuid4().hex[:8], 'by': actor, 'kind': kind, 'text': text, 'time': time.time()}
        if kind in ('decision', 'reference'):
            note['status'] = 'active'
        if release_checks:
            note['release_checks'] = release_checks
        if old:
            old.update(status='superseded' if supersedes else 'resolved', replaced_by=note['id'])
            note['supersedes' if supersedes else 'resolves'] = old['id']
        self.state['notes'].append(note)
        decisions = [n for n in self.state['notes'] if n['kind'] in ('decision', 'reference')]
        recent = [n for n in self.state['notes'] if n['kind'] not in ('decision', 'reference')][-100:]
        self.state['notes'] = sorted(decisions + recent, key=lambda n: n['time'])
        self.save()
        return note

    def bind_installed(self, manifest_sha, task_ids, text):
        """L1's binder (Docs/lead-independence.md r6, harness f9bb5b48): the installed release's live checks as a
        `system` decision. Server-only: called from the enforcement tick, never from a tool or HTTP route, so note()'s
        actor gate is unchanged. The same validation and one-checklist rule as a person's binding apply, and a refusal
        is a ValueError the tick turns into a lead item."""
        if not str(text).strip():
            raise ValueError('Provide the binding decision text')
        checks = self.release_checks({'manifest_sha': manifest_sha, 'tasks': list(task_ids)})
        note = {'id': uuid.uuid4().hex[:8], 'by': 'system', 'kind': 'decision', 'text': bounded(text, 'note'),
                'time': time.time(), 'status': 'active', 'release_checks': checks}
        self.state['notes'].append(note)
        self.save()
        return note

    def wait_refs(self, value, own_id):
        """wait_on (C6a): up to MAX_WAIT_ON references the task waits for, each {task|review|followup: id} or
        {release: version}. Ids must exist now; an empty list clears it."""
        shapes = 'each entry is {task: id}, {review: id}, {followup: id} or {release: "0.12.55"}'
        if not isinstance(value, list) or len(value) > MAX_WAIT_ON:
            raise ValueError(f'wait_on lists up to {MAX_WAIT_ON} references; {shapes}')
        refs = []
        for ref in value:
            if not isinstance(ref, dict) or len(ref) != 1:
                raise ValueError(f'wait_on: {shapes}')
            (kind, ident), = ref.items()
            if kind == 'release':
                if not isinstance(ident, str) or not RELEASE_VERSION.fullmatch(ident):
                    raise ValueError('wait_on release is a version such as "0.12.55"')
            elif kind in WAIT_COLLECTIONS:
                found = isinstance(ident, str) and any(x.get('id') == ident for x in self.state.get(WAIT_COLLECTIONS[kind], []))
                if kind == 'task' and isinstance(ident, str) and not found:
                    found = bool(self.archived_task(ident))  # archived means finished: satisfied at once
                if not found:
                    raise ValueError(f'wait_on {kind} {ident} was not found')
                if kind == 'task' and ident == own_id:
                    raise ValueError('A task cannot wait on itself')
            else:
                raise ValueError(f'wait_on: {shapes}')
            if {kind: ident} not in refs:
                refs.append({kind: ident})
        return refs

    def live_check_due(self, task):
        """L1 gate (backend 7a62b62a C2): why an agent may not yet end or hand on this task, or ''. It applies while the
        installed manifest's active checklist pins the task and its latest entry for that manifest isn't a pass by
        the pinned owner or the user, as release_request.acceptance counts it."""
        deployment = getattr(self.runtime, 'deployment', None)
        current = getattr(deployment, 'version', None) or {}
        manifest = current.get('manifest_sha')
        binding = self.active_release_checks(manifest) if manifest else None
        pin = next((p for p in (binding or {}).get('release_checks', {}).get('tasks', []) if p['id'] == task['id']), None)
        if not pin:
            return ''
        latest = next((a for a in reversed(task.get('acceptance_log') or []) if a.get('manifest_sha') == manifest), None)
        if latest and latest.get('result') == 'passed' and latest.get('by') in (pin['owner'], 'user'):
            return ''
        return (f"L1: record your live check of {current.get('version') or current.get('id') or 'the installed release'} "
                f"first (room_task update acceptance with manifest_sha {manifest}), or the lead supersedes the checklist "
                "without it; the user can override.")

    def primary_repository_key(self):
        from project_registry import repository_key
        project = getattr(self.runtime, 'project', None)
        return repository_key(project) if project else ''

    def task_repository_key(self, task):
        return (task.get('repository_key') or self.state.get('scope_repository') or
                self.project_record().get('initial_repository_key') or self.primary_repository_key())

    def task_scope(self, raw):
        raw = str(raw).strip()
        if ':' not in raw:
            return scope(raw)
        key, relative = raw.split(':', 1)
        keys = {self.primary_repository_key()}
        for state in self.runtime.states.values():
            keys.update(state.get('repositories', {}))
        if not key or key not in keys:
            raise ValueError('Use an attached repository key before the colon')
        return key + ':' + scope(relative)

    def scoped_parts(self, task, raw):
        if ':' in raw:
            key, relative = raw.split(':', 1)
        else:
            key = self.task_repository_key(task)
            relative = raw
        return key, os.path.normcase(scope(relative)).replace('\\', '/')

    def scopes_overlap(self, task, first, other, second):
        key_a, path_a = self.scoped_parts(task, first)
        key_b, path_b = self.scoped_parts(other, second)
        return key_a == key_b and overlaps(path_a, path_b)

    def check_repository_removal(self, key):
        from project_registry import RepositoryInUse
        for task in self.state.get('tasks', []):
            if task.get('owner') and task.get('status') != 'done' and any(
                    self.scoped_parts(task, path)[0] == key for path in task.get('paths', [])):
                raise RepositoryInUse(f"Repository has reserved paths on task {task['id']}; release or re-scope them before removing it")

    def read(self, run, section='overview', offset=0, limit=4000, query='', after_id=0, task_id='', owner='', status='', *, changes=None):
        limit = max(100, min(6000, int(limit)))
        offset = max(0, int(offset))
        if section == 'spec':
            text = self.state['spec']
        elif section == 'review_artifact':
            # task_id identifies the task, query is '<review-id>:<relative-file>'.
            review_id, separator, relative = query.partition(':')
            item = self.records.record('reviews', review_id)
            if task_id and item['task_id'] != task_id:
                raise ValueError('Review does not belong to this task')
            entry = next((f for f in item.get('snapshot', {}).get('files', []) if f['path'] == relative), None)
            if not separator or not entry:
                raise ValueError('Use query review-id:relative-file from the review snapshot')
            path = self.path.parent / 'review-artifacts' / item['id'] / entry['sha256']
            text = path.read_bytes().decode('utf-8', errors='replace')
        elif section == 'tasks':
            # Full details remain available on demand; returned pages are still bounded.
            text = json.dumps([t for t in self.state['tasks'] if (not task_id or t['id'] == task_id)
                and (not owner or t['owner'] == owner) and (not status or t['status'] == status)], ensure_ascii=False)
        elif section in ('reviews', 'followups'):
            text = json.dumps([x for x in self.state.get(section, []) if (not task_id or x['task_id'] == task_id)
                and (not owner or x.get('reviewer', x.get('assigned_to')) == owner)
                and (not status or x['status'] == status)], ensure_ascii=False)
        elif section == 'inbox':
            inbox = self.records.inbox(run.name)
            text = json.dumps(inbox, ensure_ascii=False)
        elif section == 'participants':
            text = json.dumps({'paused': self.runtime.paused, 'participants': {
                name: {**{k: s.get(k) for k in ('label', 'handle', 'provider', 'state', 'paused', 'detail', 'worktree', 'archived')},
                       'background': self.runtime.profiles.items.get(name, {}).get('background', '')}
                for name, s in self.runtime.states.items()}}, ensure_ascii=False)
        elif section == 'memory':
            text = json.dumps(self.state['notes'], ensure_ascii=False)
        elif section == 'messages':
            text = '\n'.join(message_text(m, self.origin(), self.uploads()) for m in self.visible(run, self.runtime.host.store.get_since(int(after_id), channel=run.channel)))
        elif section == 'patches':
            if changes is None:
                raise ValueError('Patch collection must be supplied by the async caller')
            text = json.dumps(changes, ensure_ascii=False)
        elif section == 'overview':
            text = json.dumps({'conversation': 'Current chat is available independently of an optional spec or repository.',
                # 'project' used to be the repository path, so a conversation project read as "no project".
                'project': self.project_label(),
                'repository': str(self.runtime.project) if self.runtime.project else 'none attached',
                'repositories': self.project_record().get('repositories', []),
                'spec_length': len(self.state['spec']), 'tasks': [
                {**{k: t[k] for k in ('id', 'title', 'owner', 'status', 'paths')}, 'labels': t.get('labels', []),
                 'images': len(t.get('attachments', [])),
                 **({'earlier_images': len(t['earlier_attachments'])} if t.get('earlier_attachments') else {})}
                for t in self.state['tasks'] if t['status'] != 'done'],
                'limits': self.limits}, ensure_ascii=False)
        else:
            raise ValueError('Choose overview, spec, tasks, memory, messages, patches, participants, inbox, reviews or followups')
        if query and section != 'review_artifact':
            found = text.lower().rfind(str(query).lower())
            if found < 0:
                return {'text': 'No matching project text found.', 'next_offset': None, 'total_chars': len(text)}
            offset = max(0, found - 300)
        page = text[offset:offset+limit]
        # A clipped page must not acknowledge records/messages the model never received.
        if section == 'messages':
            for m in self.visible(run, self.runtime.host.store.get_since(int(after_id), channel=run.channel)):
                if message_text(m, self.origin(), self.uploads()) in page:
                    self.observe_message(run, m)
        elif section in ('inbox', 'reviews', 'followups', 'tasks'):
            for collection in ('reviews', 'followups', 'tasks'):
                for item in self.state.get(collection, []):
                    if json.dumps(item, ensure_ascii=False) in page:
                        run.receipts.add(receipt_key({'ref': {'collection': collection,
                            'id': item['id'], 'updated': item['updated']}}))
            if section == 'inbox':
                # Inbox tasks are summaries, so they never match a raw record above.
                for summary in inbox['transfers'] + inbox['ready_tasks']:
                    if json.dumps(summary, ensure_ascii=False) in page:
                        run.receipts.add(receipt_key({'ref': {'collection': 'tasks',
                            'id': summary['id'], 'updated': summary['updated']}}))
        return {'text': page, 'offset': offset, 'next_offset': offset+limit if offset+limit < len(text) else None,
                'total_chars': len(text)}

    def observe_message(self, run, message):
        run.receipts.add(receipt_key({'message_id': message['id']}))
        ref = message.get('metadata', {}).get('coordination_ref')
        if ref:
            run.receipts.add(receipt_key({'ref': ref}))

    def visible(self, run, messages):
        # Open team-decision positions stay hidden from other participants until the stage closes.
        decisions = getattr(self.runtime, 'decisions', None)
        return [m for m in messages if not (decisions and decisions.hidden(m, run.name))]

    def context(self, run, changes=None):
        # A local model's context window can lower the room limit for its turns (room_runtime.local_input_budget).
        maximum = min(self.limits['input_chars'], getattr(run, 'input_budget', None) or self.limits['input_chars'])
        task_limit, spec_limit, memory_limit = int(maximum * .18), int(maximum * .28), int(maximum * .12)
        # Views carry the observed execution state; your own tasks first, by priority (1 highest).
        tasks = [self.task_view(t) for t in self.state['tasks'] if t['status'] != 'done']
        tasks.sort(key=lambda t: (t['owner'] != run.name, t.get('priority', 3), -t['updated']))
        now = time.time()
        def line(t):
            priority = f" priority={t['priority']}" if t.get('priority') else ''
            text = (f"{t['id']} [{t['status']}; {t['execution_state'].replace('_', ' ')}]{priority} owner={t['owner'] or 'unclaimed'} "
                    f"paths={','.join(t['paths'])} {t['title']}: {clipped(t['details'], 400)}")
            if t.get('transfer'):
                text += f"\n  transfer proposed to {t['transfer']['to']}; {t['owner'] or 'nobody'} owns it until {t['transfer']['to']} accepts"
            if t.get('next_action'):
                text += f"\n  next action: {clipped(t['next_action'], 400)}"
            if t.get('wait'):
                waiting = 'waiting for the user' if t['execution_state'] == 'waiting_on_human' else 'answered or closed; owner must act'
                text += f"\n  waits on {t['wait']['kind']} {t['wait']['record_id']} ({waiting})"
            checkins = [u for u in t['updates'] if not u.get('auto')]
            if checkins:
                last = checkins[-1]
                text += f"\n  last check-in {age(now - last['time'])} ago by {last['by']}: {clipped(last['text'], 300)}"
            if t['updates'] and t['updates'][-1].get('auto'):
                last = t['updates'][-1]
                text += f"\n  last change {age(now - last['time'])} ago by {last['by']}: {last['event']}"
            if t['stale']:
                text += f"\n  STALE: no check-in for {age(now - t['updated'])}; owner should update, release or delegate it"
            if t.get('depends_on'):
                text += f"\n  depends_on={','.join(t['depends_on'])}"
            if t.get('labels'):
                text += f"\n  labels={','.join(t['labels'])}"
            if t.get('attachments'):
                earlier = len(t.get('earlier_attachments', []))
                text += (f"\n  images={len(t['attachments'])}{f' (+{earlier} earlier)' if earlier else ''}"
                         " (room_task get for urls/notes; room_image to view)")
            return text
        def brief(t):
            priority = f" priority={t['priority']}" if t.get('priority') else ''
            paths = ','.join(t['paths'][:4]) + (f",+{len(t['paths']) - 4} more" if len(t['paths']) > 4 else '')
            text = (f"{t['id']} [{t['status']}; {t['execution_state'].replace('_', ' ')}]{priority} "
                    f"owner={t['owner'] or 'unclaimed'} paths={paths} {clipped(t['title'], 140)}")
            if t.get('transfer'):
                text += f" (transfer proposed to {t['transfer']['to']})"
            if not t['owner'] and t.get('labels'):
                text += f" labels={','.join(t['labels'])}"
            return text
        # Only this agent's tasks are spelled out. Other owners' work stays one line each, so it is
        # visible for collision checks without reading as the agent's own (or costing a fifth of the prompt).
        mine = [t for t in tasks if t['owner'] == run.name or (t.get('transfer') or {}).get('to') == run.name]
        others = [t for t in tasks if t not in mine]
        # Own tasks may use most of the board budget, but never all of it: other owners' lines are the
        # collision check, so they keep the remainder even when the agent owns many detailed tasks.
        own = 'Your tasks:\n' + '\n'.join(line(t) for t in mine) if mine else 'Your tasks: none'
        own = clipped(own, int(task_limit * .6)) if others else own
        rest = ('Other open tasks, one line each (room_task get for details):\n' + '\n'.join(brief(t) for t in others)
                if others else '')
        ledger = (own + ('\n' + clipped(rest, task_limit - len(own) - 1) if rest else '')) if tasks else ''
        # Only current decisions; superseded and resolved ones stay in room_read(section="memory").
        # References (caches, helper scripts, paths) stay listed while active, so nobody redoes setup.
        current = lambda kind: [n for n in self.state['notes'] if n['kind'] == kind and n.get('status', 'active') == 'active']
        recent_decisions = current('decision')[-8:]
        references = current('reference')[-12:]
        recent_updates = [n for n in self.state['notes'] if n['kind'] not in ('decision', 'reference')][-4:]
        decisions = '\n'.join(f"{n['kind']}{' ' + n['id'] if n.get('id') else ''} {n['by']}: {clipped(n['text'], 1600)}"
                              for n in references + recent_decisions + recent_updates)
        chat_limit = int(maximum * .2)
        previous = run.previous_session.get('context', {})
        cursor = previous.get('message_id', 0)
        messages = (self.runtime.host.store.get_since(cursor, channel=run.channel) if run.previous_session
                    else self.runtime.host.store.get_recent(40, channel=run.channel))
        latest_id = messages[-1]['id'] if messages else cursor
        messages = self.visible(run, messages)
        handle = (self.runtime.states.get(run.name) or {}).get('handle') or run.name
        # Mentions by internal name or by public @handle both count; profiles allow them to differ.
        to_reader = lambda text: addressed(text, run.name) or (handle != run.name and addressed(text, handle))
        def routine(m):
            # Another agent's mid-turn progress, task receipts and progress notes stay visible to the user,
            # but the board and notes already carry them, so they do not use up peers' chat window. Markup,
            # blockers, handoffs, decisions, references and review/follow-up posts are kept.
            meta = m.get('metadata') or {}
            if m['sender'] == run.name or m['sender'] not in self.runtime.states or to_reader(m['text']):
                return False
            if meta.get('phase') == 'commentary':
                return True
            tool_post = (meta.get('no_route') and not meta.get('phase') and not meta.get('coordination_ref')
                         and not m.get('attachments'))
            return bool(tool_post and (TASK_RECEIPT.fullmatch(m['text']) or m['text'].startswith('progress: ')))
        relevant = [m for m in messages if m['sender'] != 'system' and not routine(m)
                    and not (run.previous_session and m['sender'] == run.name)]
        request, in_request = self.request_text(run, min(8000, maximum // 3))
        # Keep whole messages, newest first, so a sender and message ID are never sliced off. Messages
        # addressed to this agent are not cut to an excerpt; the one being answered appears once, in full, below.
        lines, used, shown = [], 0, set()
        for m in reversed(relevant[-12:]):
            if m['id'] in in_request:
                entry = f"#{m['id']} {m['sender']}: [Addressed to you; full text under Current message below.]"
            elif to_reader(m['text']):
                entry = continued(m, self.origin(), max(1400, chat_limit // 2), self.uploads())
            else:
                entry = clipped(message_text(m, self.origin(), self.uploads()), 1400)
            if lines and used + len(entry) + 1 > chat_limit:
                break
            lines.insert(0, entry if len(entry) <= chat_limit else clipped(entry, chat_limit))
            shown.add(m['id'])
            if lines[0] == message_text(m, self.origin(), self.uploads()):
                self.observe_message(run, m)
            used += len(lines[0]) + 1
        history = '\n'.join(lines)
        if len(lines) < len(relevant):
            missed = [f"#{m['id']} {m['sender']}" for m in relevant
                      if m['id'] not in shown and m['id'] not in in_request and to_reader(m['text'])]
            if missed:
                history = (f"[Also addressed to you and not shown here: {', '.join(missed[-12:])}. Read them with "
                           f'room_read(section="messages", after_id={cursor}) before acting on older work.]\n' + history)
            history = f'[Older unread messages remain available via room_read(section="messages", after_id={cursor}).]\n' + history
        if run.previous_session and previous and set(previous) <= {'message_id'}:
            # Saved as cursor_only: the session was compacted since its last turn, and its summary may have dropped
            # earlier chat as well as the blocks (harness 0a48a156 note).
            history = ('[Your session was compacted since your last turn; earlier messages remain available via '
                       'room_read(section="messages").]\n' + history)
        peer_limit = int(maximum * .12)
        changes = changes if changes is not None else {}
        # Every changed worktree is summarised; full hunks are kept only for files that overlap this
        # agent's own reservations, instead of an equal, unreadable slice of every diff.
        reserved = {(t.get('repository_key') or '', p) for t in mine for p in t['paths']}
        views = [patch_view(name, diff, reserved) for name, diff in sorted(changes.items()) if diff and name != run.name]
        summaries = '\n'.join(v['summary'] for v in views)
        touching = '\n'.join(v['overlap'] for v in views if v['overlap'])
        # Summaries first; overlapping hunks get what is left, so one large overlap cannot hide other worktrees.
        patches = summaries + (('\nChanges overlapping your reserved paths:\n'
                                + clipped(touching, max(400, peer_limit - len(summaries) - 60))) if touching else '')
        values = {'spec': self.state['spec'], 'tasks': ledger, 'memory': decisions, 'patches': patches,
                  'workspace': self.execution_context(run)}
        fingerprints = {name: hashlib.sha256(text.encode('utf-8')).hexdigest() for name, text in values.items()}
        # The pending request's age moves every minute; only a real change resends the block.
        fingerprints['workspace'] = hashlib.sha256(re.sub(r'\d+h?\d*m ago', '', values['workspace']).encode('utf-8')).hexdigest()
        # Relevance-scoped: line counts in an unrelated peer diff, or an unrelated task's check-in,
        # do not invalidate the whole block for every resumed agent.
        fingerprints['patches'] = hashlib.sha256(json.dumps([[v['name'], v['files'], v['overlap']] for v in views])
                                                 .encode('utf-8')).hexdigest()
        # Relative ages change every minute; only real task changes or newly stale work count as new.
        board = [(t['id'], t['updated'], t['stale'], t['execution_state']) for t in mine] + [brief(t) for t in others]
        fingerprints['tasks'] = hashlib.sha256(json.dumps(board).encode('utf-8')).hexdigest()
        run.context_delivery = {**fingerprints, 'message_id': latest_id}
        unchanged = []
        def delivery(name, limit):
            if run.previous_session and previous.get(name) == fingerprints[name]:
                unchanged.append(name)
                return '[Unchanged; already in your native session. Pull details with room_read if needed.]'
            return clipped(values[name], limit)
        participants = ', '.join('@' + state.get('handle', name) + ' (' + state.get('label', name) + ')'
                                 for name, state in self.runtime.states.items() if not state.get('archived'))
        workspace = delivery('workspace', 2400)
        title =getattr(self.runtime, 'channel_title', lambda channel: '#' + channel)(run.channel)
        # Named blocks, so runtime_end can record each block's share of the prompt (task 844f5663).
        blocks = {'participants': f"Participants: {participants}\n",
                  'workspace': (workspace if workspace.startswith('Execution context') else 'Execution context: ' + workspace) + '\n',
                  'live': self.live_context(run) + '\n',
                  'conversation': f"Current conversation in {title}:\n{history or '[No new room messages]'}\n"}
        if self.state['spec']:
            blocks['spec'] = f"Optional shared spec ({len(self.state['spec'])} characters):\n{delivery('spec', spec_limit)}\n"
        if ledger:
            blocks['board'] = f"Board:\n{delivery('tasks', task_limit)}\n"
        if decisions:
            blocks['notes'] = f"Group notes and decisions:\n{delivery('memory', memory_limit)}\n"
        if patches:
            blocks['patches'] = f"Peer patches:\n{delivery('patches', peer_limit)}\n"
        prompt = ''.join(blocks.values())
        # Reserve the request at the end so the current user instruction cannot be lost behind history.
        room = maximum - len(request) - 60
        tail = '\nCurrent message or task:\n' + request
        run.input_blocks = {**{name: len(text) for name, text in blocks.items()}, 'request': len(tail)}
        run.input_clipped, run.input_unchanged = len(prompt) > room, unchanged
        prompt = clipped(prompt, room) + tail
        chain = self.chains.get(run.entry.get('chain'))
        if chain:
            chain['input_chars'] += len(prompt)
        run.input_chars = len(prompt)
        run.estimated_input_tokens = math.ceil(len(prompt) / 4)
        return prompt

    def live_context(self, run):
        """Layer 3 of Docs/agent-profiles.md: what this agent should act on now, rebuilt from the board every
        turn. It lives in the turn prompt, never the instructions, so native sessions keep resuming. Replaces
        the old inbox counts line with the items themselves; the Board block keeps the full task details."""
        tasks = [t for t in self.state['tasks'] if t.get('status') != 'done']
        mine = sorted((t for t in tasks if t.get('owner') == run.name),
                      key=lambda t: (t['status'] != 'working', t.get('priority', 3), -t['updated']))
        started_for = getattr(run, 'task_ids', None) or ()
        main = next((t for t in mine if t['id'] in started_for), mine[0] if mine else None)
        def short(text, limit):
            """One line per item: clipped()'s multi-line excerpt tail would split the list."""
            flat = ' '.join(str(text).split())
            return flat if len(flat) <= limit else flat[:limit - 4] + ' ...'
        lines = ['Live context (this turn, from the board):']
        deployment = getattr(self.runtime, 'deployment', None)
        gate = deployment.reload() if deployment is not None else {}
        if gate.get('admission') == 'closed':
            # Read now from the maintenance record, never saved into a prompt (Astra 78336e2a).
            details = [short(gate[key], 160) if key == 'reason' else f"{label} {short(gate[key], 40)}"
                       for key, label in (('reason', ''), ('version', 'version'), ('deployment_id', 'deployment'))
                       if isinstance(gate.get(key), (str, int)) and str(gate[key]).strip()]
            lines.append('- Room update in progress' + (f" ({'; '.join(details)})" if details else '')
                         + ': work that changes the room waits until it reopens.')
        if main:
            lines.append(f"- Main task: {main['id']} [{main['status']}] {short(main['title'], 140)}"
                         + (f"; next: {short(main['next_action'], 300)}" if main.get('next_action') else ''))
            working = sum(t['status'] == 'working' for t in mine)
            if len(mine) > 1:
                lines.append(f"- You own {len(mine) - 1} other open task(s)"
                             + (f'; {working} are working at once, keep one main task working' if working > 1 else '') + '.')
        else:
            lines.append('- Main task: none owned.')
        inbox = self.records.inbox(run.name)
        waiting = ([f"- Review {r['id']} requested by {r['requested_by']} on task {r['task_id']}" for r in inbox['review_requests']]
                   + [f"- Changes requested by {r['reviewer']} on your task {r['task_id']} (review {r['id']})" for r in inbox['review_findings']]
                   + [f"- Follow-up {f['id']} from {f['created_by']}: {short(f['text'], 160)}" for f in inbox['followups']]
                   + [f"- Transfer of task {t['id']} to you waits for your accept" for t in inbox['transfers']])
        if run.name == (self.state.get('responders') or {}).get('lead'):
            # Before the inbox list: the lead's delivery and load lines can't be read anywhere else (backend cf8b999d).
            lines += self.coordinator_lines(tasks)
        if waiting:
            # Waiting items fill what the budget leaves; the rest is one room_read pointer, never a clipped tail.
            more = lambda count: f'- and {count} more: room_read(section="inbox")'
            room = LIVE_CONTEXT_CHARS - len('\n'.join(lines + ['Waiting on you:', more(len(waiting))])) - 1
            shown = []
            for item in waiting[:LIVE_CONTEXT_ITEMS]:
                if len(item) + 1 > room:
                    break
                shown.append(item)
                room -= len(item) + 1
            lines.append('Waiting on you:')
            lines += shown
            if len(shown) < len(waiting):
                lines.append(more(len(waiting) - len(shown)))
        return clipped('\n'.join(lines), LIVE_CONTEXT_CHARS)

    def coordinator_lines(self, tasks):
        """For the project lead only (opus 02fb548e): who is overloaded, and that team builder exists, so the
        lead sees both at every checkpoint without polling or searching for a deferred tool."""
        reviews = [r for r in self.state.get('reviews', []) if r['status'] == 'pending']
        load = []
        for name, state in self.runtime.states.items():
            if state.get('archived'):
                continue
            owned = [t for t in tasks if t.get('owner') == name]
            working = sum(t['status'] == 'working' for t in owned)
            pending = sum(r['reviewer'] == name for r in reviews)
            if owned or pending:
                flag = ' OVERLOADED' if working > LOAD_WORKING_LIMIT or pending > LOAD_REVIEW_LIMIT else ''
                load.append((bool(flag), f"{name} {working}w/{len(owned)}o/{pending}r{flag}"))
        load.sort(key=lambda item: not item[0])
        lines = []
        if load:
            lines.append('Team load (working/open tasks, pending reviews): ' + ', '.join(text for _, text in load))
        members = sum(not state.get('archived') for state in self.runtime.states.values())
        lines.append(f'Team builder: {members} active members. room_team_builder(action="suggest") proposes one new member '
                     'for the user to review; use it only for a real capability gap, not for load a teammate can take.')
        return self.delivery_lines(tasks) + lines

    def last_install(self):
        """When the installed release was deployed: the deploy record's write time, or None outside a managed room."""
        deployment = getattr(self.runtime, 'deployment', None)
        root = getattr(deployment, 'root', None) or Path(self.runtime.data) / 'deploy'
        try:
            return (Path(root) / 'current.json').stat().st_mtime
        except OSError:
            return None

    def delivery_signals(self, tasks):
        """Release train, scope and cycle time as data, from the board and the deploy record (opus 70947bb7, Zero
        #16078). One computation for the lead's delivery lines and for E3, the room's release-train action
        (process_enforcement, Docs/process-enforcement.md), so the prompt and the rule never disagree."""
        now = time.time()
        installed = self.last_install()
        reviews = collections.defaultdict(list)
        for review in self.state.get('reviews', []):
            reviews[review.get('task_id')].append(review)
        # Task history is trimmed (TASK_HISTORY), so its first entry can be recent; the earliest review is a floor.
        created = lambda t: min([t['updates'][0]['time'] if t.get('updates') else t['updated']]
                                + [r['created'] for r in reviews.get(t['id'], [])])
        # Approved and waiting: an open task whose newest answered review is an approval.
        waiting = []
        for t in tasks:
            answered = [r for r in reviews.get(t['id'], []) if r['status'] in ('approved', 'changes_requested')]
            if answered and max(answered, key=lambda r: r['updated'])['status'] == 'approved':
                waiting.append(t)
        visible = [t for t in waiting if set(t.get('labels') or ()) & USER_VISIBLE_LABELS]
        hours = (now - installed) / 3600 if installed else None
        # Scope: at E5's review-round cap (counted as E5 counts, harness b56d3d87), or open for hours while nothing
        # has been installed since it began.
        heavy = []
        for t in tasks:
            rounds = len(review_rounds(reviews.get(t['id'], []))[0])
            stalled = now - created(t) > SCOPE_OPEN_HOURS * 3600 and (installed is None or installed < created(t))
            if rounds >= SCOPE_REVIEW_ROUNDS or stalled:
                heavy.append({'id': t['id'], 'rounds': rounds, 'hours': (now - created(t)) / 3600})
        # Cycle time: delivered work (done, not a closure) finished in the last day.
        # Done time: the last "moved to done" event; later notes on a done task only move `updated` (backend cf8b999d).
        done_at = lambda t: next((u['time'] for u in reversed(t.get('updates') or []) if u.get('event') == 'moved to done'),
                                 t['updated'])
        spans = sorted((done_at(t) - created(t)) / 3600 for t in self.state['tasks']
                       if t['status'] == 'done' and not t.get('closure') and now - done_at(t) < CYCLE_WINDOW_SECONDS)
        return {'now': now, 'installed_at': installed, 'hours': hours, 'waiting': waiting, 'visible': visible,
                'due': bool(visible) and hours is not None and hours > RELEASE_TRAIN_HOURS,
                'heavy': heavy, 'cycle_hours': spans}

    def delivery_lines(self, tasks):
        """The lead's release-train, scope and cycle-time lines, formatted from delivery_signals."""
        signals = self.delivery_signals(tasks)
        hours, visible = signals['hours'], signals['visible']
        lines = [f"Release train: {len(signals['waiting'])} open tasks approved and waiting ({len(visible)} user-visible); "
                 + (f'last install {hours:.1f} h ago' if hours is not None else 'last install unknown')
                 + ('. CUT A RELEASE from what is approved now.' if signals['due'] else '.')]
        heavy = sorted(((h['rounds'], f"{h['id']} {h['rounds']} rounds, {h['hours']:.0f} h") for h in signals['heavy']),
                       reverse=True)
        if heavy:
            lines.append(f'Scope signal ({len(heavy)}): ' + ', '.join(text for _, text in heavy[:SCOPE_LISTED])
                         + (f' and {len(heavy) - SCOPE_LISTED} more' if len(heavy) > SCOPE_LISTED else '') + '. Split or freeze before adding more.')
        spans = signals['cycle_hours']
        if spans:
            lines.append(f'Cycle time (created to done, last 24 h, {len(spans)} tasks): median {statistics.median(spans):.1f} h.')
        return lines

    def project_record(self):
        import project_registry
        return project_registry.record(self.path.parent) if self.path is not None else {}

    def project_label(self):
        runtime = self.runtime
        name = self.project_record().get('name') or (Path(str(runtime.project)).name if runtime.project else 'Conversation only')
        identity = getattr(runtime, 'identity', None) or (self.path.parent.name if self.path is not None else 'none')
        return f'{name} (id {identity})'

    def execution_context(self, run):
        """Separate facts the runtime knows, so no turn is spent rediscovering the project or confusing a
        scratch folder, a missing repository and a missing project with each other."""
        runtime = self.runtime
        value = self.project_record()
        state = runtime.states.get(run.name, {})
        lines = [f'- Project: {self.project_label()}. '
                 'The board, chats and records below belong to it whether or not a repository is attached.']
        if runtime.project:
            lines.append(f'- Repository: attached at {runtime.project}.')
        else:
            lines.append('- Repository: none attached (conversation project).')
        request = value.get('repo_request')
        if request:
            action = 'create a repository at' if request.get('create') else 'attach'
            lines.append(f"- Repository request: {request.get('by')} asked to {action} {request.get('path')} "
                         f"{age(time.time() - request.get('time', time.time()))} ago; waiting for the user's approval in Projects.")
        if state.get('workspace_kind') == 'repository':
            lines.append(f"- Your working directory: {run.worktree}, your own git worktree of the repository on branch "
                         f"{state.get('branch') or '(unknown)'}. Other agents have their own worktrees.")
            for key, repo in state.get('repositories', {}).items():
                if not repo.get('primary'):
                    lines.append(f"- Additional repository {key}: {repo['path']}; your worktree: {repo['worktree']}.")
        else:
            lines.append(f'- Your working directory: {run.worktree}, a private scratch folder. It starts empty; that does not '
                         'mean the project has no source. Source paths named by the user or the spec can be read directly, '
                         'subject to your provider permissions.')
        if state.get('provider') in ROOM_TOOL_PROVIDERS or state.get('provider') == 'api':
            native = state.get('provider') in ROOM_TOOL_PROVIDERS
            lines.append('- Room tools (room_read, room_task, room_update, room_review, room_followup, room_goal, room_repository, '
                         f"room_image, room_markup, room_motion{', room_browser' if native else ''}"
                         f"{', room_skill' if getattr(run, 'skills', None) else ''}"
                         # Named for every native turn (harness 541d8351: an unnamed deferred MCP tool goes unused): since
                         # E4 any managed agent checks its donor and reads status and intake (astra 7c5e2a88).
                         f"{', room_release' if native else ''}) are provided for this turn. If any is "
                         'missing from your tool list, say so in the chat as a harness fault and name the action it blocks; '
                         'do not report the action as done.')
            if native:
                # MCP tools load deferred; a tool the prompt never names is effectively invisible.
                lines.append("- room_browser is the user's Browser panel: claim it, open a local preview, the room "
                             'itself (it opens signed in) or any site, inspect or screenshot what the user sees, then release. '
                             'A screenshot returns an /uploads URL for '
                             'room_markup and task attachments. Prefer it to a private headless browser for UI work. '
                             'record_start, record_stop and record_status record real product footage from your own '
                             'browser as a fileref for room_motion, kept where it was recorded.')
            if state.get('room_tools') == 'unconfirmed':
                lines.append('- The room did not observe your room tools loading or being used last turn. Check your tool list '
                             'first; if any are missing, say so in the chat.')
        elif state:
            lines.append('- Room tools are not available to your provider here. Coordinate in your chat replies and ask '
                         'a teammate or the user to make board changes; do not report board changes as made.')
        return 'Execution context:\n' + '\n'.join(lines)

    def request_text(self, run, limit):
        """The current request(s), newest kept first. Returns the text and the chat message IDs it holds."""
        requests = run.entry.get('requests') or []
        if not requests:
            return clipped(run.entry.get('prompt') or run.entry.get('message') or 'Respond to the latest room request.', limit), set()
        store = self.runtime.host.store
        # Coalesced updates past the allowance are named, not silently dropped, so older asks aren't lost.
        reserve = 260
        parts, used, held, left = [], 0, set(), []
        for index, pending in enumerate(reversed(requests)):
            message = store.get_by_id(pending['message_id']) if pending.get('message_id') is not None else None
            if message:
                part = continued(message, self.origin(), limit - reserve - used, self.uploads())
                whole = part == message_text(message, self.origin(), self.uploads())
                if re.search(r'@(all|both)(?![\w-])', message['text'], re.IGNORECASE):
                    # Every teammate reads this at once; without this, each posts its own acknowledgement or claim.
                    part += (f"\n[Sent to every agent. Before replying, read messages after #{message['id']} with room_read: "
                             "add only what nobody has said yet, and skip acknowledgements. If it needs one person to act, "
                             'claim it with room_task first; a refused duplicate means a teammate already has it.]')
            else:
                part = clipped(pending['prompt'], limit - reserve - used)
                whole = part == pending['prompt']
            if parts and used + len(part) + 2 > limit - reserve:
                left = list(reversed(requests))[index:]
                break
            parts.insert(0, part)
            used += len(part) + 2
            if message:
                held.add(message['id'])
            if whole:
                run.receipts.add(receipt_key(pending))
        request = '\n\n'.join(parts)
        if left:
            names = []
            for pending in reversed(left):
                ref = pending.get('ref') or {}
                names.append(f"#{pending['message_id']}" if pending.get('message_id') is not None
                             else f"{ref.get('collection', 'record')} {ref['id']}" if ref.get('id') else 'an earlier update')
            request = (f"[{len(left)} earlier request(s) in this turn are not shown here: {', '.join(names[-8:])}. "
                       'Read them with room_read (messages, inbox or the named record) before acting.]\n' + request)
        return request, held

    def origin(self):
        return f"http://127.0.0.1:{self.runtime.cfg['server']['port']}"

    def uploads(self):
        return Files(getattr(self.runtime.host.store, 'upload_dir', None), self.references())

    def references(self):
        """The pasted-reference registry (app.file_references, Zero #14949): the process's one shared instance, so
        a path pasted or a recording finished a moment ago resolves at once, with nothing reread from disk."""
        import local_files
        cfg = getattr(self.runtime, 'cfg', None)
        return local_files.shared_registry(cfg if isinstance(cfg, dict) else None)


def file_size(size):
    for unit in ('bytes', 'KB', 'MB'):
        if size < 1024 or unit == 'MB':
            return f'{size} {unit}' if unit == 'bytes' else f'{size:.1f} {unit}'
        size /= 1024


def linked(message, link):
    """(url, label) for each attachment and each [label](link) or bare link in the text matching link."""
    named = [(str(a.get('url', '')), a.get('name')) for a in message.get('attachments', []) if isinstance(a, dict)]
    # The composer pastes [name](link); a bare or absolute link has no label.
    pattern = r'(?:\[([^\]\n]{1,120})\]\()?(?:https?://[^\s/)]+)?(' + link + ')'
    named += [(m.group(2), m.group(1) or None) for m in re.finditer(pattern, str(message.get('text', '')))]
    return named


def one_line(name, limit=MAX_FILE_LABEL):
    """A client-supplied file name as one bounded line: it must not add lines to an agent's prompt."""
    return ' '.join(str(name).split())[:limit]


def file_kind(extension):
    return 'image' if extension in IMAGES else 'video' if extension in VIDEOS else 'file'


def reference_files(message, references, limit):
    """Pasted originals (/api/files/ref/<id>/<name>, Zero #14949). The path comes only from the server's registry,
    never from the client's attachment, and the file is read in place: nothing is copied."""
    lines, seen = [], set()
    for url, name in linked(message, REFERENCE_LINK):
        match = re.search(REFERENCE_LINK, url)
        if not match or match.group(1) in seen:
            continue
        key, seen_name = match.group(1), urllib.parse.unquote(match.group(2))
        seen.add(key)
        label = one_line(name or seen_name)
        try:
            path, changed = references.lookup(key)
            # The original can vanish between lookup's check and this one (deleted, moved, share dropped);
            # it must cost one line, never the whole prompt (backend 236cdebc).
            size = path.stat().st_size
        except ValueError as exc:
            lines.append(f"File: {label} is unavailable: {exc}")
        except OSError:
            lines.append(f"File: {label} is unavailable: The original file is gone: {path}")
        else:
            extension = path.suffix.lstrip('.').lower()
            lines.append(f"File: {label} is at {path} ({file_kind(extension)}, {file_size(size)})"
                         + (' (changed since pasted)' if changed else ''))
        if len(lines) == limit:
            break
    return lines


def upload_files(message, files, limit=8):
    """Videos and other files a message attaches or links (Zero #14495: drop or paste any file into chat).
    room_image shows raster images only, so for anything else name the local copy an agent can open with its
    own file tools. Only names inside the upload folder count; existence, kind and size come from disk.
    Pasted references to originals follow (reference_files)."""
    if isinstance(files, (str, os.PathLike)):
        files = Files(files, None)
    if not isinstance(files, Files):
        return []
    lines = reference_files(message, files.references, limit) if files.references is not None else []
    if not isinstance(files.uploads, (str, os.PathLike)):
        return lines
    root, seen = Path(files.uploads).resolve(), set()
    for url, name in linked(message, r'/uploads/[\w.-]+'):
        if len(lines) >= limit:
            break
        upload = str(url).split('/uploads/', 1)[1].rstrip('.') if '/uploads/' in str(url) else ''
        if (not upload or upload in seen or upload.startswith('.')
                or not all(c.isascii() and (c.isalnum() or c in '._-') for c in upload)):
            continue
        seen.add(upload)
        extension = upload.rsplit('.', 1)[-1].lower() if '.' in upload else ''
        path = (root / upload).resolve()
        # Message text and attachments are not validated: same containment rule as serve_upload.
        if extension in RASTER_IMAGES or not path.is_relative_to(root) or not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:  # removed since is_file(); skip it rather than fail the prompt
            continue
        lines.append(f"File: {one_line(name or upload)} is at {path} ({file_kind(extension)}, {file_size(size)})")
    return lines


def message_text(message, origin='', uploads=None):
    text = f"#{message['id']} {message['sender']}:"
    for attachment in message.get('attachments', []):
        if not isinstance(attachment, dict):  # chat attachments are not validated server-side
            continue
        url = str(attachment.get('url', ''))
        if url.startswith('/uploads/'):
            url = origin + url
        text += f"\nAttachment: {attachment.get('name', 'image')} {url}"
    for line in upload_files(message, uploads):
        text += '\n' + line
    return text + '\n' + message['text']


def coordination_tools():
    def definition(name, description, properties, required=()):
        return {'type': 'function', 'function': {'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': properties, 'required': list(required)}}}
    string = {'type': 'string'}
    return [
        definition('room_read', 'Read shared project state or your pending review/follow-up inbox. Filter tasks/reviews/followups by task_id, owner or status. limit/offset are characters; paginate with next_offset. Messages include attachments.',
                   {'section': {'type': 'string', 'enum': ['overview', 'spec', 'tasks', 'memory', 'messages', 'patches', 'participants', 'inbox', 'reviews', 'followups', 'review_artifact']}, 'offset': {'type': 'integer'}, 'limit': {'type': 'integer'}, 'query': string, 'after_id': {'type': 'integer'}, 'task_id': string, 'owner': string, 'status': string}),
        definition('room_task', 'Get, create, claim, update, delegate, accept or release a board task. Delegation proposes a transfer; ownership stays until accept. Set next_action, priority (1 highest), wait {kind:followup/review,record_id} linking a pending human request, or {} to clear. Evidence records implementation/review/installation/acceptance separately. acceptance {manifest_sha, result passed|failed, text} records your live check of one installed release (owner or user only; the room keeps who and when). get returns the full record with history and reviews/followups; other actions return a compact receipt. Claim relative paths before editing; overlapping scopes and dependency cycles are refused. labels tag the area (e.g. frontend, backend, docs; up to 4) and group the user\'s board. attachments replaces the task\'s image list (uploaded /uploads/ urls; the card shows the 8 newest, and when more are added older ones move to earlier images instead of failing, listed in the receipt; up to 48 per call, split larger batches; a note describes an annotation). Ownership and status changes are recorded in task history automatically. Agents must resolve requested reviews/dependencies before done; the user can override. To end a task that delivered nothing itself, update with close_reason and close_pointer instead of status done (the lead may close any task this way): duplicate/superseded point at the task carrying the work on (dependents move there), parked/wont_do at that task or a decision note; a closure skips the review and installation gates, cancels the task\'s open reviews and follow-ups, clears any pending transfer or wait, and is reopened by setting a status. A closed task shows status done with its closure; status reports count it as closed, never as delivered. next_action_state (owner or user only): a working task with a next_action is continued by the room after a turn that moved it, and its idle owner is woken after 10 minutes; waiting or deferred (with the reason on the task), or a wait, stops that. ready also opts in to one wake per external change; empty clears it. A waiting or deferred task is not forgotten: wait_on (owner or user only, up to 4 of {task: id}, {review: id}, {followup: id} or {release: version}; [] clears it) names what it waits for, and the room wakes the owner once when all are met. A wait that names nothing is re-checked: its owner is woken once if it has not changed in 3 hours. A task whose last dependency finishes wakes its owner once.',
                   {'action': {'type': 'string', 'enum': ['get', 'create', 'claim', 'update', 'delegate', 'accept', 'release']}, 'task_id': string, 'title': string, 'details': string, 'owner': string, 'status': string,
                    'paths': {'type': 'array', 'items': string}, 'depends_on': {'type': 'array', 'items': string}, 'labels': {'type': 'array', 'items': string},
                    'attachments': {'type': 'array', 'items': {'type': 'object', 'properties': {'url': string, 'name': string, 'note': string, 'source': string}, 'required': ['url']}},
                    'next_action': string, 'next_action_state': {'type': 'string', 'enum': sorted(NEXT_ACTION_STATES)},
                    'priority': {'type': 'integer', 'minimum': 1, 'maximum': 5},
                    'wait': {'type': 'object'}, 'evidence': {'type': 'object'}, 'update': string,
                    'acceptance': {'type': 'object', 'properties': {'manifest_sha': string, 'result': {'type': 'string', 'enum': ['passed', 'failed']}, 'text': string}},
                    'close_reason': {'type': 'string', 'enum': list(CLOSE_REASONS)}, 'close_pointer': string,
                    'wait_on': {'type': 'array', 'maxItems': MAX_WAIT_ON, 'items': {'type': 'object',
                                'properties': {'task': string, 'review': string, 'followup': string, 'release': string}}}}, ['action']),
        definition('room_markup', 'Show instead of describe: share an uploaded image with the team, optionally marked up with numbered boxes, arrows and pins (x/y are fractions 0..1 of width/height; a pin uses x1,y1). Draws a new PNG whose note lists each mark. Posts it to this channel unless post is false; task_id adds it to a task you own.',
                   {'image': {'type': 'string', 'description': '/uploads/<file> url from chat or a task'},
                    'marks': {'type': 'array', 'items': {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['box', 'arrow', 'pin']},
                        'x1': {'type': 'number'}, 'y1': {'type': 'number'}, 'x2': {'type': 'number'}, 'y2': {'type': 'number'}, 'note': string, 'color': string},
                        'required': ['kind', 'x1', 'y1']}},
                    'summary': string, 'task_id': string, 'post': {'type': 'boolean'}}, ['image']),
        definition('room_motion', 'Author motion scenes and product videos (JSON data, never code). list/get scenes; validate scene_json (returns issues [{path, message}]); save it as project:<id> (overwrite to replace); frame renders t_ms (one time or up to 6) exactly as the video looks, returning /uploads PNGs and a contact sheet; export starts the video in profile webm (default), vp9-alpha or av1 (WebM), prores4444 (.mov with alpha), gif, png (first frame only; use frame for a chosen time) or png-sequence (.zip of numbered frames); a profile this room\'s FFmpeg cannot encode is refused, never swapped; export_status; export_cancel job_id; publish job_id posts a finished WebM export to this chat (post false to skip) and task_id attaches it to a task you own; other profiles are not published, share their video_url from export_status. Author new work as v2 (field reference: Docs/motion-scene-v2.md); v1 room replays still render. frame and export use the first composition. Sound (audio, voiceover, unmuted video) exports once this room\'s offline voice worker is installed; until then export refuses it, and validate and save say what is missing. variant scene new_id saves a template re-render as project:<new_id> with composition (default first) and props overriding its props, checked against props_schema. patch scene ops applies RFC 6902 JSON Patch to a saved project scene; nothing is written unless the result validates. import_asset url asset_id turns a room upload (a room_browser screenshot, a recording, an image) or a registered original (fileref:<id> from a recording or a pasted path, used where it lies) into an asset with its real sha256 and, for images and recordings, size and how it fits the frame; with scene it is added to that project scene; a recording also returns a muted video layer (id layer_id, default asset_id) and its cursor layer from the recorded clicks. inspect job_id (an export) or url (a /uploads video, or a pasted file as fileref:<id> or its /api/files/ref link) measures resolution, fps, cuts and transitions, shot lengths, motion and holds, and loudness (LUFS); vs (the same forms, or export:<job_id>) adds a side-by-side against a reference; sheets (default contact and motion) draws contact (8 frames), dense (every 1/fps s from start to end, fps up to 10, at most 48 tiles, for small icon motion), motion (movement over time with cuts), shots (the middle of each shot) and frames (at t_ms) as /uploads PNGs. Heavy jobs (export, frame, inspect, recordings) take turns, one at a time on this room: if inspect is still waiting or running after a few seconds it returns its handle (job_id, generation, phase, position) and carries on; job_status job_id returns its phase and, when done, its result; job_cancel job_id generation stops a job you started; export_status lists this project\'s media_jobs. Loop: validate until clean, save, frame key moments and show them with room_markup, export, inspect it against the reference, then publish.',
                   {'action': {'type': 'string', 'enum': ['list', 'get', 'validate', 'save', 'frame', 'export', 'export_status', 'export_cancel', 'publish', 'variant', 'patch', 'import_asset', 'inspect', 'job_status', 'job_cancel']},
                    'scene': string, 'scene_json': {'type': 'object'}, 'overwrite': {'type': 'boolean'},
                    't_ms': {'anyOf': [{'type': 'number'}, {'type': 'array', 'items': {'type': 'number'}, 'maxItems': 6}]},
                    'reduced': {'type': 'boolean'}, 'job_id': string, 'generation': {'type': 'integer', 'minimum': 1}, 'task_id': string, 'note': string, 'name': string,
                    'post': {'type': 'boolean'},
                    'profile': {'type': 'string', 'enum': ['webm', 'vp9-alpha', 'av1', 'prores4444', 'gif', 'png', 'png-sequence']},
                    'new_id': string, 'composition': string, 'props': {'type': 'object'},
                    'ops': {'type': 'array', 'items': {'type': 'object'}, 'maxItems': 200},
                    'url': string, 'asset_id': string, 'layer_id': string,
                    'kind': {'type': 'string', 'enum': ['image', 'capture', 'video', 'font', 'audio']},
                    'vs': string, 'start': {'type': 'number', 'minimum': 0}, 'end': {'type': 'number', 'minimum': 0},
                    'fps': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 10},
                    'sheets': {'type': 'array', 'items': {'type': 'string', 'enum': ['contact', 'dense', 'motion', 'shots', 'frames']},
                               'maxItems': 5}}, ['action']),
        definition('room_update', 'Post a concise progress, decision, reference, blocker, or handoff update to project memory and the chatroom without waking peers. The room manager (or the user) can add release_checks {manifest_sha, tasks} to a decision: the tasks whose owners must check that installed release live; each owner then records room_task update acceptance {manifest_sha, result passed|failed, text}.',
                   {'kind': string, 'text': string, 'supersedes': string, 'resolves': string,
                    'release_checks': {'type': 'object', 'properties': {'manifest_sha': string, 'tasks': {'type': 'array', 'items': string, 'maxItems': 32}}}}, ['kind', 'text']),
        definition('room_review', 'Request peer review of an approach or patch using an explicit artifact/revision reference, or respond with findings for that exact reference. Any agent may request a review of its own work on any task; the reviewer must be someone else, and the verdict reaches both the requester and the task owner, and the owner can cancel a stray request on their task; a cancel from an agent wakes nobody (the reviewer sees it in their next turn). Optional files selects relative files in your workspace for backend hash snapshots (otherwise declared file paths). Changed snapshots block approval; room_read review_artifact retrieves captured content. Requests remain in the inbox. A request from an agent starts no turn at once: if the reviewer is idle and it is still pending after 2 minutes, the room wakes them once, so a re-request with a new revision replaces it first. A respond notifies the owner and the requester. Severity is recorded, not argued: critical (up to 12 of {text, class}, text up to 1000 characters) lists concrete failures, each the inputs or state and then the wrong result; notes (up to 12 texts) is everything else. changes_requested needs at least one critical entry, and approved carries notes only; notes become one follow-up on the task for whoever requested the review. On a release-pipeline task each critical names its class: room_down, data_loss, stop_overridden, unrecoverable or security_exposure.',
                   {'action': {'type': 'string', 'enum': ['request', 'respond', 'cancel']}, 'task_id': string, 'review_id': string, 'reviewer': string,
                    'files': {'type': 'array', 'items': string}, 'revision': string, 'text': string, 'verdict': {'type': 'string', 'enum': ['approved', 'changes_requested']}, 'notify': {'type': 'boolean'}, 'critical': {'type': 'array', 'maxItems': SEVERITY_ITEMS, 'items': {'type': 'object', 'properties': {'text': string, 'class': {'type': 'string', 'enum': list(PIPELINE_CLASSES)}}, 'required': ['text']}}, 'notes': {'type': 'array', 'maxItems': SEVERITY_ITEMS, 'items': string}}, ['action']),
        definition('room_followup', 'Create a durable action/question for a teammate, optionally linked to a task; resolve with its result or cancel with a reason. Open items stay in the assignee inbox. notify attempts one bounded wake-up. With notify=false nobody is woken now; if the recipient is idle and it is still unanswered after 2 minutes, the room wakes them once. There is no other reminder. A result from the user always wakes the other party; a result from an agent wakes the other party when the follow-up is on an open task they own, and otherwise is seen in their next turn (a task whose wait_on names the follow-up still wakes its owner). A cancel from an agent wakes nobody; the assignee sees it in their next turn. To answer several follow-ups with the same result (for example one release receipt), resolve once with followup_ids instead of followup_id: each record resolves and each requester is told, with one chat line.',
                   {'action': {'type': 'string', 'enum': ['create', 'resolve', 'cancel']}, 'followup_id': string,
                    'followup_ids': {'type': 'array', 'items': string, 'maxItems': MAX_BATCH_RESOLVE}, 'task_id': string,
                    'assigned_to': string, 'text': string, 'result': string, 'notify': {'type': 'boolean'}}, ['action']),
        definition('room_repository', 'Ask the user to add a repository (action add) or replace the primary (action replace). Use an existing absolute repository root, or create true for a new folder. Only the user approves. Running turns can finish and queued work is kept. Task/review paths may use key:relative/path; bare task paths are pinned to the primary when claimed.',
                   {'path': string, 'create': {'type': 'boolean'}, 'reason': string, 'action': {'type': 'string', 'enum': ['add', 'replace']}}, ['path', 'reason']),
        definition('room_goal', 'Read the shared room goal, or as its named lead finish it: complete or block with goal_id and evidence. The user starts, pauses and cancels goals.',
                   {'action': {'type': 'string', 'enum': ['status', 'complete', 'block']}, 'goal_id': string, 'evidence': string}),
        definition('room_skill', 'Your approved skills. list gives each name and when to use it; load name returns its full procedure, which you then follow; load name with file returns one of that skill\'s listed extra files. Only your own skills load.',
                   {'action': {'type': 'string', 'enum': ['list', 'load']}, 'name': string, 'file': string}),
        # The same surface as room_mcp.room_release (E4, astra 7c5e2a88); release_interface validates the values.
        definition('room_release', 'The room release pipeline, without the browser. The room takes who you are from this turn, never from arguments. Any managed agent may check its own donor and read status and intake; everything else belongs to the profile the user granted release permission to. Any agent: check repository commit (40 hex) reviews (1-12 approved 8-hex review ids) base (40 hex, the commit the donor applies to) dependencies (optional, up to 12 check or release ids): validate a reviewed donor, one check per agent every 30 s; status (id optional, 32 hex): the deployment, gate and jobs, or one job, receipt, check or handoff, shared with every managed agent; intake: this project donor checks and handoffs. Release manager only: handoff check_id; disposition handoff_id disposition (open, deferred or dropped) reason (1-1000 characters) target (optional, up to 160); archive check_id (moves a check out of a full history; it still answers status); list; stage repository commit reviews, with optional release notes: title (up to 120 characters) and notes (markdown, up to 16000) go into RELEASE.json, and shipped_tasks (up to 32 of {task_id: 8 hex, scope: what this release delivers for it, 1-1000 characters}; owners come from the board, never from the caller) go into the stage receipt, from which the room binds their live checks after install; omit all three to stage without them; rehearse release_id manifest_sha; request release_id manifest_sha expected_manifest_sha idempotency_key; verify deployment_id; rollback expected_manifest_sha idempotency_key. If this tool refuses, report it; never use the Updates page, credentials or files instead.',
                   {'action': {'type': 'string', 'enum': ['list', 'stage', 'rehearse', 'request', 'status', 'verify', 'rollback',
                                                          'check', 'intake', 'handoff', 'disposition', 'archive']},
                    'repository': string, 'commit': string, 'reviews': {'type': 'array', 'items': string, 'maxItems': 12},
                    'release_id': string, 'manifest_sha': string, 'expected_manifest_sha': string, 'idempotency_key': string,
                    'id': string, 'deployment_id': string, 'base': string,
                    'dependencies': {'type': 'array', 'items': string, 'maxItems': 12}, 'check_id': string,
                    'handoff_id': string, 'disposition': {'type': 'string', 'enum': ['open', 'deferred', 'dropped']},
                    'reason': string, 'target': string, 'title': {'type': 'string', 'maxLength': 120},
                    'notes': {'type': 'string', 'maxLength': 16000},
                    'shipped_tasks': {'type': 'array', 'maxItems': 32, 'items': {
                        'type': 'object', 'properties': {'task_id': {'type': 'string', 'pattern': '^[0-9a-f]{8}$'},
                                                         'scope': {'type': 'string', 'minLength': 1, 'maxLength': 1000}},
                        'required': ['task_id', 'scope'], 'additionalProperties': False}}}, ['action']),
    ]
