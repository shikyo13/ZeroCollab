"""E4 host-owned donor checks and handoff dispositions.

Checks reuse stage's isolated bounded worker. No caller supplies coverage,
actor identity, filesystem paths or an included assertion. Published stage
receipts are the commit record for inclusion, including after a crash.
"""
import copy
import hashlib
import os
import re
import shutil
import time
from pathlib import Path

from release_pack import canonical

LIMIT = 256
CHECK_INTERVAL = 30
CHECK_ID = re.compile(r'[0-9a-f]{32}')


def save(manager, relative, value):
    from room_deploy import atomic_json
    if len(canonical(value)) > 2_000_000:
        raise ValueError('E4: intake record exceeds its size bound')
    atomic_json(manager._path(relative), value)


def collection(manager, kind):
    paths = sorted(manager._path('intake/' + kind).glob('*.json'))
    if len(paths) > LIMIT:
        raise ValueError('E4: intake history exceeds its scan bound')
    return [manager._record('intake/' + kind + '/' + p.name)
            for p in paths if CHECK_ID.fullmatch(p.stem)]


def stored_check(manager, identifier):
    return (manager._record('intake/checks/' + identifier + '.json')
            or manager._record('intake/archive/checks/' + identifier + '.json'))


def check_record(manager, identifier):
    if not isinstance(identifier, str) or not CHECK_ID.fullmatch(identifier):
        raise ValueError('E4: a server-issued check id is required')
    value = stored_check(manager, identifier)
    if (not value or value.get('id') != identifier or value.get('status') != 'passed'
            or value.get('project') != manager.runtime.identity):
        raise ValueError('E4: no passed check for this project')
    return value


def reviews_valid(manager, context):
    records = {r.get('id'): r for r in manager.runtime.project_room.state.get('reviews', [])}
    if any(records.get(i) != context['records'].get(i) or records[i].get('status') != 'approved'
           for i in context['reviews']):
        raise ValueError('E4: donor review evidence changed; run check again')


def dependencies(manager, identifiers, current, repository, seen=()):
    """Resolve only host records; reject cycles and stale/unrelated checks."""
    if len(seen) > 12:
        raise ValueError('E4: dependency chain exceeds its bound')
    result = {}
    for identifier in identifiers:
        if identifier in seen:
            raise ValueError('E4: dependency cycle')
        if len(identifier) == 32:
            record = check_record(manager, identifier)
            validate_check(manager, record, (*seen, identifier))
            if record['repository'] != repository:
                raise ValueError('E4: donor dependency belongs to another repository')
            result[identifier] = {'commit': record['commit'], 'manifest_sha': record['manifest_sha'],
                                  'fingerprint': hashlib.sha256(canonical(record)).hexdigest()}
        else:
            release = manager._record('releases/' + identifier + '/RELEASE.json')
            stage = manager._record('releases/' + identifier + '/stage.json')
            raw = manager._bytes('releases/' + identifier + '/manifest.json')
            if (not release or not raw or hashlib.sha256(raw).hexdigest() != release.get('manifest_sha')
                    or release.get('id') != identifier):
                raise ValueError('E4: dependency has no exact retained release')
            if identifier != current['id']:
                if (not stage or stage.get('repository') != repository
                        or stage.get('project') != manager.runtime.identity
                        or stage.get('manifest_sha') != release['manifest_sha']
                        or stage.get('predecessor_manifest_sha') != current['manifest_sha']):
                    raise ValueError('E4: unreleased dependency has no current-base stage receipt')
                records = {r.get('id'): r for r in manager.runtime.project_room.state.get('reviews', [])}
                if not stage.get('reviews') or any(records.get(i, {}).get('status') != 'approved'
                                                   for i in stage['reviews']):
                    raise ValueError('E4: dependency reviews are no longer approved')
            result[identifier] = {'commit': release['commit'], 'manifest_sha': release['manifest_sha']}
    return result


