"""Durable project coordination and bounded room context, without model calls."""
import json
import hashlib
import math
import os
import re
import time
import urllib.parse
import uuid
from collections import namedtuple
from coordination_records import TASK_HISTORY
from pathlib import Path, PurePosixPath
from coordination_records import CoordinationRecords, bounded
from coordination_queue import receipt_key
from durable import atomic_write
from coordination_state import update_execution, execution_state, pending_human_wait

DEFAULT_LIMITS = {'input_chars': 16000, 'agent_turns': 0,
                  'turn_seconds': 0, 'turn_max_seconds': 0, 'parallel_agents': 0, 'local_tool_rounds': 3,
                  'native_tool_calls': 0,
                  'reported_token_budget': 0,
                  # Ready-task wakes (ready_wakes.py): 0 = off (default), 1 = on. Opt-in only.
                  'ready_task_wakes': 0}
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
# Owner-set readiness for ready-task wakes; never inferred from text, status or labels ('' clears it).
NEXT_ACTION_STATES = {'ready', 'deferred', 'waiting', ''}
# Changes to these fields by anyone other than the owner are new work for the owner (work_rev).
SEMANTIC_FIELDS = ('status', 'owner', 'transfer', 'depends_on', 'wait', 'next_action', 'next_action_state', 'paths')
# The board holds at most TASK_LIMIT tasks. Finished tasks that nothing open still references move,
# oldest first and ARCHIVE_BATCH at a time, to tasks-archive.jsonl beside coordination.json.
TASK_LIMIT = 200
ARCHIVE_BATCH = 20
# Owned work without a check-in for this long is flagged as stale in context and on the board.
# This is visibility only; turn cutoffs stay governed by the optional limits (0 = off).
STALE_SECONDS = 600
# Providers whose turns get the room MCP server (native_adapters: codex/claude flags, antigravity config).
ROOM_TOOL_PROVIDERS = {'codex', 'claude', 'antigravity'}


