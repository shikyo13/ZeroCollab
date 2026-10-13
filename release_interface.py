"""Strict public arguments for room_release; authorization stays in the host core.

The adapter supplies caller={profile, project, run} from the authenticated run.
These arguments never carry identity, authority, paths, commands or receipts.
Rollback always resolves the immediately preceding installation on the server.
"""
import re


PROTOCOL = 1
MUTATIONS = frozenset({'stage', 'rehearse', 'request', 'rollback'})
FIELDS = {
    'list': frozenset(),
    'stage': frozenset({'repository', 'commit', 'reviews'}),
    'rehearse': frozenset({'release_id', 'manifest_sha'}),
    'request': frozenset({'release_id', 'manifest_sha', 'expected_manifest_sha', 'idempotency_key'}),
    'status': frozenset({'id'}),
    'verify': frozenset({'deployment_id'}),
    'rollback': frozenset({'expected_manifest_sha', 'idempotency_key'}),
}
PATTERNS = {
    'repository': r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',
    'commit': r'[0-9a-f]{40}',
    'release_id': r'[0-9a-f]{12}',
    'manifest_sha': r'[0-9a-f]{64}',
    'expected_manifest_sha': r'[0-9a-f]{64}',
    'idempotency_key': r'[A-Za-z0-9_-]{1,64}',
    'id': r'[0-9a-f]{32}',
    'deployment_id': r'[0-9a-f]{32}',
}


def arguments(action, value):
    """Validate/copy public input before dispatch; this does not grant authority.

    Status with no id reports the current deployment, gate and bounded jobs.
    A supplied id selects either a packaging/rehearsal/verification job or the
    deployment journal/receipt. Jobs and requests use server-generated UUID hex.
    """
    if not isinstance(action, str) or action not in FIELDS:
        raise ValueError('Choose list, stage, rehearse, request, status, verify or rollback')
    if not isinstance(value, dict):
        raise ValueError('Release arguments must be an object')
    expected = FIELDS[action]
    if set(value) - expected or (action != 'status' and set(value) != expected):
        raise ValueError('Unexpected or missing release arguments')
    result = {}
    for name, item in value.items():
        if name == 'reviews':
            if (not isinstance(item, list) or not 1 <= len(item) <= 12
                    or any(not isinstance(review, str) or not re.fullmatch(r'[0-9a-f]{8}', review)
                           for review in item)
                    or len(set(item)) != len(item)):
                raise ValueError('Choose 1-12 distinct approved review ids')
            result[name] = list(item)
        else:
            if not isinstance(item, str) or not re.fullmatch(PATTERNS[name], item):
                raise ValueError('Invalid release argument: ' + name)
            result[name] = item
    return result
