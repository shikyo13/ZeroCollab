"""Terminal receipt adapter to the runtime's existing durable release wake queue.

Never resumes the room. Stop defers delivery, grant revision changes suppress it,
and the runtime calls allowed() again immediately before a queued turn starts.
"""
from itertools import islice
import re

ID = re.compile(r'[0-9a-f]{32}')
SHA = re.compile(r'[0-9a-f]{64}')
RECIPIENTS = ('astra', 'opus')
OUTCOMES = {'deployed', 'aborted', 'rejected', 'rolled_back'}
SCAN_LIMIT = 4096
PAGE = 16


def terminal(manager, identifier):
    if not isinstance(identifier, str) or not ID.fullmatch(identifier):
        raise ValueError('A terminal deployment id is required')
    receipt = manager._record('receipts/' + identifier + '.json')
    if not receipt or receipt.get('id') != identifier or receipt.get('status') not in OUTCOMES:
        raise ValueError('No settled deployment receipt')
    request = receipt.get('request') or {}
    if (request.get('id') != identifier or request.get('approved_by') != 'release-manager:astra'
            or request.get('protocol') != 1 or request.get('profile_id') != 'astra'
            or not isinstance(request.get('project_id'), str) or not request['project_id']
            or not isinstance(request.get('run_id'), str) or not request['run_id']
            or type(request.get('grant_revision')) is not int or request['grant_revision'] < 1):
        raise ValueError('Receipt has no agent request identity')
    # An aborted attempt never installed its candidate. A failed install that
    # rolled back also ends on the recorded predecessor. Do not label either
    # candidate as installed in the completion prompt.
    if receipt['status'] == 'deployed':
        release_id, manifest = receipt.get('release_id'), request.get('manifest_sha')
    else:
        release_id = receipt.get('previous_release_id')
        manifest = receipt.get('previous_manifest_sha') or request.get('expected_manifest_sha')
        if release_id is None and isinstance(manifest, str):
            release_id = manifest[:12]
    if (not isinstance(manifest, str) or not SHA.fullmatch(manifest) or release_id != manifest[:12]):
        raise ValueError('Receipt has no exact resulting installed fingerprint')
    return receipt, request, release_id, manifest


def allowed(manager, key, recipient):
    """Read-only; false preserves the pending entry, including across Resume."""
    try:
        if recipient not in RECIPIENTS or not isinstance(key, str):
            return False
        identifier, separator, named = key.partition(':')
        if not separator or named != recipient:
            return False
        rt, state = manager.runtime, manager.state
        if (manager.closing or state.rehearsal or not state.admission_open() or rt.paused
                or rt.manual_stop is None or rt.manual_stop_pending or rt.manual_stop.snapshot()['stopped']):
            return False
        receipt, request, release_id, manifest = terminal(manager, identifier)
        if request['project_id'] != rt.identity:
            return False
        grant = state.release_grant.require({'profile': 'astra', 'project': rt.identity, 'run': request['run_id']},
                                            rt.profiles.items.get('astra'))
        if grant['revision'] != request['grant_revision']:
            return False
        from release_manager import package_identity
        current = manager._record('current.json', {})
        if ((current.get('id'), current.get('manifest_sha')) != (release_id, manifest)
                or manager.running != package_identity(current)):
            return False
        # Recovery outcomes never wake from this adapter. A terminal receipt
        # with a still-present journal also waits for supervisor finalisation.
        if manager._record('journal.json', {}):
            return False
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def consume(manager):
    rt, state = manager.runtime, manager.state
    summary = {'examined': 0, 'persisted': 0, 'invalid': 0}
    if (manager.closing or state.rehearsal or not rt.identity
            or getattr(rt, 'release_completion_guarded', False) is not True):
        return {**summary, 'held': 'runtime not ready'}
    if not state.enter_mutation():
        return {**summary, 'held': 'writes blocked'}
    try:
        paths = sorted(islice(manager._path('receipts').glob('*.json'), SCAN_LIMIT + 1))
        if len(paths) > SCAN_LIMIT:
            return {**summary, 'held': 'receipt scan limit'}
        if not paths:
            return summary
        start = getattr(manager, 'completion_cursor', 0) % len(paths)
        for offset in range(min(PAGE, len(paths))):
            path = paths[(start + offset) % len(paths)]
            summary['examined'] += 1
            try:
                receipt, request, identifier, manifest = terminal(manager, path.stem)
                if request['project_id'] != rt.identity:
                    continue
                release = manager._record('releases/' + identifier + '/RELEASE.json', {})
                version = release.get('version') if release.get('manifest_sha') == manifest else None
                # Persist even while stopped/revoked; the runtime's guarded
                # restore and pre-turn checks decide whether it can be offered.
                # The dedup key and pending record share the runtime atomic write.
                added = rt.enqueue_release_completion({'deployment_id': receipt['id'],
                    'receipt_id': receipt['id'], 'project': request['project_id'], 'outcome': receipt['status'],
                    'version': str(version or identifier)[:120], 'manifest': manifest,
                    'reason': 'Terminal receipt recorded; verify installed identity before reporting acceptance'}, RECIPIENTS)
                summary['persisted'] += len(added)
            except (OSError, ValueError, TypeError, KeyError):
                summary['invalid'] += 1
        manager.completion_cursor = (start + summary['examined']) % len(paths)
        return summary
    finally:
        state.leave_mutation()
