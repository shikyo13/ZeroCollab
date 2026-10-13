"""Durable peer reviews and follow-ups shared by the board and agent tools."""
import os
import subprocess
import time
import uuid
import hashlib
from pathlib import Path

# Entries kept per task: check-ins, review/follow-up activity and automatic ownership/status events.
TASK_HISTORY = 20
MAX_BATCH_RESOLVE = 20  # follow-ups one resolve call may close with a shared result
# Free-text limits. Over-limit text is refused, never cut, so the author learns before the record is saved.
# Requests, follow-ups, results and cancellations are stored in full, so they match findings: 4000 kept
# refusing ordinary multi-point reviews and forced agents to split one record into several.
LIMITS = dict(title=120, details=8000, update=4000, note=4000, request=8000, findings=8000,
              cancellation=8000, followup=8000, result=8000, evidence=4000,
              # Matches room_markup's summary limit. A markup note is up to two provenance lines,
              # that summary and up to 20 mark lines of at most 358 characters: under 8500.
              markup=1000, **{'attachment note': 9000})
HISTORY_TEXT = 1600
# Characters of one captured review file returned to a workspace tab; a whole capture is at most 2 MB.
ARTIFACT_TEXT = 1_000_000
REVIEW_FIELDS = ('id', 'task_id', 'revision', 'status', 'reviewer', 'owner', 'requested_by', 'created', 'updated')
# The room's own actor for records it creates (Docs/process-enforcement.md S5): reserved as an agent id and handle,
# set only by server code, and never woken.
SYSTEM = 'system'
SCOPE_ANSWER = 'E5: only the user answers a scope-cap item (resolve to allow more review rounds, cancel to stop)'


def bounded(text, kind):
    """Return stripped text, or refuse it with the length and limit when it is too long."""
    text, limit = str(text).strip(), LIMITS[kind]
    if len(text) > limit:
        raise ValueError(f'{kind.capitalize()} too long: {len(text)} characters; the limit is {limit}. '
                         'Shorten it, or put the detail in a file and cite its path.')
    return text


class RuleRefused(ValueError):
    """A process rule's refusal that also opened a record for the user (E5's bell item), which the runtime announces."""

    def __init__(self, message, record=None):
        super().__init__(message)
        self.record = record


def excerpt(text, limit=HISTORY_TEXT):
    """History summaries repeat a record that is stored in full, so they are shortened visibly."""
    return text if len(text) <= limit else text[:limit - 40] + ' [...] (full text in the record)'


def _git_bytes(root, *args):
    """Raw stdout of a read-only Git command in root (hooks off), or None when Git fails or is missing."""
    try:
        result = subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull, '-C', str(root), *args],
                                capture_output=True, timeout=5,  # runs on the event loop; stale is the safe answer
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def checked_out_branch(root):
    """The full ref of the branch checked out in root ('refs/heads/...'), or '' (detached, not Git)."""
    out = _git_bytes(root, 'symbolic-ref', '-q', 'HEAD')
    return out.decode('utf-8', 'replace').strip() if out else ''


def git_location(root):
    """(branch, worktree top, root's path from that top) for a review root, or None when root is not in a
    Git worktree or HEAD is detached. The top always exists, even after a branch switch removes root."""
    out = _git_bytes(root, 'rev-parse', '--show-toplevel', '--show-prefix', '--symbolic-full-name', 'HEAD')
    lines = out.decode('utf-8', 'replace').splitlines() if out else []
    if len(lines) != 3 or not lines[2].startswith('refs/heads/'):
        return None
    return lines[2], lines[0], lines[1]


