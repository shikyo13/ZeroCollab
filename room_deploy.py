"""App side of the supervisor protocol. This module never stops or replaces the server."""
import asyncio
import hashlib
import json
import os
import re
import threading
import time
import uuid
from functools import wraps
from contextlib import contextmanager
from pathlib import Path

from durable import atomic_write
from release_control import ReleaseGrant

from fastapi import Request
from fastapi.responses import JSONResponse

TERMINAL = {'deployed', 'aborted', 'rolled_back', 'rollback_failed', 'conflict', 'recovery_required', 'rejected'}
AGENT_DRAIN_SECONDS = 30   # an agent-requested install: running turns get this long, then are interrupted (9f3eca50)
AGENT_IDLE_DRAIN_SECONDS = 600  # the same request on a host without the interrupt capability: wait for idle
RECENT_RECEIPTS = 10       # receipts the deployments feed lists in full
MAX_RECEIPT_SCAN = 4096    # receipts read for install dates, newest first (release_request's history bound)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text('utf-8-sig'))
    except FileNotFoundError:
        return default


def release_order(item):
    """Releases sort by semantic version, then build time; staging order and folder names don't matter."""
    numbers = tuple(int(part) for part in re.findall(r'\d+', str((item or {}).get('version') or ''))[:3])
    return numbers, str((item or {}).get('created', ''))


def atomic_json(path, value):
    atomic_write(path, lambda stream: json.dump(value, stream, indent=2))