def validate_check(manager, record, seen=()):
    context = record['context']
    current = manager._record('current.json')
    if current != context['current']:
        raise ValueError('E4: check has a stale installed base; run check again')
    reviews_valid(manager, context)
    from project_registry import repositories
    repo = next((r for r in repositories(manager.runtime) if r['key'] == record['repository']), None)
    if not repo or str(Path(repo['path']).resolve()) != context['repository']:
        raise ValueError('E4: registered repository changed')
    resolved = dependencies(manager, context['dependencies'], current, record['repository'], seen)
    if resolved != context['dependency_records']:
        raise ValueError('E4: donor dependencies changed; run check again')


def prepare(manager, caller, values):
    from project_registry import repositories
    # The stage helper's authority policy is intentionally not used for a check.
    repo = next((r for r in repositories(manager.runtime) if r['key'] == values['repository']), None)
    if not repo:
        raise ValueError('Choose a registered project repository')
    current = manager._record('current.json')
    if not current or not manager.ID.fullmatch(current.get('id', '')):
        raise ValueError('E4: an installed predecessor is required')
    records = {r['id']: copy.deepcopy(r) for r in manager.runtime.project_room.state.get('reviews', [])
               if r.get('id') in values['reviews']}
    if any(records.get(i, {}).get('status') != 'approved' for i in values['reviews']):
        raise ValueError('E4: every selected review must be approved')
    resolved = dependencies(manager, values['dependencies'], current, repo['key'])
    if values['base'] not in {current['commit'], *(r['commit'] for r in resolved.values())}:
        raise ValueError('E4: base must be installed or an explicitly declared checked dependency')
    prior = collection(manager, 'checks')
    if len(prior) >= LIMIT:
        raise ValueError('E4: check history is full; the grant holder can use room_release archive with check_id')
    if any(r.get('profile') == caller['profile'] and time.time() - r.get('created', 0) < CHECK_INTERVAL
           for r in prior):
        raise ValueError('E4: wait thirty seconds before another donor check')
    return {'repository': str(Path(repo['path']).resolve()), 'repository_key': repo['key'],
            'commit': values['commit'], 'reviews': values['reviews'], 'records': records,
            'current': current, 'predecessor': str(manager._path('releases/' + current['id'])),
            'artifacts': str(manager.runtime.project_room.path.parent / 'review-artifacts'),
            'caller': copy.deepcopy(caller), 'base': values['base'],
            'dependencies': values['dependencies'], 'dependency_records': resolved, 'intake_check': True}


def revalidate(manager, context):
    rt = manager.runtime
    manager._caller(context['caller'], donor=True)
    if (manager.closing or not manager.state.admission_open() or rt.paused
            or rt.manual_stop is None or rt.manual_stop_pending or rt.manual_stop.snapshot()['stopped']):
        raise ValueError('E4: donor checks wait until the room reopens')
    if manager._record('current.json') != context['current']:
        raise ValueError('E4: installed base changed during check')
    reviews_valid(manager, context)
    resolved = dependencies(manager, context['dependencies'], context['current'], context['repository_key'])
    if resolved != context['dependency_records']:
        raise ValueError('E4: donor dependencies changed during check')


