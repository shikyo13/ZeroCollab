"""Durable project coordination and bounded room context, without model calls."""
import json
import hashlib
import math
import re
import time
import uuid
from coordination_records import TASK_HISTORY
from pathlib import Path, PurePosixPath
from coordination_records import CoordinationRecords
from coordination_queue import receipt_key
from durable import atomic_write
from coordination_state import update_execution, execution_state, pending_human_wait

# chat_handoffs guards against agents @mentioning each other back and forth; 0 = no limit.
# Tracked reviews, follow-ups and delegations never count (see RoomRuntime.run_turn).
DEFAULT_LIMITS = {'input_chars': 16000, 'agent_turns': 0, 'chat_handoffs': 10,
                  'turn_seconds': 0, 'turn_max_seconds': 0, 'parallel_agents': 0, 'local_tool_rounds': 3,
                  'native_tool_calls': 0,
                  'reported_token_budget': 0}
LIMIT_RANGES = {'input_chars': (4000, 48000), 'agent_turns': (0, 32), 'chat_handoffs': (0, 50),
                'turn_seconds': (0, 3600), 'turn_max_seconds': (0, 14400), 'parallel_agents': (0, 64),
                'local_tool_rounds': (0, 4), 'reported_token_budget': (0, 200000)}
LIMIT_RANGES['native_tool_calls'] = (0, 64)
TASK_STATES = {'open', 'working', 'blocked', 'review', 'done'}
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


def continued(message, origin, limit):
    # A message addressed to the reader is cut only when it cannot fit at all, and then the cut
    # names the exact room_read call that returns the rest, so nobody acts on half a message.
    full = message_text(message, origin)
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


def upload_url(raw):
    # Accept the room's own upload links, including the absolute form agents see in chat excerpts.
    raw = str(raw).strip()
    path = '/uploads/' + raw.split('/uploads/', 1)[1] if '/uploads/' in raw else ''
    name = path[len('/uploads/'):]
    if not name or not all(c.isascii() and (c.isalnum() or c in '._-') for c in name) or name.startswith('.'):
        raise ValueError('Attachments must be uploaded images (/uploads/<file>)')
    if name.rsplit('.', 1)[-1].lower() not in RASTER_IMAGES:
        # SVG can carry script; board images and annotation sources are raster only.
        raise ValueError('Attach a PNG, JPEG, GIF or WebP image')
    return path


def task_attachments(values):
    # Images shown on the board card; prompts carry only a count, agents fetch details and pixels on demand.
    if not isinstance(values, list) or len(values) > 8:
        raise ValueError('attachments must be a list of up to 8 uploaded images')
    result = []
    for value in values:
        item = {'url': value} if isinstance(value, str) else value
        if not isinstance(item, dict):
            raise ValueError('Each attachment needs a url')
        attachment = {'url': upload_url(item.get('url', '')), 'name': str(item.get('name') or 'image')[:120]}
        if item.get('note'):
            attachment['note'] = str(item['note']).strip()[:1000]
        if item.get('source'):
            attachment['source'] = upload_url(item['source'])
        if attachment['url'] not in [a['url'] for a in result]:
            result.append(attachment)
    return result


def overlaps(a, b):
    # Directory scopes and globs reserve the directory prefix conservatively.
    a, b = a.split('*', 1)[0].rstrip('/'), b.split('*', 1)[0].rstrip('/')
    return a in ('', '.') or b in ('', '.') or a == b or a.startswith(b + '/') or b.startswith(a + '/')