@contextmanager
def approval_lock(root):
    """Serialize web and tray approvals, including their pending-request check."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'approval.lock').open('a+b') as handle:
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError('Another deployment approval is in progress') from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def agent_drain_policy(status):
    """(interrupt, drain_seconds) for an agent-requested install: the server's policy, never the caller's body, and
    the one rule both _approve_release_locked and release_request.validate apply (opus c11b3a54). No agent holds up
    an install (opus 7cdd51bd): a short drain, then running turns are interrupted, queues kept and the turns resumed;
    the user's Stop still wins. Only when the host supervisor advertises that it takes an agent interrupt: an older
    one refuses it at take (security 4b29e072 B1), so it keeps the wait-for-idle request."""
    if (status or {}).get('agent_install_interrupt') == 1:
        return True, AGENT_DRAIN_SECONDS
    return False, AGENT_IDLE_DRAIN_SECONDS


def approve_release(state, body, *, allow_offline=False, supervisor_online=None, allow_interrupt=False):
    """One fingerprinted approval path for the room and native controller.

    Offline validation returns a transport without writing a request. The tray
    starts the existing supervisor (or bootstraps an unversioned room) and must
    validate this same fingerprint again before queuing an online request.
    allow_interrupt is passed only by the room's own approve route (Install now).
    """
    with state.lock, approval_lock(state.root):
        return _approve_release_locked(state, body, allow_offline=allow_offline,
                                       supervisor_online=supervisor_online, allow_interrupt=allow_interrupt)


def _approve_release_locked(state, body, *, allow_offline=False, supervisor_online=None,
                            approval=None, allow_interrupt=False):
    """Shared queue core; caller holds both approval locks.

    approval is server-produced agent audit metadata, never an HTTP/tool body.
    Agent policy must validate the grant, Stop, acceptance and expected current
    fingerprint under the same locks before entering this core. A caller asks to
    interrupt only through the user's room route (Install now, 9f3eca50): never
    the tray or an offline transport (security 8401e355 R1). An agent approval
    never chooses: agent_drain_policy sets its interrupt and drain (opus 7cdd51bd).
    """
    state.version = read_json(state.root / 'current.json', {}) or {}
    item = state.release(body.get('release_id'))
    if body.get('manifest_sha') != item['manifest_sha']:
        raise ValueError('Candidate changed; review the release shown now')
    if not item['rehearsal_passed']:
        raise ValueError('This exact package must pass rehearsal before approval')
    if item['relation'] == 'current':
        raise ValueError('This release is already running')
    if item['relation'] == 'older' and body.get('rollback') is not True:
        raise ValueError(f"{item.get('version') or item['id']} is older than the running release; "
                         'choose it under Earlier releases to roll back deliberately')
    status = read_json(state.root / 'status.json', {}) or {}
    age = time.time() - float(status.get('updated', 0))
    online = age <= 15 and supervisor_online is not False
    if not online and not allow_offline:
        raise ValueError('Supervisor is offline; external bootstrap is required')
    if online and status.get('state') not in ('running', 'ready', 'idle'):
        raise ValueError('The supervisor is busy or needs recovery')
    if (state.root / 'journal.json').exists():
        raise ValueError('A deployment is in progress or needs recovery')
    if any((state.root / 'requests').glob('*.json')):
        raise ValueError('A deployment is already queued')
    if state.reload()['writes'] != 'open' or state.gate['admission'] != 'open':
        raise ValueError('The room is closed for deployment or recovery')
    interrupt = body.get('interrupt', False)
    if not isinstance(interrupt, bool):
        raise ValueError('interrupt must be true or false')
    if interrupt and (not allow_interrupt or approval is not None or allow_offline):
        raise ValueError('This control waits for current turns; only Install now on the Updates page interrupts them')
    seconds = body.get('drain_seconds', 600)
    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 10 <= seconds <= 3600:
        raise ValueError('Drain time must be between 10 and 3600 seconds')
    if approval is not None:
        interrupt, seconds = agent_drain_policy(status)
    identifier = uuid.uuid4().hex
    record = {'id': identifier, 'release_id': item['id'], 'manifest_sha': item['manifest_sha'],
              'approved_by': 'user', 'approved_at': time.time(), 'drain_seconds': seconds,
              'interrupt': interrupt, 'rollback': body.get('rollback') is True or item['relation'] == 'older'}
    if approval is not None:
        if allow_offline or not isinstance(approval, dict) or approval.get('approved_by') != 'release-manager:astra':
            raise ValueError('Invalid agent approval context')
        allowed = {'approved_by', 'project_id', 'profile_id', 'run_id', 'grant_revision',
                   'grant_decision', 'expected_manifest_sha', 'stop_generation',
                   'idempotency_key', 'protocol', 'acceptance_receipt'}
        if set(approval) - allowed:
            raise ValueError('Unknown agent approval metadata')
        record.update(approval)
    if not online:
        return {**record, 'transport': 'start' if state.version else 'bootstrap'}
    atomic_json(state.root / 'requests' / (identifier + '.json'), record)
    return record


class DeploymentState:
    def __init__(self, data):
        self.root = Path(data).resolve() / 'deploy'
        self.lock = threading.RLock()
        self.release_grant = ReleaseGrant(self.root, self.lock)
        # The runtime opts in only when its blocked-phase user routes implement
        # latch-only updates; old adapters must remain behind the write gate.
        self.manual_control_latch_only = False
        self.mutations = 0
        self.gate = {}
        self.stamp = None
        self.version = read_json(self.root / 'current.json', {}) or {}
        self.rehearsal = os.environ.get('ZC_REHEARSAL') == '1'
        self.reload()

    def reload(self):
        with self.lock:
            path = self.root / 'maintenance.json'
            try:
                stat = path.stat()
                stamp = (stat.st_mtime_ns, stat.st_size)
                if stamp != self.stamp:
                    value = read_json(path)
                    if not isinstance(value, dict) or value.get('admission') not in ('open', 'closed') or value.get('writes') not in ('open', 'blocked') or not isinstance(value.get('seq'), int):
                        raise ValueError('Invalid maintenance record')
                    self.gate = value
                    self.stamp = stamp
            except FileNotFoundError:
                self.gate = {'seq': 0, 'admission': 'open', 'writes': 'open'}
                self.stamp = None
            except (OSError, ValueError, TypeError):
                self.gate = {'seq': -1, 'admission': 'closed', 'writes': 'blocked', 'reason': 'Maintenance state cannot be read safely'}
                self.stamp = None
            return dict(self.gate)

    def admission_open(self):
        return not self.rehearsal and self.reload()['admission'] == 'open' and self.gate['writes'] == 'open'

    def enter_mutation(self):
        with self.lock:
            if self.reload()['writes'] != 'open':
                return False
            self.mutations += 1
            return True

    def leave_mutation(self):
        with self.lock:
            self.mutations -= 1

    def track_continuation(self):
        # Store callbacks belong to a write already admitted by HTTP/MCP or a running turn.
        # Count them before scheduling, so drain cannot stop between storage and routing.
        with self.lock:
            self.mutations += 1

    def guard_tool(self, fn):
        @wraps(fn)
        def guarded(*args, **kwargs):
            if not self.enter_mutation():
                return 'Error: deploying; the room is temporarily read-only. Try again after it reconnects.'
            try:
                return fn(*args, **kwargs)
            finally:
                self.leave_mutation()
        return guarded

    def releases(self):
        result = []
        for folder in sorted((self.root / 'releases').glob('*')):
            if not re.fullmatch(r'[0-9a-f]{12}', folder.name) or folder.is_symlink():
                continue
            try:
                item = self.release(folder.name)
                result.append(item)
            except (OSError, ValueError, TypeError):
                continue
        return sorted(result, key=release_order, reverse=True)

    def release(self, identifier):
        if not re.fullmatch(r'[0-9a-f]{12}', str(identifier or '')):
            raise ValueError('Choose a staged release')
        folder = self.root / 'releases' / identifier
        if not folder.resolve().is_relative_to((self.root / 'releases').resolve()):
            raise ValueError('Release is outside the staging directory')
        item = read_json(folder / 'RELEASE.json')
        raw = (folder / 'manifest.json').read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        manifest = json.loads(raw)
        if not isinstance(item, dict) or item.get('id') != identifier or item.get('manifest_sha') != sha or identifier != sha[:12]:
            raise ValueError('Release fingerprint changed; prepare and review it again')
        if not isinstance(manifest, dict) or any(item.get(key) != manifest.get(key) for key in ('commit', 'files', 'deletions')):
            raise ValueError('Release details disagree with the approved manifest')
        evidence = read_json(folder / 'rehearsal.json', {}) or {}
        ready = isinstance(evidence, dict) and (evidence.get('release_id'), evidence.get('manifest_sha'), evidence.get('status')) == (identifier, sha, 'passed')
        # review.json (written when staging, outside the fingerprint) names the board reviews behind
        # this exact package; the panel shows their live board state, not a claim in the package.
        linked = read_json(folder / 'review.json', {}) or {}
        review_ids = [str(r) for r in linked.get('reviews', []) if isinstance(r, str)][:12] if isinstance(linked, dict) and             (linked.get('release_id'), linked.get('manifest_sha')) == (identifier, sha) else []
        if identifier == self.version.get('id'):
            relation = 'current'
        else:
            relation = 'newer' if release_order(item) > release_order(self.version) else 'older'
        return {**item, 'rehearsal_passed': ready, 'relation': relation, 'review_ids': review_ids,
                'rehearsal': 'Passed for this exact package.' if ready else 'Required rehearsal has not passed for this package.'}

    def snapshot(self):
        receipts, installed = [], {}
        paths = sorted((self.root / 'receipts').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        for index, path in enumerate(paths[:MAX_RECEIPT_SCAN]):
            try:
                item = read_json(path)
            except (OSError, ValueError):
                continue
            if not isinstance(item, dict):
                continue
            if index < RECENT_RECEIPTS:
                receipts.append(item)
            finished = item.get('finished')
            if item.get('status') == 'deployed' and isinstance(finished, str) and finished:
                # The changelog's install date (frontend 377eb442, backend 67ce6389): the latest successful install
                # of each release, from every receipt, never the package's build time.
                release = item.get('release_id')
                installed[release] = max(installed.get(release, ''), finished)
        releases = self.releases()
        for release in releases:
            if release['id'] in installed:
                release['installed_at'] = installed[release['id']]
        status = read_json(self.root / 'status.json', {}) or {}
        return {'current': self.version, 'supervisor': status, 'maintenance': self.reload(),
                'releases': releases, 'receipts': receipts,
                'pending': [read_json(p, {}) for p in (self.root / 'requests').glob('*.json')]}


class MutationGate:
    def __init__(self, app, state):
        self.app, self.state = app, state

    async def __call__(self, scope, receive, send):
        mutation = scope['type'] == 'http' and scope['method'] not in ('GET', 'HEAD', 'OPTIONS')
        path = scope.get('path', '')
        # Only grant storage is always outside the rollback snapshot. Stop and
        # Resume count as mutations while writes are open. A compatible runtime
        # may handle them while blocked using ONLY the host latch, deferring all
        # room-data effects. Auth middleware still protects every route.
        grant_control = path == '/api/runtime/deployments/grant'
        manual_control = path in {'/api/runtime/stop', '/api/runtime/resume'}
        if self.state.rehearsal and mutation and ('/repository' in path or path.startswith(('/api/open-path', '/api/runtime/providers', '/api/runtime/login', '/api/runtime/settings'))):
            await JSONResponse({'error': 'External operations are disabled during rehearsal'}, 503)(scope, receive, send)
            return
        entered = False
        if mutation and not grant_control:
            entered = self.state.enter_mutation()
            if not entered:
                if manual_control and self.state.manual_control_latch_only:
                    scope['release_latch_only'] = True
                else:
                    await JSONResponse({'error': 'Deploying: the room is temporarily read-only'}, 503)(scope, receive, send)
                    return
        try:
            await self.app(scope, receive, send)
        finally:
            if entered:
                self.state.leave_mutation()


def install(host, runtime):
    state = DeploymentState(runtime.data)
    # Paired with the runtime's latch-only handlers and deferred reconciler.
    # Older adapters keep the fail-closed default behind the final write gate.
    state.manual_control_latch_only = all(callable(getattr(runtime, name, None)) for name in
                                         ('user_stop', 'user_resume', 'reconcile_manual_stop'))
    host.deployment = state
    runtime.deployment = state
    from release_manager import ReleaseManager
    runtime.release_manager = ReleaseManager(runtime, Path(__file__).resolve().parent)
    from release_maintenance import ReleaseMaintenance
    release_maintenance = ReleaseMaintenance(host, runtime, state)
    host.app.add_middleware(MutationGate, state=state)
    import mcp_bridge
    mcp_bridge.deployment = state

    @host.app.get('/api/version')
    async def version():
        return state.version

    @host.app.get('/api/runtime/maintenance')
    async def maintenance():
        gate = state.reload()
        # Explicit interrupt cancels active provider turns; it never discards pending wakes.
        if gate.get('interrupt') and gate['admission'] == 'closed':
            names = [name for name in runtime.states if name in runtime.running]
            mark = getattr(runtime, 'install_interrupt', None)
            for name in names:
                if mark:
                    mark(name, gate.get('deployment_id', ''))  # resumed after the install (9f3eca50)
            tasks = [runtime.states[name].get('task') for name in names]
            for task in tasks:
                if task:
                    task.cancel()
            if tasks:
                await asyncio.gather(*(t for t in tasks if t), return_exceptions=True)
        active = len(runtime.running) + sum(bool(s.get('task')) and not s.get('waiting') and name not in runtime.running for name, s in runtime.states.items())
        with state.lock:
            mutations = state.mutations
        if gate['writes'] == 'blocked' and active == 0 and mutations == 0:
            runtime.save_deploy_queue()
        return {'seq_applied': gate['seq'], 'admission': gate['admission'], 'writes': gate['writes'],
                'active_runs': active, 'active_mutations': mutations, 'release_id': state.version.get('id', ''),
                'project_id': runtime.identity, 'paused': runtime.paused}

    @host.app.get('/api/runtime/deployments/control/{identifier}')
    async def release_control_status(identifier: str):
        try:
            return release_maintenance.status(identifier)
        except (OSError, ValueError, TypeError) as exc:
            return JSONResponse({'error': str(exc)}, 409)

    @host.app.post('/api/runtime/deployments/control/{identifier}')
    async def release_control_action(identifier: str, request: Request):
        # Existing UI/session authentication applies; managed run tokens cannot
        # reach this route. MutationGate counts it normally, with no exemption.
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {'action'}:
                raise ValueError('Choose a maintenance action only')
            if body['action'] == 'announce':
                return release_maintenance.announce(identifier)
            if body['action'] == 'block':
                return await release_maintenance.block(identifier)
            if body['action'] == 'release':
                return await release_maintenance.release(identifier)
            raise ValueError('Unknown maintenance action')
        except (OSError, ValueError, TypeError, TimeoutError) as exc:
            return JSONResponse({'error': str(exc)}, 409)

    def board_reviews(ids):
        records = {r.get('id'): r for r in runtime.project_room.state.get('reviews', [])}
        tasks = {t.get('id'): t.get('title', '') for t in runtime.project_room.state.get('tasks', [])}
        return [{'id': i, 'status': 'not_found'} if i not in records else
                {k: records[i].get(k, '') for k in ('id', 'status', 'reviewer', 'revision', 'task_id')}
                | {'task_title': tasks.get(records[i].get('task_id'), '')} for i in ids]

    @host.app.get('/api/runtime/deployments')
    async def deployments():
        try:
            snapshot = state.snapshot()
            for item in snapshot['releases']:
                item['reviews'] = board_reviews(item.pop('review_ids', []))
            return snapshot
        except (OSError, ValueError, TypeError) as exc:
            return JSONResponse({'error': 'Deployment records unavailable: ' + str(exc)}, 503)

    @host.app.post('/api/runtime/deployments/approve')
    async def approve(request: Request):
        try:
            body = await request.json()
            return approve_release(state, body, allow_interrupt=True)  # the user's Updates page only
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            return JSONResponse({'error': str(exc)}, 409)

    @host.app.get('/api/runtime/deployments/grant')
    async def release_grant():
        try:
            return state.release_grant.snapshot()
        except (OSError, ValueError, TypeError) as exc:
            return JSONResponse({'error': str(exc), 'enabled': False}, 503)

    @host.app.post('/api/runtime/deployments/grant')
    async def change_release_grant(request: Request):
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {'enabled', 'expected_revision'}:
                raise ValueError('Choose enabled state and expected revision only')
            with state.lock:
                if state.rehearsal:
                    raise ValueError('Release grants cannot change during rehearsal')
                if body['enabled'] is True and not state.admission_open():
                    raise ValueError('Wait for deployment to finish before enabling a grant')
                return state.release_grant.set_user_grant(
                    enabled=body['enabled'], expected_revision=body['expected_revision'],
                    project=runtime.identity, profile=runtime.profiles.items.get('astra'))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            return JSONResponse({'error': str(exc)}, 409)