async def run_check(manager, job, context):
    from release_stage import run_worker
    directory = manager._path('work/' + job['id'])
    directory.mkdir(parents=True, exist_ok=False)
    try:
        save(manager, 'work/' + job['id'] + '/input.json', context)
        def check():
            if job['stop'].is_set():
                raise ValueError('E4: donor check cancelled')
            revalidate(manager, context)
        await run_worker(directory, check)
        result = manager._record('work/' + job['id'] + '/result.json')
        if (not result or not manager.ID.fullmatch(result.get('release_id', ''))
                or result.get('predecessor_manifest_sha') != context['current']['manifest_sha']):
            raise ValueError('E4: check worker returned a different predecessor')
        if not result.get('coverage') and not result.get('uncovered'):
            raise ValueError('E4: donor has no changed files')
        with manager.state.lock:
            check()
            record = {**result, 'id': job['id'], 'status': 'failed' if result.get('uncovered') else 'passed',
                      'project': context['caller']['project'], 'profile': context['caller']['profile'],
                      'run': context['caller']['run'], 'repository': context['repository_key'],
                      'commit': context['commit'], 'base': context['base'], 'created': time.time(),
                      'task_ids': sorted({r['task_id'] for r in context['records'].values() if r.get('task_id')}),
                      'context': context}
            save(manager, 'intake/checks/' + job['id'] + '.json', record)
        return public_check(record)
    finally:
        target, work = directory.resolve(), manager._path('work')
        if target.parent != work or target.name != job['id']:
            raise ValueError('Refusing cleanup outside the owned check directory')
        shutil.rmtree(target)


def public_check(record):
    return {k: copy.deepcopy(record[k]) for k in ('id', 'status', 'project', 'profile', 'repository',
            'commit', 'base', 'created', 'manifest_sha', 'predecessor_manifest_sha', 'coverage', 'task_ids', 'uncovered')
            if k in record}


def handoffs(manager):
    """Staging is provisional; only exact deployed receipts finalize inclusion."""
    from release_request import receipts
    deployed = {(r.get('release_id'), (r.get('request') or {}).get('manifest_sha')):
                r.get('finished', '') for r in receipts(manager) if r.get('status') == 'deployed'}
    rows = {r['id']: r for r in collection(manager, 'handoffs') if r.get('project') == manager.runtime.identity}
    stages = sorted(manager._path('releases').glob('*/stage.json'))
    if len(stages) > LIMIT:
        raise ValueError('E4: stage history exceeds its scan bound')
    for path in stages:
        if not manager.ID.fullmatch(path.parent.name):
            continue
        receipt = manager._record('releases/' + path.parent.name + '/stage.json')
        if not receipt or receipt.get('project') != manager.runtime.identity:
            continue
        for entry in receipt.get('handoff_dispositions', []):
            row = rows.get(entry.get('id'))
            if (row and entry.get('state') == 'included' and entry.get('check_id') == row['check_id']
                    and entry.get('release_id') == receipt.get('release_id') == path.parent.name):
                event = entry['history'][-1]
                if (event.get('state') != 'included' or event.get('source') != 'stage_publication'
                        or event.get('release_id') != path.parent.name
                        or event.get('manifest_sha') != receipt.get('manifest_sha')):
                    raise ValueError('E4: inclusion event differs from its publication')
                if event not in row['history']:
                    row['history'].append(event)
    for row in rows.values():
        # Do not let a lexically later package override a later manual disposition.
        # Keep every prior event, including stages which were never deployed.
        row['history'].sort(key=lambda event: (event['time'], canonical(event)))
        installed = [e for e in row['history'] if e.get('state') == 'included'
                     and (e.get('release_id'), e.get('manifest_sha')) in deployed]
        event = (max(installed, key=lambda e: (deployed[e['release_id'], e['manifest_sha']], e['time']))
                 if installed else row['history'][-1])
        row.update(state=event['state'], reason=event['reason'], target=event.get('target', ''),
                   deployed=bool(installed))
        row.pop('release_id', None)
        if event['state'] == 'included':
            row['release_id'] = event['release_id']
    return sorted(rows.values(), key=lambda r: r['id'])


def archive_check(manager, caller, identifier):
    """Grant-holder recovery even at the scan ceiling; immutable evidence remains usable."""
    source = manager._path('intake/checks/' + identifier + '.json')
    destination = manager._path('intake/archive/checks/' + identifier + '.json')
    record = stored_check(manager, identifier)
    if not record or record.get('id') != identifier or record.get('project') != caller['project']:
        raise ValueError('E4: unknown project check')
    if source.exists():
        if destination.exists():
            raise ValueError('E4: archive destination already exists')
        # Audit precedes the move. A crash can leave an intent, never lose the check.
        save(manager, 'intake/archive/events/' + identifier + '.json',
             {'check_id': identifier, 'actor': caller['profile'], 'run': caller['run'],
              'time': time.time(), 'source': 'archive_check'})
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
    return {'check_id': identifier, 'archived': True}