class ProjectRoom:
    def __init__(self, runtime):
        self.runtime = runtime
        self.limits_path = runtime.data / 'room_limits.json'
        self.limits = dict(DEFAULT_LIMITS)
        if self.limits_path.exists():
            stored = json.loads(self.limits_path.read_text('utf-8'))
            # The old 'hops' limit also counted reviews and follow-ups, and its stored 4 was the
            # old default and maximum, so it does not carry over to chat_handoffs.
            stored.pop('hops', None)
            self.change_limits(stored, save=False)
        self.path = None
        self.state = {'spec': '', 'tasks': [], 'notes': []}
        self.chains = {}
        self.current_chain = {}
        self.records = CoordinationRecords(self)

    def use_project(self, identity):
        self.path = self.runtime.data / 'projects' / identity / 'coordination.json'
        self.state = json.loads(self.path.read_text('utf-8')) if self.path.exists() else {'spec': '', 'tasks': [], 'notes': []}
        for note in self.state.get('notes', []):
            if note.get('kind') == 'decision':
                note.setdefault('id', uuid.uuid4().hex[:8])
                note.setdefault('status', 'active')
        self.save()
        self.chains.clear()
        self.current_chain.clear()

    def snapshot(self):
        review_hashes = {}
        return {**{k: v for k, v in self.state.items() if k not in ('sessions', 'cursors', 'receipts')},
                'tasks': [self.task_view(t) for t in self.state['tasks']],
                'reviews': [self.records.review_view(r, review_hashes) for r in self.state.get('reviews', [])],
                'limits': dict(self.limits), 'chains': list(self.chains.values())[-12:],
                'stale_seconds': STALE_SECONDS}

    def task_view(self, task):
        activity = (getattr(self.runtime, 'activity', lambda name: {})(task['owner']) or {}) if task.get('owner') else {}
        state = execution_state(task, self.state,
            running=task['id'] in activity.get('running_task_ids', []),
            queued=task['id'] in activity.get('queued_task_ids', []))
        return {**task, 'execution_state': state, 'owner_activity': activity,
                'observed_at': time.time(),
                'stale': stale(task, time.time()) and not pending_human_wait(task, self.state)}

    def save(self):
        write_json(self.path, self.state)

    def change_limits(self, updates, save=True):
        values = dict(self.limits)
        for name, value in updates.items():
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
        if hasattr(self.runtime.host, 'router') and self.runtime.host.router:
            self.runtime.host.router.max_hops = values['chat_handoffs']
        return dict(values)

    def set_spec(self, text):
        if not isinstance(text, str) or len(text) > 64000:
            raise ValueError('The shared spec must be text under 64,000 characters')
        self.state['spec'] = text.strip()
        self.save()

    def new_chain(self, channel):
        identifier = uuid.uuid4().hex[:12]
        self.current_chain[channel] = identifier
        self.chains[identifier] = {'id': identifier, 'channel': channel, 'turns': 0,
                                   'handoffs': 0, 'reported_tokens': 0, 'input_chars': 0, 'paused': False}
        # Completed old conversations need not stay in memory; audit retains events.
        if len(self.chains) > 100:
            active = {r.entry.get('chain') for r in self.runtime.running.values()}
            # An open shared goal keeps continuing on its chain; its budget must survive pruning.
            goal = getattr(getattr(self.runtime, 'goals', None), 'goal', None)
            if goal and goal.get('status') in ('active', 'paused', 'blocked'):
                active.add(goal.get('chain'))
            for old in list(self.chains)[:-60]:
                if old not in active and old not in self.current_chain.values():
                    self.chains.pop(old, None)
        return identifier

    def chain_for(self, channel):
        return self.current_chain.get(channel) or self.new_chain(channel)

    def permit(self, identifier):
        chain = self.chains.get(identifier)
        if chain is None:
            return False
        if chain['paused'] or (self.limits['agent_turns'] and chain['turns'] >= self.limits['agent_turns']) or (self.limits['reported_token_budget'] and chain['reported_tokens'] >= self.limits['reported_token_budget']):
            chain['paused'] = True
            return False
        chain['turns'] += 1
        return True

    def handoffs_spent(self, identifier):
        limit = self.limits['chat_handoffs']
        return bool(limit) and self.chains.get(identifier, {}).get('handoffs', 0) >= limit

    def handoff(self, identifier):
        chain = self.chains.get(identifier)
        if chain is None or chain['paused'] or self.handoffs_spent(identifier):
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
        return {'reported_tokens': run.reported_tokens, 'provider_usage': usage}

    def previous_session(self, run):
        saved = self.state.get('sessions', {}).get(run.session_key, {})
        if (saved.get('settings') == run.settings
                and saved.get('provider') == self.runtime.states[run.name]['provider']
                and saved.get('instruction_hash') == run.instruction_hash):
            return saved
        return {}

    def complete(self, run):
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
             next_action=None, priority=None, wait=None, evidence=None):
        tasks = self.state['tasks']
        task = next((t for t in tasks if t['id'] == task_id), None)
        previous = task
        human = actor == 'user'
        if action == 'get':
            if not task:
                raise ValueError('Task was not found')
            return {**self.task_view(task), 'reviews': [self.records.review_view(r) for r in self.state.get('reviews', []) if r['task_id'] == task_id],
                    'followups': [f for f in self.state.get('followups', []) if f['task_id'] == task_id]}
        if action == 'create':
            duplicate = next((t for t in tasks if t['status'] != 'done' and t['title'].strip().casefold() == str(title).strip()[:120].casefold()), None)
            if duplicate:
                raise ValueError('Duplicate task; existing task: ' + json.dumps({k: duplicate[k] for k in ('id', 'title', 'owner', 'status')}))
            if not str(title).strip() or len(tasks) >= 200:
                raise ValueError('Provide a task title; the project supports up to 200 tasks')
            task = {'id': uuid.uuid4().hex[:8], 'title': str(title).strip()[:120],
                    'details': str(details).strip()[:3000], 'paths': [scope(p) for p in (paths or [])[:20]],
                    'owner': owner or ('' if human else actor), 'status': 'open', 'updates': [], 'updated': time.time()}
        elif not task:
            raise ValueError('Task was not found')
        else:
            task = {**task, 'updates': list(task['updates'])}
            accepting = action == 'accept' and task.get('transfer', {}).get('to') == actor
            if not human and task['owner'] and task['owner'] != actor and not accepting:
                raise ValueError(f"Task is owned by {task['owner']}; ask its owner to delegate it")
            if paths is not None:
                task['paths'] = [scope(p) for p in paths[:20]]
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
                    task['title'] = str(title).strip()[:120]
                if details:
                    task['details'] = str(details).strip()[:3000]
                if status:
                    if status not in TASK_STATES:
                        raise ValueError('Choose open, working, blocked, review, or done')
                    task['status'] = status
            else:
                raise ValueError('Choose get, create, claim, delegate, accept, release, or update')
        task = update_execution(task, self.state, next_action=next_action, priority=priority, wait=wait)
        if evidence is not None:
            if not isinstance(evidence, dict) or set(evidence) - {'implementation', 'review', 'installation', 'acceptance'}:
                raise ValueError('Evidence keys: implementation, review, installation, acceptance')
            if any(not isinstance(v, str) or len(v) > 2400 for v in evidence.values()):
                raise ValueError('Evidence entries must be text of at most 2400 characters')
            task['evidence'] = {**task.get('evidence', {}), **evidence}
        if labels is not None:
            task['labels'] = task_labels(labels)
        if attachments is not None:
            task['attachments'] = task_attachments(attachments)
            store = getattr(getattr(self.runtime, 'host', None), 'store', None)
            upload_dir = getattr(store, 'upload_dir', None)
            missing = [a['url'] for a in task['attachments'] if upload_dir and not (upload_dir / a['url'][len('/uploads/'):]).is_file()]
            if missing:
                raise ValueError(f'Upload not found: {missing[0]}')
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
            changed_reviews = [r['id'] for r in self.state.get('reviews', []) if r['task_id'] == task['id'] and r['status'] == 'approved' and self.records.changed_files(r)]
            reviews += changed_reviews
            if unfinished or reviews:
                raise ValueError(f'Resolve requested reviews {reviews} and unfinished dependencies {unfinished} before marking done; the user can override')
        if task['owner'] and task['owner'] not in self.runtime.states:
            raise ValueError('Choose a registered project participant as owner')
        if not human and task['owner'] == actor and task['status'] in ('working', 'review') and not task['paths']:
            # Discussion/research tasks can have no file scope; edits still need a scoped task.
            task['details'] = task['details'] or 'Discussion/research task; claim file paths before editing.'
        if task['owner'] and task['status'] != 'done':
            for other in tasks:
                if other['id'] == task['id'] or not other['owner'] or other['status'] == 'done':
                    continue
                if any(overlaps(a, b) for a in task['paths'] for b in other['paths']):
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
        if after - before:
            events.append(f"attached {len(after - before)} image{'s' if len(after - before) > 1 else ''}")
        if before - after:
            events.append(f"removed {len(before - after)} image{'s' if len(before - after) > 1 else ''}")
        if update or events:
            entry = {'by': actor, 'text': str(update)[:1600] if update else '; '.join(events), 'time': time.time()}
            if events:
                entry['event'] = '; '.join(events)
            if not update:
                # Lifecycle-only entries are history, not progress check-ins.
                entry['auto'] = True
            task['updates'].append(entry)
            task['updates'] = task['updates'][-TASK_HISTORY:]
        task['updated'] = time.time()
        if action == 'create':
            tasks.append(task)
        else:
            tasks[tasks.index(next(t for t in tasks if t['id'] == task['id']))] = task
        self.save()
        return self.task_view(task)

    def markup(self, actor, channel, chain, image, marks=None, summary='', task_id='', post=True):
        """Share an uploaded image, optionally with numbered marks drawn onto a new PNG."""
        url = upload_url(image)
        upload_dir = self.runtime.host.store.upload_dir
        source = upload_dir / url[len('/uploads/'):]
        if not source.is_file():
            raise ValueError(f'Upload not found: {url}')
        if not isinstance(post, bool):
            raise ValueError('post must be true or false')
        if marks:
            # Imported on use so the room still starts if Pillow is missing; only markup fails.
            from room_markup import render_markup
            data, note = render_markup(source.read_bytes(), marks, summary)
            filename = f'{uuid.uuid4().hex[:8]}.png'
            (upload_dir / filename).write_bytes(data)
            attachment = {'url': '/uploads/' + filename, 'name': f'{source.stem}-markup.png', 'note': note, 'source': url}
        else:
            # Plain sharing keeps the original upload; the summary travels as its note.
            note = str(summary or '').strip()
            attachment = {'url': url, 'name': source.name, **({'note': note} if note else {})}
        result = {'attachment': attachment}
        if task_id:
            task = next((t for t in self.state['tasks'] if t['id'] == task_id), None)
            if not task:
                raise ValueError('Task was not found')
            # task() applies ownership, the 8-image cap and history like any other attachment change.
            self.task(actor, 'update', task_id=task_id, attachments=[*task.get('attachments', []), attachment])
            result['task_id'] = task_id
        if post:
            text = attachment.get('note') or f"Shared {attachment['name']}"
            message = self.runtime.host.store.add(actor, text[:2000], channel=channel,
                attachments=[{'name': attachment['name'], 'url': attachment['url']}],
                metadata={'no_route': True, 'chain': chain})
            result['message_id'] = message['id']
        return result

    def note(self, actor, kind, text, supersedes='', resolves=''):
        if kind not in ('progress', 'decision', 'reference', 'blocker', 'handoff') or not str(text).strip():
            raise ValueError('Provide a progress, decision, reference, blocker, or handoff update')
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
        note = {'id': uuid.uuid4().hex[:8], 'by': actor, 'kind': kind, 'text': str(text).strip()[:1600], 'time': time.time()}
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
            text = json.dumps(self.records.inbox(run.name), ensure_ascii=False)
        elif section == 'participants':
            text = json.dumps({'paused': self.runtime.paused, 'participants': {
                name: {**{k: s.get(k) for k in ('label', 'provider', 'state', 'paused', 'detail', 'worktree', 'archived')},
                       'background': self.runtime.profiles.items.get(name, {}).get('background', '')}
                for name, s in self.runtime.states.items()}}, ensure_ascii=False)
        elif section == 'memory':
            text = json.dumps(self.state['notes'], ensure_ascii=False)
        elif section == 'messages':
            text = '\n'.join(message_text(m, self.origin()) for m in self.runtime.host.store.get_since(int(after_id), channel=run.channel))
        elif section == 'patches':
            if changes is None:
                raise ValueError('Patch collection must be supplied by the async caller')
            text = json.dumps(changes, ensure_ascii=False)
        elif section == 'overview':
            text = json.dumps({'conversation': 'Current chat is available independently of an optional spec or repository.',
                # 'project' used to be the repository path, so a conversation project read as "no project".
                'project': self.project_label(),
                'repository': str(self.runtime.project) if self.runtime.project else 'none attached',
                'spec_length': len(self.state['spec']), 'tasks': [
                {**{k: t[k] for k in ('id', 'title', 'owner', 'status', 'paths')}, 'labels': t.get('labels', []),
                 'images': len(t.get('attachments', []))}
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
            for m in self.runtime.host.store.get_since(int(after_id), channel=run.channel):
                if message_text(m, self.origin()) in page:
                    self.observe_message(run, m)
        elif section in ('inbox', 'reviews', 'followups', 'tasks'):
            for collection in ('reviews', 'followups', 'tasks'):
                for item in self.state.get(collection, []):
                    if json.dumps(item, ensure_ascii=False) in page:
                        run.receipts.add(receipt_key({'ref': {'collection': collection,
                            'id': item['id'], 'updated': item['updated']}}))
        return {'text': page, 'offset': offset, 'next_offset': offset+limit if offset+limit < len(text) else None,
                'total_chars': len(text)}

    def observe_message(self, run, message):
        run.receipts.add(receipt_key({'message_id': message['id']}))
        ref = message.get('metadata', {}).get('coordination_ref')
        if ref:
            run.receipts.add(receipt_key({'ref': ref}))

    def context(self, run, changes=None):
        maximum = self.limits['input_chars']
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
                text += f"\n  images={len(t['attachments'])} (room_task get for urls/notes; room_image to view)"
            return text
        ledger = '\n'.join(line(t) for t in tasks)
        # Only current decisions; superseded and resolved ones stay in room_read(section="memory").
        # References (caches, helper scripts, paths) stay listed while active, so nobody redoes setup.
        current = lambda kind: [n for n in self.state['notes'] if n['kind'] == kind and n.get('status', 'active') == 'active']
        recent_decisions = current('decision')[-8:]
        references = current('reference')[-12:]
        recent_updates = [n for n in self.state['notes'] if n['kind'] not in ('decision', 'reference')][-4:]
        decisions = '\n'.join(f"{n['kind']}{' ' + n['id'] if n.get('id') else ''} {n['by']}: {n['text']}"
                              for n in references + recent_decisions + recent_updates)
        chat_limit = int(maximum * .2)
        previous = run.previous_session.get('context', {})
        cursor = previous.get('message_id', 0)
        messages = (self.runtime.host.store.get_since(cursor, channel=run.channel) if run.previous_session
                    else self.runtime.host.store.get_recent(40, channel=run.channel))
        latest_id = messages[-1]['id'] if messages else cursor
        relevant = [m for m in messages if m['sender'] != 'system' and not (run.previous_session and m['sender'] == run.name)]
        request, in_request = self.request_text(run, min(8000, maximum // 3))
        # Keep whole messages, newest first, so a sender and message ID are never sliced off. Messages
        # addressed to this agent are not cut to an excerpt; the one being answered appears once, in full, below.
        lines, used, shown = [], 0, set()
        for m in reversed(relevant[-12:]):
            if m['id'] in in_request:
                entry = f"#{m['id']} {m['sender']}: [Addressed to you; full text under Current message below.]"
            elif addressed(m['text'], run.name):
                entry = continued(m, self.origin(), max(1400, chat_limit // 2))
            else:
                entry = clipped(message_text(m, self.origin()), 1400)
            if lines and used + len(entry) + 1 > chat_limit:
                break
            lines.insert(0, entry if len(entry) <= chat_limit else clipped(entry, chat_limit))
            shown.add(m['id'])
            if lines[0] == message_text(m, self.origin()):
                self.observe_message(run, m)
            used += len(lines[0]) + 1
        history = '\n'.join(lines)
        if len(lines) < len(relevant):
            missed = [f"#{m['id']} {m['sender']}" for m in relevant
                      if m['id'] not in shown and m['id'] not in in_request and addressed(m['text'], run.name)]
            if missed:
                history = (f"[Also addressed to you and not shown here: {', '.join(missed[-12:])}. Read them with "
                           f'room_read(section="messages", after_id={cursor}) before acting on older work.]\n' + history)
            history = f'[Older unread messages remain available via room_read(section="messages", after_id={cursor}).]\n' + history
        peer_limit = int(maximum * .12)
        changes = changes if changes is not None else {}
        changed = [f"{name}'s worktree:\n{clipped(diff, max(200, peer_limit // max(1, len(changes))))}" for name, diff in changes.items() if diff]
        patches = chr(10).join(changed)
        values = {'spec': self.state['spec'], 'tasks': ledger, 'memory': decisions, 'patches': patches,
                  'workspace': self.execution_context(run)}
        fingerprints = {name: hashlib.sha256(text.encode('utf-8')).hexdigest() for name, text in values.items()}
        # The pending request's age moves every minute; only a real change resends the block.
        fingerprints['workspace'] = hashlib.sha256(re.sub(r'\d+h?\d*m ago', '', values['workspace']).encode('utf-8')).hexdigest()
        fingerprints['patches'] = hashlib.sha256(json.dumps(changes, sort_keys=True).encode('utf-8')).hexdigest()
        # Relative ages change every minute; only real task changes or newly stale work count as new.
        board = [(t['id'], t['updated'], t['stale'], t['execution_state']) for t in tasks]
        fingerprints['tasks'] = hashlib.sha256(json.dumps(board).encode('utf-8')).hexdigest()
        run.context_delivery = {**fingerprints, 'message_id': latest_id}
        def delivery(name, limit):
            if run.previous_session and previous.get(name) == fingerprints[name]:
                return '[Unchanged; already in your native session. Pull details with room_read if needed.]'
            return clipped(values[name], limit)
        participants = ', '.join('@' + name + ' (' + state.get('label', name) + ')'
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
            prompt += f"Owned tasks:\n{delivery('tasks', task_limit)}\n"
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
        else:
            lines.append(f'- Your working directory: {run.worktree}, a private scratch folder. It starts empty; that does not '
                         'mean the project has no source. Source paths named by the user or the spec can be read directly, '
                         'subject to your provider permissions.')
        if state.get('provider') in ROOM_TOOL_PROVIDERS or state.get('provider') == 'api':
            lines.append('- Room tools (room_read, room_task, room_update, room_review, room_followup, room_goal, room_repository, '
                         'room_image, room_markup) are provided for this turn. If any is missing from your tool list, say so in '
                         'the chat as a harness fault and name the action it blocks; do not report the action as done.')
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
                part = continued(message, self.origin(), limit - reserve - used)
                whole = part == message_text(message, self.origin())
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


def message_text(message, origin=''):
    text = f"#{message['id']} {message['sender']}:"
    for attachment in message.get('attachments', []):
        url = attachment.get('url', '')
        if url.startswith('/uploads/'):
            url = origin + url
        text += f"\nAttachment: {attachment.get('name', 'image')} {url}"
    return text + '\n' + message['text']


def coordination_tools():
    def definition(name, description, properties, required=()):
        return {'type': 'function', 'function': {'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': properties, 'required': list(required)}}}
    string = {'type': 'string'}
    return [
        definition('room_read', 'Read shared project state or your pending review/follow-up inbox. Filter tasks/reviews/followups by task_id, owner or status. limit/offset are characters; paginate with next_offset. Messages include attachments.',
                   {'section': {'type': 'string', 'enum': ['overview', 'spec', 'tasks', 'memory', 'messages', 'patches', 'participants', 'inbox', 'reviews', 'followups', 'review_artifact']}, 'offset': {'type': 'integer'}, 'limit': {'type': 'integer'}, 'query': string, 'after_id': {'type': 'integer'}, 'task_id': string, 'owner': string, 'status': string}),
        definition('room_task', 'Get, create, claim, update, delegate, accept or release a board task. Delegation proposes a transfer; ownership stays until accept. Set next_action, priority (1 highest), wait {kind:followup/review,record_id} linking a pending human request, or {} to clear. Evidence records implementation/review/installation/acceptance separately. get includes reviews/followups. Claim relative paths before editing; overlapping scopes and dependency cycles are refused. labels tag the area (e.g. frontend, backend, docs; up to 4) and group the user\'s board. attachments replaces the task\'s image list (uploaded /uploads/ urls, up to 8; a note describes an annotation). Ownership and status changes are recorded in task history automatically. Agents must resolve requested reviews/dependencies before done; the user can override.',
                   {'action': {'type': 'string', 'enum': ['get', 'create', 'claim', 'update', 'delegate', 'accept', 'release']}, 'task_id': string, 'title': string, 'details': string, 'owner': string, 'status': string,
                    'paths': {'type': 'array', 'items': string}, 'depends_on': {'type': 'array', 'items': string}, 'labels': {'type': 'array', 'items': string},
                    'attachments': {'type': 'array', 'items': {'type': 'object', 'properties': {'url': string, 'name': string, 'note': string, 'source': string}, 'required': ['url']}},
                    'next_action': string, 'priority': {'type': 'integer', 'minimum': 1, 'maximum': 5},
                    'wait': {'type': 'object'}, 'evidence': {'type': 'object'}, 'update': string}, ['action']),
        definition('room_markup', 'Show instead of describe: share an uploaded image with the team, optionally marked up with numbered boxes, arrows and pins (x/y are fractions 0..1 of width/height; a pin uses x1,y1). Draws a new PNG whose note lists each mark. Posts it to this channel unless post is false; task_id adds it to a task you own.',
                   {'image': {'type': 'string', 'description': '/uploads/<file> url from chat or a task'},
                    'marks': {'type': 'array', 'items': {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['box', 'arrow', 'pin']},
                        'x1': {'type': 'number'}, 'y1': {'type': 'number'}, 'x2': {'type': 'number'}, 'y2': {'type': 'number'}, 'note': string, 'color': string},
                        'required': ['kind', 'x1', 'y1']}},
                    'summary': string, 'task_id': string, 'post': {'type': 'boolean'}}, ['image']),
        definition('room_update', 'Post a concise progress, decision, reference, blocker, or handoff update to project memory and the chatroom without waking peers.',
                   {'kind': string, 'text': string, 'supersedes': string, 'resolves': string}, ['kind', 'text']),
        definition('room_review', 'Request peer review of an approach or patch using an explicit artifact/revision reference, or respond with findings for that exact reference. Optional files selects relative files for backend hash snapshots (otherwise declared file paths). Changed snapshots block approval; room_read review_artifact retrieves captured content. Requests remain in the inbox; notify attempts one bounded wake-up.',
                   {'action': {'type': 'string', 'enum': ['request', 'respond', 'cancel']}, 'task_id': string, 'review_id': string, 'reviewer': string,
                    'files': {'type': 'array', 'items': string}, 'revision': string, 'text': string, 'verdict': {'type': 'string', 'enum': ['approved', 'changes_requested']}, 'notify': {'type': 'boolean'}}, ['action']),
        definition('room_followup', 'Create a durable action/question for a teammate, optionally linked to a task; resolve with its result or cancel with a reason. Open items stay in the assignee inbox. notify attempts one bounded wake-up; there is no polling or automatic repeated reminder.',
                   {'action': {'type': 'string', 'enum': ['create', 'resolve', 'cancel']}, 'followup_id': string, 'task_id': string,
                    'assigned_to': string, 'text': string, 'result': string, 'notify': {'type': 'boolean'}}, ['action']),
        definition('room_repository', 'Ask the user to attach or replace this project\'s Git repository: use an existing repository root (create false) or create a new one at an absolute folder path (create true). Only the user approves. Running turns can finish and queued work is kept, so continue working meanwhile.',
                   {'path': string, 'create': {'type': 'boolean'}, 'reason': string}, ['path', 'reason']),
        definition('room_goal', 'Read the shared room goal, or as its named lead finish it: complete or block with goal_id and evidence. The user starts, pauses and cancels goals.',
                   {'action': {'type': 'string', 'enum': ['status', 'complete', 'block']}, 'goal_id': string, 'evidence': string}),
    ]
