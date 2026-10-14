"""Agent admission policy for the existing deployment queue, not a second engine.

Payload/review hashing runs in an owned bounded worker. Queueing and every drain
poll recheck host records synchronously; no caller supplies acceptance or paths.
"""
import copy
import hashlib
import json
import re
import shutil
import sys
import time
from pathlib import Path

SECONDS = 120
MAX_HISTORY = 4096
ID = re.compile(r'[0-9a-f]{32}')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def acceptance(manager, manifest):
    board = manager.runtime.project_room.state
    bindings = [n for n in board.get('notes', []) if n.get('kind') == 'decision'
                and n.get('status') == 'active'
                and (n.get('release_checks') or {}).get('manifest_sha') == manifest]
    if len(bindings) != 1:
        raise ValueError('Exactly one active checklist for the installed manifest is required')
    note = bindings[0]
    pm = manager.runtime.manager_wakes.selected_manager()
    if note.get('by') not in ('user', pm, 'system'):
        raise ValueError('The checklist must belong to the selected manager or user')
    pins = note['release_checks'].get('tasks')
    if not isinstance(pins, list) or not 1 <= len(pins) <= 32:
        raise ValueError('The installed checklist has no bounded task set')
    tasks = {t['id']: t for t in board.get('tasks', [])}
    results, seen = [], set()
    for pin in pins:
        identifier, owner = pin.get('id'), pin.get('owner')
        task = tasks.get(identifier)
        if identifier in seen or not owner or not task or task.get('owner') != owner:
            raise ValueError('A required check is missing or reassigned')
        seen.add(identifier)
        latest = next((r for r in reversed(task.get('acceptance_log', []))
                       if r.get('manifest_sha') == manifest), None)
        if not latest or latest.get('result') != 'passed' or latest.get('by') not in (owner, 'user'):
            raise ValueError('Every installed-release check must have an owner-authored pass')
        results.append({'task': identifier, 'owner': owner, 'entry': copy.deepcopy(latest)})
    return {'decision': copy.deepcopy(note), 'results': results}


def receipts(manager):
    paths = list(manager._path('receipts').glob('*.json'))
    if len(paths) > MAX_HISTORY:
        raise ValueError('Deployment history exceeds the admission scan bound')
    return [manager._record('receipts/' + p.name) for p in paths if ID.fullmatch(p.stem)]


def predecessor(manager, current):
    matches = [r for r in receipts(manager) if r and r.get('status') == 'deployed'
               and r.get('release_id') == current['id']
               and (r.get('request') or {}).get('manifest_sha') == current['manifest_sha']]
    matches.sort(key=lambda r: str(r.get('finished', '')), reverse=True)
    if not matches or (len(matches) > 1 and matches[0].get('finished') == matches[1].get('finished')):
        raise ValueError('The current installation has no unambiguous terminal receipt')
    latest = matches[0]
    identifier, sha = latest.get('previous_release_id'), latest.get('previous_manifest_sha')
    if not isinstance(identifier, str) or not manager.ID.fullmatch(identifier) or not isinstance(sha, str) or sha[:12] != identifier:
        raise ValueError('The immediately preceding install has no exact retained fingerprint')
    return identifier, sha, fingerprint(latest)