def mutate(manager, caller, action, values):
    import uuid
    with manager.state.lock:
        manager._caller(caller)
        manager.state.release_grant.require(caller, manager.runtime.profiles.items.get('astra'))
        rt = manager.runtime
        if (not manager.state.admission_open() or rt.paused or rt.manual_stop is None
                or rt.manual_stop_pending or rt.manual_stop.snapshot()['stopped']):
            raise ValueError('E4: intake mutations wait until the room reopens')
        if action == 'archive':
            return archive_check(manager, caller, values['check_id'])
        rows = handoffs(manager)
        now = time.time()
        if action == 'handoff':
            check = check_record(manager, values['check_id'])
            validate_check(manager, check)
            existing = next((r for r in rows if r['check_id'] == check['id']), None)
            if existing:
                return existing
            if len(rows) >= LIMIT:
                raise ValueError('E4: handoff history is full')
            row = {'id': uuid.uuid4().hex, 'check_id': check['id'], 'project': caller['project'],
                   'repository': check['repository'], 'predecessor_manifest_sha': check['predecessor_manifest_sha'],
                   'task_ids': check['task_ids'], 'state': 'open', 'history': []}
            event = {'state': 'open', 'actor': caller['profile'], 'run': caller['run'], 'time': now,
                     'reason': 'Checked donor accepted'}
        else:
            row = next((r for r in rows if r['id'] == values['handoff_id']), None)
            if row is None or row['project'] != caller['project']:
                raise ValueError('E4: unknown project handoff')
            if row.get('deployed'):
                raise ValueError('E4: a deployed release already included this handoff; create a new checked donor')
            event = {'state': values['disposition'], 'actor': caller['profile'], 'run': caller['run'],
                     'time': now, 'reason': values['reason'], 'target': values['target']}
        row.pop('release_id', None)
        row.update(state=event['state'], reason=event['reason'], target=event.get('target', ''), deployed=False)
        row['history'].append(event)
        save(manager, 'intake/handoffs/' + row['id'] + '.json', row)
        return copy.deepcopy(row)


def stage_snapshot(manager, context, result=None):
    """Called at admission and under publication lock, using the current set."""
    rows = handoffs(manager)
    for row in rows:
        if row['project'] != context['caller']['project']:
            continue
        if row['state'] != 'open' and not (row['state'] == 'included' and not row.get('deployed')):
            continue
        if row['predecessor_manifest_sha'] != context['current']['manifest_sha']:
            # Do not quietly lose an outstanding handoff across an install.
            raise ValueError('E4: stale open handoff ' + row['id'] + '; defer/drop it or check its donor again')
        check = check_record(manager, row['check_id'])
        validate_check(manager, check)
        if result is None:
            continue
        coverage = result['coverage']
        if (row['repository'] != context['repository_key'] or any(
                path not in coverage or coverage[path]['normalized_sha256'] != item['normalized_sha256']
                for path, item in check['coverage'].items())):
            previous = (' previously included in unshipped ' + row['release_id']) if row.get('release_id') else ''
            raise ValueError('E4: handoff ' + row['id'] + previous
                             + ' is absent from this package; defer/drop or include')
        row.update(state='included', release_id=result['release_id'], deployed=False)
        row['history'].append({'state': 'included', 'actor': context['caller']['profile'],
            'run': context['caller']['run'], 'time': time.time(), 'source': 'stage_publication',
            'release_id': result['release_id'], 'manifest_sha': result['manifest_sha'],
            'check_id': check['id'], 'reason': 'All checked donor bytes have P1 coverage in this package'})
    return rows