class DriftCache:
    """Board refreshes only (Zero #13117): every board broadcast re-checked every review's files, re-hashing
    and re-resolving ~1,800 files and running git per changed file on the event loop. Hashes are reused
    while a file's (mtime, size) is unchanged, resolved paths are kept, and git answers are reused for a
    short while. Approval and done checks never use this cache; they always read fresh bytes."""
    GIT_SECONDS = 60

    def __init__(self):
        self.paths, self.digests, self.git = {}, {}, {}

    def resolve(self, text):
        hit = self.paths.get(text)
        if hit is None:
            hit = self.paths[text] = Path(text).resolve()
        return hit

    def digest(self, path):
        stat = path.stat()  # OSError: missing, which the caller treats as changed
        key, mark = str(path), (stat.st_mtime_ns, stat.st_size)
        hit = self.digests.get(key)
        if hit and hit[0] == mark:
            return hit[1]
        with path.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        self.digests[key] = (mark, sha)
        return sha

    def remember(self, key, compute):
        now, hit = time.monotonic(), self.git.get(key)
        if hit and now - hit[0] < self.GIT_SECONDS:
            return hit[1]
        value = compute()
        self.git[key] = (now, value)
        return value


class CoordinationRecords:
    def __init__(self, room):
        self.room = room
        self.drift = DriftCache()

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

    @staticmethod
    def note_task(actor, task, text):
        task['updates'].append({'by': actor, 'text': excerpt(text), 'time': time.time()})
        task['updates'] = task['updates'][-TASK_HISTORY:]
        task['updated'] = time.time()

    def save(self, actor, task, text):
        if task:
            self.note_task(actor, task, text)
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
        branches = {}  # file root -> branch checked out there when the review was requested
        for relative in dict.fromkeys(selected):
            file_root, file_relative = self.review_source(task, relative, root)
            path = (file_root / file_relative).resolve()
            if Path(file_relative).is_absolute() or not path.is_relative_to(file_root):
                raise ValueError('Review files must stay inside the owner workspace')
            if any(p.lower() in ('.git', '.venv', 'node_modules') for p in path.relative_to(file_root).parts) or path.name.lower().startswith(('.env', 'credentials', 'secrets')) or path.suffix.lower() in ('.pem', '.key', '.pfx'):
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
            entry = {'path': relative, 'root': str(file_root), 'relative': file_relative, 'sha256': sha, 'bytes': len(data)}
            if file_root not in branches:
                branches[file_root] = git_location(file_root)
            if branches[file_root]:
                # Lets the approval outlive the owner switching branches (see changed_files).
                branch, top, prefix = branches[file_root]
                entry.update(branch=branch, git_root=top, git_path=prefix + file_relative.replace('\\', '/'))
            entries.append(entry)
            blobs[sha] = data
        return {'root': str(root), 'files': entries, 'unavailable': unavailable}, blobs

    def review_source(self, task, relative, primary_root):
        runtime = self.room.runtime
        primary_key = self.room.primary_repository_key()
        if ':' in relative:
            key, relative = relative.split(':', 1)
        else:
            key = self.room.task_repository_key(task)
        sources = runtime.states.get(task.get('owner'), {}).get('repositories', {})
        if key in sources:
            return Path(sources[key]['worktree']).resolve(), relative
        if key == primary_key:
            return primary_root, relative
        # Replacement/detach never deletes old roots. Reviews of a pinned task can
        # still capture its original files without interpreting them in the new primary.
        for entry in reversed(self.room.project_record().get('repository_history', [])):
            from project_registry import repository_key
            if entry.get('key', repository_key(entry['path'])) == key:
                root = entry.get('worktrees', {}).get(task.get('owner'))
                if root:
                    return Path(root).resolve(), relative
        raise ValueError('Review repository workspace is unavailable; attach it or re-scope the task')

    def changed_files(self, item, hashes=None, drift=None):
        # Optional cache belongs to one response only. Approval calls omit it,
        # so subsequent changes are always checked against fresh file bytes.
        # drift (a DriftCache) is for board refreshes only, never for approval or done checks.
        hashes = {} if hashes is None else hashes
        snapshot = item.get('snapshot', {})
        if not snapshot.get('root'):
            return []
        changed = []
        for entry in snapshot.get('files', []):
            root_text = entry.get('root', snapshot['root'])
            if drift is not None:
                root = drift.resolve(root_text)
                path = drift.resolve(str(Path(root_text) / entry.get('relative', entry['path'])))
            else:
                root = Path(root_text).resolve()
                path = (root / entry.get('relative', entry['path'])).resolve()
            if not path.is_relative_to(root):
                changed.append(entry['path'])
                continue
            try:
                if drift is not None:
                    matches = drift.digest(path) == entry['sha256']
                else:
                    if path not in hashes:
                        with path.open('rb') as stream:
                            hashes[path] = hashlib.file_digest(stream, 'sha256').hexdigest()
                    matches = hashes[path] == entry['sha256']
            except OSError:
                matches = False
            if not matches and not self.branch_holds(root, entry, hashes, drift):
                changed.append(entry['path'])
        return changed

    def branch_holds(self, root, entry, hashes, drift=None):
        """True when the owner has since checked out another branch and the reviewed branch's tip still
        holds the reviewed bytes, so switching branches does not make an approval look stale. While the
        reviewed branch is checked out, its working copy decides, so uncommitted edits still count. The
        tip is read as a checkout writes it (git cat-file --filters), the same bytes the snapshot hashed."""
        branch, top, git_path = entry.get('branch'), entry.get('git_root'), entry.get('git_path')
        if not (branch and top and git_path):
            return False  # Snapshot taken before branches were recorded, or a detached HEAD.
        if drift is not None:
            current = drift.remember(('branch', top), lambda: checked_out_branch(top))
        else:
            if ('branch', top) not in hashes:
                hashes[('branch', top)] = checked_out_branch(top)
            current = hashes[('branch', top)]
        if current == branch:
            return False
        # Run from the worktree top with the path from the top: a subfolder root may not exist on the
        # branch now checked out, and a bare 'branch:path' always resolves from the top anyway.
        spec = f'{branch}:{git_path}'
        key = ('tip', top, spec)

        def tip():
            data = _git_bytes(top, 'cat-file', '--filters', spec)
            return hashlib.sha256(data).hexdigest() if data is not None else None
        if drift is not None:
            return drift.remember(key, tip) == entry['sha256']
        if key not in hashes:
            hashes[key] = tip()
        return hashes[key] == entry['sha256']

    def artifact(self, review_id, path):
        """One captured review file exactly as it was submitted, never the current working copy.
        Only the stored record names the blob, so no caller-supplied path reaches the filesystem."""
        item = self.record('reviews', review_id)
        snapshot = item.get('snapshot', {})
        task = next((t for t in self.room.state['tasks'] if t['id'] == item['task_id']), {})
        result = {'review': {k: item.get(k) for k in REVIEW_FIELDS},
                  'task': {'id': item['task_id'], 'title': task.get('title', '')}, 'path': path}
        entry = next((f for f in snapshot.get('files', []) if f['path'] == path), None)
        if entry is None:
            if path in snapshot.get('unavailable', []):
                return {**result, 'kind': 'unavailable', 'reason': 'The file was missing when the review was requested'}
            raise LookupError('That file is not part of this review snapshot')
        try:
            data = (self.room.path.parent / 'review-artifacts' / item['id'] / entry['sha256']).read_bytes()
        except OSError:
            return {**result, 'kind': 'unavailable', 'reason': 'The captured copy is no longer stored'}
        if hashlib.sha256(data).hexdigest() != entry['sha256']:
            return {**result, 'kind': 'unavailable', 'reason': 'The captured copy does not match its recorded hash'}
        result.update(sha256=entry['sha256'], bytes=len(data),
                      changed_since=bool(self.changed_files({'snapshot': {**snapshot, 'files': [entry]}})))
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            text = None
        if text is None or '\0' in text[:8192]:
            return {**result, 'kind': 'binary'}
        return {**result, 'kind': 'ok', 'encoding': 'utf-8', 'text': text[:ARTIFACT_TEXT],
                'truncated': len(text) > ARTIFACT_TEXT}

    def review_view(self, item, hashes=None, drift=None, live_tasks=None):
        """drift/live_tasks: board refreshes only. History (superseded, cancelled) and approvals of tasks
        that are done or archived skip the file check: their drift can no longer gate anything."""
        settled = drift is not None and (item.get('status') in ('superseded', 'cancelled') or (
            item.get('status') == 'approved' and live_tasks is not None and item.get('task_id') not in live_tasks))
        return {**item, 'changed_files': [] if settled else self.changed_files(item, hashes, drift),
                'fingerprinted': bool(item.get('snapshot', {}).get('files'))}

    def scope_cap(self, task, revision):
        """E5, the scope cap (Docs/process-enforcement.md, Zero #16116 and 67ad9590): a task gets SCOPE_REVIEW_ROUNDS
        distinct reviewed revisions plus what the user grants. An agent's request for a new revision past that is
        refused, and the first refusal opens one continue-or-stop item for the user. Parallel reviews of a counted
        revision are the same round, and verdicts are never refused, so work already in review can finish."""
        from project_room import SCOPE_REVIEW_ROUNDS
        revisions = {r['revision'] for r in self.room.state.get('reviews', []) if r['task_id'] == task['id']}
        if revision in revisions:
            return
        cap = task.get('scope_cap') or {}
        if cap.get('stopped'):
            raise ValueError(f"E5: stopped by the user. Task {task['id']} takes no new review revisions from agents; "
                             'the user can request one directly, or the coordinator can split the rest into a new task.')
        limit = SCOPE_REVIEW_ROUNDS + cap.get('allowance', 0)
        if len(revisions) < limit:
            return
        items = self.room.state.setdefault('followups', [])
        item = next((f for f in items if f['id'] == cap.get('item') and f['status'] == 'open'), None)
        opened = None
        if item is None:
            now = time.time()
            item = opened = {'id': uuid.uuid4().hex[:8], 'task_id': task['id'], 'created_by': SYSTEM,
                             'assigned_to': 'user', 'kind': 'scope_cap', 'status': 'open', 'result': '',
                             'text': f"E5: task {task['id']} ({task['title']}) has had {len(revisions)} review rounds. "
                                     f'Resolve to allow {SCOPE_REVIEW_ROUNDS} more; cancel to stop new review '
                                     'requests on it; or ask the coordinator to split it.',
                             'created': now, 'updated': now}
            items.append(item)
            task['scope_cap'] = {**cap, 'item': item['id']}
            self.save(SYSTEM, task, f"Follow-up {item['id']} for user: {item['text']}")
        raise RuleRefused(f"E5: task {task['id']} has had {len(revisions)} review rounds (limit {limit}). The user "
                          f"decides in bell item {item['id']}: resolve for {SCOPE_REVIEW_ROUNDS} more rounds, or cancel "
                          'to stop. Or ask the coordinator to split the work.', opened)

    @staticmethod
    def scope_answer(item, task):
        """The user's answer to a scope-cap item (security S1: only actor user reaches here): resolve grants
        SCOPE_REVIEW_ROUNDS more rounds and lifts a stop, cancel stops new review requests from agents."""
        from project_room import SCOPE_REVIEW_ROUNDS
        cap = {key: value for key, value in (task.get('scope_cap') or {}).items() if key != 'item'}
        if item['status'] == 'resolved':
            cap['allowance'] = cap.get('allowance', 0) + SCOPE_REVIEW_ROUNDS
            cap.pop('stopped', None)
        else:
            cap['stopped'] = True
        task['scope_cap'] = cap

    def review(self, actor, action, task_id='', review_id='', reviewer='', revision='', text='', verdict='', files=None):
        reviews = self.room.state.get('reviews', [])
        if action == 'request':
            task = self.task(task_id)
            if actor != 'user' and actor != task['owner']:
                raise ValueError('Only the task owner or user can request its review')
            self.participant(reviewer)
            if reviewer == task['owner']:
                raise ValueError('Choose a peer; an owner cannot approve their own work')
            revision, text = str(revision).strip(), bounded(text, 'request')
            if not revision or len(revision) > 240 or not text:
                raise ValueError('Provide a revision reference (up to 240 characters) and a concrete review question or scope')
            for item in reviews:
                if (item['task_id'], item['reviewer'], item['revision']) == (task_id, reviewer, revision) and item['status'] == 'pending':
                    return {'item': item, 'changed': False, 'notify': []}
            if actor != 'user':
                self.scope_cap(task, revision)
            snapshot, blobs = self.capture(task, files)
            item = {'id': uuid.uuid4().hex[:8], 'task_id': task_id, 'requested_by': actor,
                    'owner': task['owner'], 'reviewer': reviewer, 'revision': revision,
                    'request': text, 'status': 'pending', 'findings': '',
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
            findings = bounded(text, 'findings')
            changed = self.changed_files(item)
            if verdict == 'approved' and changed:
                raise ValueError(f'Reviewed files changed: {changed}; request a new review revision')
            if item['status'] == verdict and item['findings'] == findings:
                return {'item': item, 'changed': False, 'notify': []}
            item.update(status=verdict, findings=findings, updated=time.time())
            summary = f"Review {item['id']} {verdict} by {actor} for {revision}: {text}"
            recipients = [task['owner'] or item['requested_by']]
        elif action == 'cancel':
            item = self.record('reviews', review_id)
            task = self.task(item['task_id'])
            if actor not in ('user', task['owner'], item['requested_by']):
                raise ValueError('Only the owner, requester or user can cancel a review')
            if not str(text).strip():
                raise ValueError('Record why the review is being cancelled')
            cancellation = bounded(text, 'cancellation')
            if item['status'] in ('cancelled', 'superseded'):
                return {'item': item, 'changed': False, 'notify': []}
            item.update(status='cancelled', cancellation=cancellation, updated=time.time())
            summary = f"Review {item['id']} cancelled by {actor}: {text}"
            recipients = [item['reviewer']]
        else:
            raise ValueError('Choose request, respond or cancel')
        self.save(actor, task, summary)
        return {'item': item, 'changed': True, 'notify': recipients, 'summary': excerpt(summary)}

    def followup(self, actor, action, followup_id='', task_id='', assigned_to='', text='', result=''):
        items = self.room.state.get('followups', [])
        if action == 'create':
            self.participant(assigned_to)
            task = self.task(task_id) if task_id else None
            text = bounded(text, 'followup')
            if not text:
                raise ValueError('Provide a concrete next action or question')
            for item in items:
                if (item['created_by'], item['assigned_to'], item['task_id'], item['text'], item['status']) == (actor, assigned_to, task_id, text, 'open'):
                    return {'item': item, 'changed': False, 'notify': []}
            item = {'id': uuid.uuid4().hex[:8], 'task_id': task_id, 'created_by': actor,
                    'assigned_to': assigned_to, 'text': text, 'status': 'open', 'result': '',
                    'created': time.time(), 'updated': time.time()}
            self.room.state.setdefault('followups', []).append(item)
            summary = f"Follow-up {item['id']} for {assigned_to}: {text}"
            recipients = [assigned_to]
        elif action in ('resolve', 'cancel'):
            item = self.record('followups', followup_id)
            if item.get('kind') == 'scope_cap' and actor != 'user':
                raise ValueError(SCOPE_ANSWER)
            allowed = ('user', item['assigned_to'], item['created_by']) if action == 'resolve' else ('user', item['created_by'])
            if actor not in allowed:
                raise ValueError('Only the assignee, requester or user can resolve; only requester/user can cancel')
            if not str(result).strip():
                raise ValueError('Record the result or cancellation reason')
            result = bounded(result, 'result')
            if item['status'] != 'open':
                return {'item': item, 'changed': False, 'notify': []}
            task = self.task(item['task_id']) if item['task_id'] else None
            item.update(status='resolved' if action == 'resolve' else 'cancelled',
                        result=result, updated=time.time())
            if item.get('kind') == 'scope_cap' and task:
                self.scope_answer(item, task)
            summary = f"Follow-up {item['id']} {item['status']} by {actor}: {result}"
            recipients = [item['assigned_to'] if action == 'cancel' or actor == item['created_by'] else item['created_by']]
        else:
            raise ValueError('Choose create, resolve or cancel')
        self.save(actor, task, summary)
        return {'item': item, 'changed': True, 'notify': recipients, 'summary': excerpt(summary)}

    def resolve_followups(self, actor, followup_ids, result):
        """Resolve several follow-ups with one result (harness retro 1b999d3b: one release receipt was posted
        once per follow-up). Every id is checked before any changes, so a bad id refuses the whole call.
        Already-closed items are skipped, as a single resolve would. One task update and one summary per call."""
        ids = list(dict.fromkeys(str(i) for i in followup_ids or ()))
        if not 1 <= len(ids) <= MAX_BATCH_RESOLVE:
            raise ValueError(f'Resolve 1 to {MAX_BATCH_RESOLVE} follow-ups at once')
        if not str(result).strip():
            raise ValueError('Record the result or cancellation reason')
        result = bounded(result, 'result')
        items = [self.record('followups', identifier) for identifier in ids]
        if actor != 'user' and any(i.get('kind') == 'scope_cap' for i in items):
            raise ValueError(SCOPE_ANSWER)
        refused = [i['id'] for i in items if actor not in ('user', i['assigned_to'], i['created_by'])]
        if refused:
            raise ValueError('Only the assignee, requester or user can resolve: ' + ', '.join(refused))
        changed = [i for i in items if i['status'] == 'open']
        if not changed:
            return {'items': items, 'changed': [], 'notify': {}}
        # Looked up before anything changes, so a missing task also refuses the whole call.
        tasks = [self.task(task_id) for task_id in dict.fromkeys(i['task_id'] for i in changed if i['task_id'])]
        now = time.time()
        for item in changed:
            item.update(status='resolved', result=result, updated=now)
            if item.get('kind') == 'scope_cap' and item['task_id']:
                self.scope_answer(item, self.task(item['task_id']))
        summary = f"Follow-ups {', '.join(i['id'] for i in changed)} resolved by {actor}: {result}"
        for task in tasks:
            self.note_task(actor, task, summary)
        self.room.save()
        # Each waiting party is told once, naming its own records (the assignee when the requester resolved).
        notify = {}
        for item in changed:
            notify.setdefault(item['assigned_to'] if actor == item['created_by'] else item['created_by'], []).append(item)
        return {'items': items, 'changed': changed, 'notify': notify, 'summary': excerpt(summary)}

    def inbox(self, actor):
        reviews = self.room.state.get('reviews', [])
        return {
            # Task entries are summaries; room_task get returns history, details and attachments.
            'transfers': [self.room.task_summary(t, check_in=True) for t in self.room.state['tasks'] if t.get('transfer', {}).get('to') == actor],
            'ready_tasks': [summary for summary in (self.room.task_summary(t, check_in=True)
                            for t in sorted(self.room.state['tasks'], key=lambda t: (t.get('priority', 3), t['updated']))
                            if t.get('owner') == actor and t.get('status') != 'done')
                            if summary['execution_state'] in ('idle', 'needs_attention')],
            'review_requests': [r for r in reviews if r['reviewer'] == actor and r['status'] == 'pending'],
            # Status first, and a lookup that cannot raise: finished tasks with old approved reviews may be
            # archived off the board (project_room.archive_done), and the inbox feeds every turn's context.
            'review_findings': [r for r in reviews if r['status'] == 'changes_requested' and next(
                (t for t in self.room.state['tasks'] if t['id'] == r['task_id']), {}).get('owner') == actor],
            'followups': [f for f in self.room.state.get('followups', []) if f['assigned_to'] == actor and f['status'] == 'open'],
            'waiting_for': [f for f in self.room.state.get('followups', []) if f['created_by'] == actor and f['assigned_to'] != actor and f['status'] == 'open'],
        }


def install(host, runtime):
    from fastapi.responses import JSONResponse

    @host.app.get('/api/runtime/review-artifact')
    async def review_artifact(project: str, review_id: str, path: str):
        """Read-only: the exact file captured for a review in the open project (UI session token only)."""
        if project != runtime.identity:
            return JSONResponse({'kind': 'error', 'error': 'This review belongs to another project; open that project first'},
                                status_code=409)
        try:
            return runtime.project_room.records.artifact(review_id, path)
        except (ValueError, LookupError) as exc:
            return JSONResponse({'kind': 'error', 'error': str(exc)}, status_code=404)