def evidence(manager, identifier, manifest, current, rollback):
    prefix = 'releases/' + identifier
    stage = manager._record(prefix + '/stage.json')
    rehearsal = manager._record(prefix + '/rehearsal.json')
    if not stage or not rehearsal:
        raise ValueError('A host stage and rehearsal receipt are required')
    expected = (identifier, manifest, manager.runtime.identity)
    if tuple(stage.get(k) for k in ('release_id', 'manifest_sha', 'project')) != expected:
        raise ValueError('Stage receipt belongs to another package or project')
    if tuple(rehearsal.get(k) for k in ('release_id', 'manifest_sha', 'project')) != expected or rehearsal.get('status') != 'passed':
        raise ValueError('A successful host rehearsal for this package is required')
    from release_metadata import verify
    verify(manager._path(prefix), stage=stage)
    base = stage.get('predecessor_manifest_sha', '')
    if (not isinstance(base, str) or not re.fullmatch(r'[0-9a-f]{64}', base)
            or rehearsal.get('predecessor_manifest_sha') != base
            or (not rollback and base != current['manifest_sha'])):
        raise ValueError('Package evidence has a different installed predecessor')
    ids = stage.get('reviews')
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 12
            or any(not isinstance(i, str) or not re.fullmatch(r'[0-9a-f]{8}', i) for i in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError('Stage receipt has no valid reviewed provenance')
    all_records = {r.get('id'): r for r in manager.runtime.project_room.state.get('reviews', [])}
    records = {}
    for i in ids:
        r = all_records.get(i)
        if (not r or r.get('status') != 'approved' or not r.get('reviewer') or not r.get('requested_by')
                or r['reviewer'] in (r['requested_by'], r.get('owner'))):
            raise ValueError('Every source review must be approved by an independent reviewer')
        records[i] = copy.deepcopy(r)
    return {'stage': stage, 'rehearsal': rehearsal, 'records': records}


def authority(manager, caller, expected):
    from release_manager import package_identity
    rt = manager.runtime
    if manager.closing or manager.state.rehearsal or caller['project'] != rt.identity:
        raise ValueError('Release authority belongs to the active non-rehearsal room')
    if rt.paused or rt.manual_stop is None or rt.manual_stop_pending:
        raise ValueError('The room is stopped or manual control is pending')
    latch = rt.manual_stop.snapshot()
    if latch['stopped']:
        raise ValueError('User Stop prevents deployment')
    grant = manager.state.release_grant.require(caller, rt.profiles.items.get('astra'))
    current = manager._record('current.json', {})
    if current.get('manifest_sha') != expected or manager.running != package_identity(current):
        raise ValueError('The installed or running manifest changed')
    return current, grant, latch


def prepare(manager, caller, action, values):
    current, grant, latch = authority(manager, caller, values['expected_manifest_sha'])
    rollback = action == 'rollback'
    previous = None
    if rollback:
        identifier, manifest, previous = predecessor(manager, current)
    else:
        identifier, manifest = values['release_id'], values['manifest_sha']
    proof = evidence(manager, identifier, manifest, current, rollback)
    status = manager._record('status.json', {})
    if status.get('agent_release_protocol') != 1 or not 0 <= time.time() - float(status.get('updated', 0)) <= 15:
        raise ValueError('The separately installed supervisor protocol is unavailable')
    try:
        accepted = acceptance(manager, current['manifest_sha'])
    except ValueError as exc:
        if rollback:
            raise ValueError('Agent rollback requires passed current-release checks. '
                             'Use the user Updates rollback control when those checks fail.') from exc
        raise
    context = {'caller': copy.deepcopy(caller), 'values': copy.deepcopy(values), 'action': action,
               'release_id': identifier, 'manifest_sha': manifest, 'current': current,
               'grant_revision': grant['revision'], 'stop_generation': latch['generation'],
               'acceptance': accepted, 'evidence': proof,
               'previous': previous}
    return context


def token(context):
    return {key: copy.deepcopy(context[key]) for key in ('action', 'values', 'release_id', 'manifest_sha',
            'grant_revision', 'stop_generation', 'previous')} | {
            'current': fingerprint(context['current']), 'acceptance': fingerprint(context['acceptance']),
            'evidence': fingerprint(context['evidence'])}


def validate(manager, request):
    """Cheap record-only revalidation during drain; the author run may have ended."""
    caller = {'profile': request.get('profile_id'), 'project': request.get('project_id'), 'run': request.get('run_id')}
    saved = request.get('acceptance_receipt')
    if not isinstance(saved, dict) or saved.get('action') not in ('request', 'rollback'):
        raise ValueError('Request has no host admission receipt')
    from release_interface import arguments
    values = arguments(saved['action'], saved.get('values'))
    context = prepare(manager, caller, saved['action'], values)
    if token(context) != saved:
        raise ValueError('Request authority or evidence changed after queueing')
    for key in ('release_id', 'manifest_sha', 'grant_revision', 'stop_generation'):
        if request.get(key) != context[key]:
            raise ValueError('Request metadata differs from host admission')
    if (request.get('approved_by') != 'release-manager:astra' or request.get('protocol') != 1
            or not exact_drain(request, manager.state.root)
            or request.get('expected_manifest_sha') != values['expected_manifest_sha']
            or request.get('rollback') is not (saved['action'] == 'rollback')
            or request.get('idempotency_key') != values['idempotency_key']):
        raise ValueError('Request is not an exact agent approval')
    return True


def exact_drain(request, root):
    """The request's interrupt and drain are exactly the server's agent policy for this host (opus c11b3a54): the
    same room_deploy.agent_drain_policy that stamped them, read from the same status.json. Literal types only, so
    no other value passes."""
    from room_deploy import agent_drain_policy, read_json
    interrupt, seconds = request.get('interrupt'), request.get('drain_seconds')
    if not isinstance(interrupt, bool) or not isinstance(seconds, int) or isinstance(seconds, bool):
        return False
    return (interrupt, seconds) == agent_drain_policy(read_json(Path(root) / 'status.json', {}) or {})


def existing(manager, caller, action, values):
    records = receipts(manager)
    journal = manager._record('journal.json', {})
    records.append(journal)
    paths = list(manager._path('requests').glob('*'))
    if len(paths) > MAX_HISTORY:
        raise ValueError('Request history exceeds its scan bound')
    records.extend(manager._record('requests/' + p.name) for p in paths
                   if p.suffix in ('.json', '.taken') and ID.fullmatch(p.stem))
    found = {}
    for record in records:
        request = (record or {}).get('request') or record or {}
        if (request.get('project_id'), request.get('profile_id'), request.get('idempotency_key')) != (
                caller['project'], caller['profile'], values['idempotency_key']):
            continue
        saved = request.get('acceptance_receipt', {})
        if (saved.get('action'), saved.get('values')) != (action, values):
            raise ValueError('Idempotency key already belongs to another request')
        found[request['id']] = request
    if len(found) > 1:
        raise ValueError('Ambiguous idempotency history')
    return next(iter(found.values()), None)


def worker(directory):
    from release_agent import review_coverage
    from release_pack import check_release, write_json
    data = json.loads((directory / 'input.json').read_text('utf-8'))
    context = data['context']
    proof, root = context['evidence'], Path(data['root'])
    package = root / 'releases' / context['release_id']
    base = proof['stage']['predecessor_manifest_sha']
    # Bound actual files before the existing packer's whole-file hashing.
    for identifier in {context['release_id'], base[:12], context['current']['id']}:
        folder = root / 'releases' / identifier
        inventory = json.loads((folder / 'manifest.json').read_text('utf-8'))['files']
        if len(inventory) > 4096:
            raise ValueError('Package inventory exceeds preflight bounds')
        total = 0
        from release_pack import safe_relative
        for item in inventory:
            path = (folder / 'payload' / safe_relative(item['path'])).resolve()
            if not path.is_relative_to((folder / 'payload').resolve()):
                raise ValueError('Payload escapes retained package')
            size = path.stat().st_size
            total += size
            if size > 128 * 1024 ** 2 or total > 2 * 1024 ** 3:
                raise ValueError('Package bytes exceed preflight bounds')
    coverage = review_coverage(package, root / 'releases' / base[:12], proof['stage']['reviews'],
                               proof['records'], Path(data['artifacts']))
    if (coverage['manifest_sha'], coverage['predecessor_manifest_sha'], coverage['coverage']) != (
            context['manifest_sha'], base, proof['stage']['coverage']):
        raise ValueError('Exact reviewed payload changed after rehearsal')
    check_release(root / 'releases' / context['current']['id'], context['current']['manifest_sha'])
    write_json(directory / 'result.json', {'admission': fingerprint(token(context))})


async def submit(manager, job, context):
    from release_stage import run_worker
    from release_rehearse import environment
    from room_deploy import approval_lock, atomic_json, _approve_release_locked
    directory = manager._path('work/' + job['id'])
    directory.mkdir(parents=True, exist_ok=False)
    queued = None
    try:
        atomic_json(directory / 'input.json', {'context': context, 'root': str(manager.root),
                    'artifacts': str(manager.runtime.project_room.path.parent / 'review-artifacts')})
        def check():
            if job['stop'].is_set() or not manager.state.admission_open():
                raise ValueError('Request preflight cancelled or admission closed')
            fresh = prepare(manager, context['caller'], context['action'], context['values'])
            if token(fresh) != token(context):
                raise ValueError('Request authority changed during preflight')
        await run_worker(directory, check, helper=Path(__file__).resolve(), env=environment(directory), seconds=SECONDS)
        if manager._record('work/' + job['id'] + '/result.json') != {'admission': fingerprint(token(context))}:
            raise ValueError('Request preflight produced no exact proof')
        with manager.state.lock, approval_lock(manager.root):
            check()
            caller, values = context['caller'], context['values']
            queued = existing(manager, caller, context['action'], values)
            if not queued:
                queued = _approve_release_locked(manager.state, {
                    'release_id': context['release_id'], 'manifest_sha': context['manifest_sha'],
                    'rollback': context['action'] == 'rollback', 'interrupt': False}, approval={
                    'approved_by': 'release-manager:astra', 'project_id': caller['project'],
                    'profile_id': caller['profile'], 'run_id': caller['run'], 'protocol': 1,
                    'grant_revision': context['grant_revision'], 'grant_decision': 'user-enabled',
                    'expected_manifest_sha': values['expected_manifest_sha'],
                    'stop_generation': context['stop_generation'], 'idempotency_key': values['idempotency_key'],
                    'acceptance_receipt': token(context)})
        return {'deployment_id': queued['id'], 'release_id': queued['release_id'],
                'manifest_sha': queued['manifest_sha'], 'state': 'queued'}
    finally:
        target, work = directory.resolve(), manager._path('work')
        if target.parent != work or target.name != job['id']:
            raise ValueError('Refusing cleanup outside request preflight directory')
        shutil.rmtree(target)


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    worker(Path(sys.argv[1]).resolve())