def age(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 60}m' if seconds < 3600 else f'{seconds // 3600}h{seconds % 3600 // 60:02d}m'


def stale(task, now):
    return bool(task['owner']) and task['status'] in ('working', 'blocked') and now - task['updated'] > STALE_SECONDS


def write_json(path, value):
    content = json.dumps(value, ensure_ascii=False, indent=2)
    atomic_write(path, lambda stream: stream.write(content))


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
REFERENCE_REGISTRY = 'file_references.json'  # in the server's data_dir, written by app.file_references()
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
        self.path = self.runtime.data / 'projects' / identity / 'coordination.json'
        self.state = json.loads(self.path.read_text('utf-8')) if self.path.exists() else {'spec': '', 'tasks': [], 'notes': []}
        # Legacy bare reservations keep their original repository after a primary change.
        self.state.setdefault('scope_repository', self.primary_repository_key())
        for note in self.state.get('notes', []):
            if note.get('kind') == 'decision':
                note.setdefault('id', uuid.uuid4().hex[:8])
                note.setdefault('status', 'active')
        self.save()
        self.chains.clear()
        self.current_chain.clear()

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
        summary.update({key: view[key] for key in ('priority', 'next_action', 'wait', 'transfer', 'depends_on', 'paths', 'labels')
                        if view.get(key)})
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

    def save(self):
        write_json(self.path, self.state)

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
        """Conversation records live in memory only. Admitted work that names one lost to a restart (a restored
        install checkpoint) or to pruning gets a fresh record under the same id instead of being refused as if
        its budget ran out (harness 8016e379). Never used for manager check-ins, whose fences must hold."""
        if identifier and identifier not in self.chains:
            self.chains[identifier] = self.fresh_chain(identifier, channel)

    def new_chain(self, channel):
        identifier = uuid.uuid4().hex[:12]
        self.current_chain[channel] = identifier
        self.chains[identifier] = self.fresh_chain(identifier, channel)
        # Completed old conversations need not stay in memory; audit retains events.
        if len(self.chains) > 100:
            active = {r.entry.get('chain') for r in self.runtime.running.values()}
            # An open shared goal keeps continuing on its chain; its budget must survive pruning.
            goal = getattr(getattr(self.runtime, 'goals', None), 'goal', None)
            if goal and goal.get('status') in ('active', 'paused', 'blocked'):
                active.add(goal.get('chain'))
            audit = self.state.get('manager_audit', {}).get('chain') or {}
            if audit.get('id'):
                active.add(audit['id'])
            for old in list(self.chains)[:-60]:
                if old not in active and old not in self.current_chain.values():
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
        if (saved.get('settings') == run.settings
                and saved.get('provider') == self.runtime.states[run.name]['provider']
                and saved.get('instruction_hash') == run.instruction_hash):
            return saved
        return {}

    def complete(self, run):
        if getattr(run, 'manager_audit', None):
            self.save()
            return  # Timer sessions never replace the ordinary conversation continuation.
        self.state.setdefault('cursors', {})[run.session_key] = run.context_delivery
        receipts = self.state.setdefault('receipts', {}).get(run.session_key, [])
        delivered = run.receipts
        receipts = [r for r in receipts if r not in delivered] + sorted(delivered - {''})
        self.state['receipts'][run.session_key] = receipts[-512:]
        if run.provider_session:
            self.state.setdefault('sessions', {})[run.session_key] = {'id': run.provider_session,
                'settings': run.settings, 'provider': self.runtime.states[run.name]['provider'],
                'instruction_hash': run.instruction_hash,
                'context': run.context_delivery, 'usage_total': getattr(run, 'provider_usage_total', 0)}
        self.save()

    def task(self, actor, action, task_id='', title='', details='', paths=None, owner='', status='', update='', depends_on=None, labels=None, attachments=None,
             next_action=None, priority=None, wait=None, evidence=None, next_action_state=None):
        tasks = self.state['tasks']
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
            if not human and task['owner'] and task['owner'] != actor and not accepting:
                raise ValueError(f"Task is owned by {task['owner']}; ask its owner to delegate it")
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
                if title:
                    task['title'] = title
                if details:
                    task['details'] = details
                if status:
                    if status not in TASK_STATES:
                        raise ValueError('Choose open, working, blocked, review, or done')
                    task['status'] = status
            else:
                raise ValueError('Choose get, create, claim, delegate, accept, release, or update')
            if previous['status'] == 'done' and task['status'] != 'done' and task.get('depends_on'):
                # Reopening: a dependency missing from the board was archived, and only done tasks
                # are archived, so it is finished. Dropping it keeps the task from waiting forever
                # on an id every done-set check would treat as unfinished (backend 86d7fe0e F1).
                board = {t['id'] for t in tasks}
                task['depends_on'] = [d for d in task['depends_on'] if d in board]
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
        if evidence is not None:
            if not isinstance(evidence, dict) or set(evidence) - {'implementation', 'review', 'installation', 'acceptance'}:
                raise ValueError('Evidence keys: implementation, review, installation, acceptance')
            if any(not isinstance(v, str) or len(v) > 2400 for v in evidence.values()):
                raise ValueError('Evidence entries must be text of at most 2400 characters')
            task['evidence'] = {**task.get('evidence', {}), **evidence}
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
        if not human and task['status'] == 'done':
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
        if previous and task['status'] != previous['status']:
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
        if previous and task['status'] == 'done' and previous['status'] != 'done':
            # A finished dependency is new work for each waiting dependent's owner.
            for other in tasks:
                if task['id'] in other.get('depends_on', []) and other['status'] != 'done':
                    self.bump_work(other, actor, f"dependency {task['id']} finished")
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

    def note(self, actor, kind, text, supersedes='', resolves=''):
        if kind not in ('progress', 'decision', 'reference', 'blocker', 'handoff') or not str(text).strip():
            raise ValueError('Provide a progress, decision, reference, blocker, or handoff update')
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
        if old:
            old.update(status='superseded' if supersedes else 'resolved', replaced_by=note['id'])
            note['supersedes' if supersedes else 'resolves'] = old['id']
        self.state['notes'].append(note)
        decisions = [n for n in self.state['notes'] if n['kind'] in ('decision', 'reference')]
        recent = [n for n in self.state['notes'] if n['kind'] not in ('decision', 'reference')][-100:]
        self.state['notes'] = sorted(decisions + recent, key=lambda n: n['time'])
        self.save()
        return note

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
        def delivery(name, limit):
            if run.previous_session and previous.get(name) == fingerprints[name]:
                return '[Unchanged; already in your native session. Pull details with room_read if needed.]'
            return clipped(values[name], limit)
        participants = ', '.join('@' + state.get('handle', name) + ' (' + state.get('label', name) + ')'
                                 for name, state in self.runtime.states.items() if not state.get('archived'))
        prompt = f"Participants: {participants}\n"
        workspace = delivery('workspace', 2400)
        prompt += (workspace if workspace.startswith('Execution context') else 'Execution context: ' + workspace) + '\n'
        inbox = self.records.inbox(run.name)
        counts = {key: len(items) for key, items in inbox.items() if items}
        if counts:
            prompt += 'Outstanding coordination: ' + json.dumps(counts) + '. Read room_read(section="inbox") at your next checkpoint.\n'
        title = getattr(self.runtime, 'channel_title', lambda channel: '#' + channel)(run.channel)
        prompt += f"Current conversation in {title}:\n{history or '[No new room messages]'}\n"
        if self.state['spec']:
            prompt += f"Optional shared spec ({len(self.state['spec'])} characters):\n{delivery('spec', spec_limit)}\n"
        if ledger:
            prompt += f"Board:\n{delivery('tasks', task_limit)}\n"
        if decisions:
            prompt += f"Group notes and decisions:\n{delivery('memory', memory_limit)}\n"
        if patches:
            prompt += f"Peer patches:\n{delivery('patches', peer_limit)}\n"
        # Reserve the request at the end so the current user instruction cannot be lost behind history.
        prompt = clipped(prompt, maximum - len(request) - 60) + '\nCurrent message or task:\n' + request
        chain = self.chains.get(run.entry.get('chain'))
        if chain:
            chain['input_chars'] += len(prompt)
        run.input_chars = len(prompt)
        run.estimated_input_tokens = math.ceil(len(prompt) / 4)
        return prompt

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
                         f"room_image, room_markup, room_motion{', room_browser' if native else ''}) are provided for this turn. If any is "
                         'missing from your tool list, say so in the chat as a harness fault and name the action it blocks; '
                         'do not report the action as done.')
            if native:
                # MCP tools load deferred; a tool the prompt never names is effectively invisible.
                lines.append("- room_browser is the user's Browser panel: claim it, open a local preview, inspect or "
                             'screenshot what the user sees, then release. A screenshot returns an /uploads URL for '
                             'room_markup and task attachments. Prefer it to a private headless browser for UI work.')
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
        """The pasted-reference registry the server writes (app.file_references, Zero #14949). Reloaded only when
        its file changes, so a reference pasted after the room started resolves without rereading every time."""
        cfg = getattr(self.runtime, 'cfg', None)
        server = cfg.get('server') if isinstance(cfg, dict) else None
        path = Path((server or {}).get('data_dir', './data')) / REFERENCE_REGISTRY
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            return None
        cached = getattr(self, '_references', None)
        if cached is None or cached[0] != stamp:
            import local_files
            cached = self._references = (stamp, local_files.Registry(path))
        return cached[1]


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
        definition('room_task', 'Get, create, claim, update, delegate, accept or release a board task. Delegation proposes a transfer; ownership stays until accept. Set next_action, priority (1 highest), wait {kind:followup/review,record_id} linking a pending human request, or {} to clear. Evidence records implementation/review/installation/acceptance separately. get returns the full record with history and reviews/followups; other actions return a compact receipt. Claim relative paths before editing; overlapping scopes and dependency cycles are refused. labels tag the area (e.g. frontend, backend, docs; up to 4) and group the user\'s board. attachments replaces the task\'s image list (uploaded /uploads/ urls; the card shows the 8 newest, and when more are added older ones move to earlier images instead of failing, listed in the receipt; up to 48 per call, split larger batches; a note describes an annotation). Ownership and status changes are recorded in task history automatically. Agents must resolve requested reviews/dependencies before done; the user can override.',
                   {'action': {'type': 'string', 'enum': ['get', 'create', 'claim', 'update', 'delegate', 'accept', 'release']}, 'task_id': string, 'title': string, 'details': string, 'owner': string, 'status': string,
                    'paths': {'type': 'array', 'items': string}, 'depends_on': {'type': 'array', 'items': string}, 'labels': {'type': 'array', 'items': string},
                    'attachments': {'type': 'array', 'items': {'type': 'object', 'properties': {'url': string, 'name': string, 'note': string, 'source': string}, 'required': ['url']}},
                    'next_action': string, 'next_action_state': {'type': 'string', 'enum': sorted(NEXT_ACTION_STATES)},
                    'priority': {'type': 'integer', 'minimum': 1, 'maximum': 5},
                    'wait': {'type': 'object'}, 'evidence': {'type': 'object'}, 'update': string}, ['action']),
        definition('room_markup', 'Show instead of describe: share an uploaded image with the team, optionally marked up with numbered boxes, arrows and pins (x/y are fractions 0..1 of width/height; a pin uses x1,y1). Draws a new PNG whose note lists each mark. Posts it to this channel unless post is false; task_id adds it to a task you own.',
                   {'image': {'type': 'string', 'description': '/uploads/<file> url from chat or a task'},
                    'marks': {'type': 'array', 'items': {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['box', 'arrow', 'pin']},
                        'x1': {'type': 'number'}, 'y1': {'type': 'number'}, 'x2': {'type': 'number'}, 'y2': {'type': 'number'}, 'note': string, 'color': string},
                        'required': ['kind', 'x1', 'y1']}},
                    'summary': string, 'task_id': string, 'post': {'type': 'boolean'}}, ['image']),
        definition('room_motion', 'Author motion scenes and product videos (JSON data, never code). list/get scenes; validate scene_json (returns issues [{path, message}]); save it as project:<id> (overwrite to replace); frame renders t_ms (one time or up to 6) exactly as the video looks, returning /uploads PNGs and a contact sheet; export starts the video in profile webm (default), vp9-alpha or av1 (WebM), prores4444 (.mov with alpha), gif, png (first frame only; use frame for a chosen time) or png-sequence (.zip of numbered frames); a profile this room\'s FFmpeg cannot encode is refused, never swapped; export_status; export_cancel job_id; publish job_id posts a finished WebM export to this chat (post false to skip) and task_id attaches it to a task you own; other profiles are not published, share their video_url from export_status. Author new work as v2 (field reference: Docs/motion-scene-v2.md); v1 room replays still render. frame and export use the first composition. Sound is not exported yet: export refuses audio layers and unmuted video, and validate and save warn first. variant scene new_id saves a template re-render as project:<new_id> with composition (default first) and props overriding its props, checked against props_schema. patch scene ops applies RFC 6902 JSON Patch to a saved project scene; nothing is written unless the result validates. import_asset url asset_id turns a room upload (a room_browser screenshot, a recording, an image) into an asset with its real sha256 and, for images, size and how it fits the frame; with scene it is added to that project scene. inspect job_id (an export) or url (a /uploads video, or a pasted file as fileref:<id> or its /api/files/ref link) measures resolution, fps, cuts and transitions, shot lengths, motion and holds, and loudness (LUFS); vs (the same forms, or export:<job_id>) adds a side-by-side against a reference; sheets (default contact and motion) draws contact (8 frames), dense (every 1/fps s from start to end, fps up to 10, at most 48 tiles, for small icon motion), motion (movement over time with cuts), shots (the middle of each shot) and frames (at t_ms) as /uploads PNGs. Loop: validate until clean, save, frame key moments and show them with room_markup, export, inspect it against the reference, then publish.',
                   {'action': {'type': 'string', 'enum': ['list', 'get', 'validate', 'save', 'frame', 'export', 'export_status', 'export_cancel', 'publish', 'variant', 'patch', 'import_asset', 'inspect']},
                    'scene': string, 'scene_json': {'type': 'object'}, 'overwrite': {'type': 'boolean'},
                    't_ms': {'anyOf': [{'type': 'number'}, {'type': 'array', 'items': {'type': 'number'}, 'maxItems': 6}]},
                    'reduced': {'type': 'boolean'}, 'job_id': string, 'task_id': string, 'note': string, 'name': string,
                    'post': {'type': 'boolean'},
                    'profile': {'type': 'string', 'enum': ['webm', 'vp9-alpha', 'av1', 'prores4444', 'gif', 'png', 'png-sequence']},
                    'new_id': string, 'composition': string, 'props': {'type': 'object'},
                    'ops': {'type': 'array', 'items': {'type': 'object'}, 'maxItems': 200},
                    'url': string, 'asset_id': string,
                    'kind': {'type': 'string', 'enum': ['image', 'capture', 'video', 'font', 'audio']},
                    'vs': string, 'start': {'type': 'number', 'minimum': 0}, 'end': {'type': 'number', 'minimum': 0},
                    'fps': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 10},
                    'sheets': {'type': 'array', 'items': {'type': 'string', 'enum': ['contact', 'dense', 'motion', 'shots', 'frames']},
                               'maxItems': 5}}, ['action']),
        definition('room_update', 'Post a concise progress, decision, reference, blocker, or handoff update to project memory and the chatroom without waking peers.',
                   {'kind': string, 'text': string, 'supersedes': string, 'resolves': string}, ['kind', 'text']),
        definition('room_review', 'Request peer review of an approach or patch using an explicit artifact/revision reference, or respond with findings for that exact reference. Optional files selects relative files for backend hash snapshots (otherwise declared file paths). Changed snapshots block approval; room_read review_artifact retrieves captured content. Requests remain in the inbox; notify attempts one bounded wake-up.',
                   {'action': {'type': 'string', 'enum': ['request', 'respond', 'cancel']}, 'task_id': string, 'review_id': string, 'reviewer': string,
                    'files': {'type': 'array', 'items': string}, 'revision': string, 'text': string, 'verdict': {'type': 'string', 'enum': ['approved', 'changes_requested']}, 'notify': {'type': 'boolean'}}, ['action']),
        definition('room_followup', 'Create a durable action/question for a teammate, optionally linked to a task; resolve with its result or cancel with a reason. Open items stay in the assignee inbox. notify attempts one bounded wake-up; there is no polling or automatic repeated reminder.',
                   {'action': {'type': 'string', 'enum': ['create', 'resolve', 'cancel']}, 'followup_id': string, 'task_id': string,
                    'assigned_to': string, 'text': string, 'result': string, 'notify': {'type': 'boolean'}}, ['action']),
        definition('room_repository', 'Ask the user to add a repository (action add) or replace the primary (action replace). Use an existing absolute repository root, or create true for a new folder. Only the user approves. Running turns can finish and queued work is kept. Task/review paths may use key:relative/path; bare task paths are pinned to the primary when claimed.',
                   {'path': string, 'create': {'type': 'boolean'}, 'reason': string, 'action': {'type': 'string', 'enum': ['add', 'replace']}}, ['path', 'reason']),
        definition('room_goal', 'Read the shared room goal, or as its named lead finish it: complete or block with goal_id and evidence. The user starts, pauses and cancels goals.',
                   {'action': {'type': 'string', 'enum': ['status', 'complete', 'block']}, 'goal_id': string, 'evidence': string}),
    ]
