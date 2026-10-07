"""Durable peer reviews and follow-ups shared by the board and agent tools."""
import time
import uuid
import hashlib
from pathlib import Path

# Entries kept per task: check-ins, review/follow-up activity and automatic ownership/status events.
TASK_HISTORY = 20


class CoordinationRecords:
    def __init__(self, room):
        self.room = room

    def task(self, task_id):
        task = next((t for t in self.room.state['tasks'] if t['id'] == task_id), None)
        if task is None:
            raise ValueError('Task was not found')
        return task

    def participant(self, name):
        if name != 'user' and name not in self.room.runtime.states:
            raise ValueError('Choose a registered participant or user')

    def record(self, collection, identifier):
        item = next((x for x in self.room.state.get(collection, []) if x['id'] == identifier), None)
        if item is None:
            raise ValueError('Coordination item was not found')
        return item

    def save(self, actor, task, text):
        if task:
            task['updates'].append({'by': actor, 'text': text[:1600], 'time': time.time()})
            task['updates'] = task['updates'][-TASK_HISTORY:]
            task['updated'] = time.time()
        self.room.save()

    def capture(self, task, files=None):
        """Capture bounded declared source files; never guess a different workspace."""
        raw_root = self.room.runtime.states.get(task.get('owner'), {}).get('worktree')
        if not raw_root:
            if files:
                raise ValueError('Review source workspace is unavailable')
            return {'files': [], 'unavailable': list(task.get('paths', []))}, {}
        root = Path(raw_root).resolve()
        selected = task.get('paths', []) if files is None else files
        if not isinstance(selected, list) or len(selected) > 32 or any(not isinstance(p, str) for p in selected):
            raise ValueError('Review files must be a list of at most 32 relative files')
        entries, blobs, unavailable, total = [], {}, [], 0
        for relative in dict.fromkeys(selected):
            path = (root / relative).resolve()
            if Path(relative).is_absolute() or not path.is_relative_to(root):
                raise ValueError('Review files must stay inside the owner workspace')
            if any(p.lower() in ('.git', '.venv', 'node_modules') for p in path.relative_to(root).parts) or path.name.lower().startswith(('.env', 'credentials', 'secrets')) or path.suffix.lower() in ('.pem', '.key', '.pfx'):
                raise ValueError('Review snapshots exclude environment, credential and generated files')
            if not path.is_file():
                if files is not None:
                    raise ValueError(f'Review file is missing or is a directory: {relative}')
                unavailable.append(relative)
                continue
            total += path.stat().st_size
            if total > 2_000_000:
                raise ValueError('Review snapshot exceeds 2 MB; select a smaller explicit file list')
            data = path.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            entries.append({'path': relative, 'sha256': sha, 'bytes': len(data)})
            blobs[sha] = data
        return {'root': str(root), 'files': entries, 'unavailable': unavailable}, blobs

    def changed_files(self, item):
        snapshot = item.get('snapshot', {})
        if not snapshot.get('root'):
            return []
        root = Path(snapshot['root']).resolve()
        changed = []
        for entry in snapshot.get('files', []):
            path = (root / entry['path']).resolve()
            try:
                matches = path.is_relative_to(root) and hashlib.sha256(path.read_bytes()).hexdigest() == entry['sha256']
            except OSError:
                matches = False
            if not matches:
                changed.append(entry['path'])
        return changed

    def review_view(self, item):
        return {**item, 'changed_files': self.changed_files(item),
                'fingerprinted': bool(item.get('snapshot', {}).get('files'))}

    def review(self, actor, action, task_id='', review_id='', reviewer='', revision='', text='', verdict='', files=None):
        reviews = self.room.state.get('reviews', [])
        if action == 'request':
            task = self.task(task_id)
            if actor != 'user' and actor != task['owner']:
                raise ValueError('Only the task owner or user can request its review')
            self.participant(reviewer)
            if reviewer == task['owner']:
                raise ValueError('Choose a peer; an owner cannot approve their own work')
            revision, text = str(revision).strip(), str(text).strip()
            if not revision or len(revision) > 240 or not text:
                raise ValueError('Provide a revision reference (up to 240 characters) and a concrete review question or scope')
            for item in reviews:
                if (item['task_id'], item['reviewer'], item['revision']) == (task_id, reviewer, revision) and item['status'] == 'pending':
                    return {'item': item, 'changed': False, 'notify': []}
            snapshot, blobs = self.capture(task, files)
            item = {'id': uuid.uuid4().hex[:8], 'task_id': task_id, 'requested_by': actor,
                    'owner': task['owner'], 'reviewer': reviewer, 'revision': revision,
                    'request': text[:2400], 'status': 'pending', 'findings': '',
                    'created': time.time(), 'updated': time.time()}
            item['snapshot'] = snapshot
            if blobs:
                folder = self.room.path.parent / 'review-artifacts' / item['id']
                folder.mkdir(parents=True, exist_ok=True)
                for sha, data in blobs.items():
                    (folder / sha).write_bytes(data)
            # A new reference supersedes this peer's earlier review of the same task.
            for old in reviews:
                if old['task_id'] == task_id and old['reviewer'] == reviewer and old['status'] != 'superseded':
                    old.update(status='superseded', updated=time.time())
            self.room.state.setdefault('reviews', []).append(item)
            summary = f"Review {item['id']} requested from {reviewer} for {revision}: {text}"
            recipients = [reviewer]
        elif action == 'respond':
            item = self.record('reviews', review_id)
            task = self.task(item['task_id'])
            if actor != item['reviewer'] or actor == task['owner']:
                raise ValueError('Only the assigned reviewer can submit this review')
            if item['status'] in ('superseded', 'cancelled'):
                raise ValueError('This request has been superseded; read the current review request')
            if revision != item['revision']:
                raise ValueError('Respond with the exact revision reference you inspected')
            if verdict not in ('approved', 'changes_requested') or not str(text).strip():
                raise ValueError('Provide approved or changes_requested with concrete findings and evidence')
            changed = self.changed_files(item)
            if verdict == 'approved' and changed:
                raise ValueError(f'Reviewed files changed: {changed}; request a new review revision')
            if item['status'] == verdict and item['findings'] == str(text).strip()[:3000]:
                return {'item': item, 'changed': False, 'notify': []}
            item.update(status=verdict, findings=str(text).strip()[:3000], updated=time.time())
            summary = f"Review {item['id']} {verdict} by {actor} for {revision}: {text}"
            recipients = [task['owner'] or item['requested_by']]
        elif action == 'cancel':
            item = self.record('reviews', review_id)
            task = self.task(item['task_id'])
            if actor not in ('user', task['owner'], item['requested_by']):
                raise ValueError('Only the owner, requester or user can cancel a review')
            if not str(text).strip():
                raise ValueError('Record why the review is being cancelled')
            if item['status'] in ('cancelled', 'superseded'):
                return {'item': item, 'changed': False, 'notify': []}
            item.update(status='cancelled', cancellation=str(text).strip()[:2400], updated=time.time())
            summary = f"Review {item['id']} cancelled by {actor}: {text}"
            recipients = [item['reviewer']]
        else:
            raise ValueError('Choose request, respond or cancel')
        self.save(actor, task, summary)
        return {'item': item, 'changed': True, 'notify': recipients, 'summary': summary[:1600]}

    def followup(self, actor, action, followup_id='', task_id='', assigned_to='', text='', result=''):
        items = self.room.state.get('followups', [])
        if action == 'create':
            self.participant(assigned_to)
            task = self.task(task_id) if task_id else None
            text = str(text).strip()
            if not text:
                raise ValueError('Provide a concrete next action or question')
            for item in items:
                if (item['created_by'], item['assigned_to'], item['task_id'], item['text'], item['status']) == (actor, assigned_to, task_id, text[:2400], 'open'):
                    return {'item': item, 'changed': False, 'notify': []}
            item = {'id': uuid.uuid4().hex[:8], 'task_id': task_id, 'created_by': actor,
                    'assigned_to': assigned_to, 'text': text[:2400], 'status': 'open', 'result': '',
                    'created': time.time(), 'updated': time.time()}
            self.room.state.setdefault('followups', []).append(item)
            summary = f"Follow-up {item['id']} for {assigned_to}: {text}"
            recipients = [assigned_to]
        elif action in ('resolve', 'cancel'):
            item = self.record('followups', followup_id)
            allowed = ('user', item['assigned_to'], item['created_by']) if action == 'resolve' else ('user', item['created_by'])
            if actor not in allowed:
                raise ValueError('Only the assignee, requester or user can resolve; only requester/user can cancel')
            if not str(result).strip():
                raise ValueError('Record the result or cancellation reason')
            if item['status'] != 'open':
                return {'item': item, 'changed': False, 'notify': []}
            task = self.task(item['task_id']) if item['task_id'] else None
            item.update(status='resolved' if action == 'resolve' else 'cancelled',
                        result=str(result).strip()[:2400], updated=time.time())
            summary = f"Follow-up {item['id']} {item['status']} by {actor}: {result}"
            recipients = [item['assigned_to'] if action == 'cancel' or actor == item['created_by'] else item['created_by']]
        else:
            raise ValueError('Choose create, resolve or cancel')
        self.save(actor, task, summary)
        return {'item': item, 'changed': True, 'notify': recipients, 'summary': summary[:1600]}

    def inbox(self, actor):
        reviews = self.room.state.get('reviews', [])
        return {
            'transfers': [t for t in self.room.state['tasks'] if t.get('transfer', {}).get('to') == actor],
            'ready_tasks': [self.room.task_view(t) for t in sorted(self.room.state['tasks'], key=lambda t: (t.get('priority', 3), t['updated']))
                            if t.get('owner') == actor and t.get('status') != 'done'
                            and self.room.task_view(t)['execution_state'] in ('idle', 'needs_attention')],
            'review_requests': [r for r in reviews if r['reviewer'] == actor and r['status'] == 'pending'],
            'review_findings': [r for r in reviews if self.task(r['task_id'])['owner'] == actor and r['status'] == 'changes_requested'],
            'followups': [f for f in self.room.state.get('followups', []) if f['assigned_to'] == actor and f['status'] == 'open'],
            'waiting_for': [f for f in self.room.state.get('followups', []) if f['created_by'] == actor and f['assigned_to'] != actor and f['status'] == 'open'],
        }
