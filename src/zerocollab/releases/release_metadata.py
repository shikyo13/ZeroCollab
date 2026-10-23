"""Bound stage metadata to the package fingerprint without changing the host packer."""
import copy
import hashlib
import json
import os
import re

FIELDS = frozenset({'title', 'notes', 'shipped_tasks'})


def text(value, limit, name, *, multiline=False, required=False):
    if (not isinstance(value, str) or len(value) > limit
            or any(ord(c) < 32 and (not multiline or c not in '\n\t\r') for c in value)
            or (required and not value.strip())):
        raise ValueError('Invalid bounded stage metadata: ' + name)
    return value.strip()


def argument(name, value):
    if name in ('title', 'notes'):
        return text(value, 120 if name == 'title' else 16000, name, multiline=name == 'notes')
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError('Choose at most 32 shipped tasks')
    result, seen = [], set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {'task_id', 'scope'}:
            raise ValueError('Each shipped task needs only task_id and scope')
        identifier = entry['task_id']
        if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{8}', identifier) or identifier in seen:
            raise ValueError('Shipped tasks need distinct task ids')
        seen.add(identifier)
        result.append({'task_id': identifier, 'scope': text(entry['scope'], 1000, 'scope', multiline=True, required=True)})
    return result


def resolve(manager, values):
    """Resolve owners on this project's board, both at admission and publication."""
    result = {key: argument(key, values[key]) for key in FIELDS if key in values}
    if 'shipped_tasks' in result:
        tasks = {t['id']: t for t in manager.runtime.project_room.state.get('tasks', [])}
        for entry in result['shipped_tasks']:
            task = tasks.get(entry['task_id'])
            if not task or task.get('closure') or not task.get('owner'):
                raise ValueError('A shipped task is missing, closed or unowned')
            entry['owner'] = task['owner']
    return result


def package(package, metadata):
    """Seal metadata in a private package before review coverage or publication."""
    if not metadata:
        return package
    from zerocollab.releases.release_pack import canonical, check_release, write_json
    manifest, release = check_release(package)
    manifest['release_metadata'] = copy.deepcopy(metadata)
    raw = canonical(manifest)
    digest = hashlib.sha256(raw).hexdigest()
    destination = package.with_name(digest[:12])
    if destination.exists():
        raise ValueError('Private metadata package already exists')
    (package / 'manifest.json').write_bytes(raw)
    release.update(metadata, id=digest[:12], manifest_sha=digest)
    write_json(package / 'RELEASE.json', release)
    os.replace(package, destination)
    check_release(destination, digest)
    return destination


def verify(package, *, stage=None):
    """Legacy packages stay valid; declared metadata must agree everywhere."""
    manifest = json.loads((package / 'manifest.json').read_text('utf-8'))
    if 'release_metadata' not in manifest:
        if stage and any(key in stage for key in FIELDS):
            raise ValueError('Stage metadata has no manifest binding')
        return
    metadata = manifest['release_metadata']
    if not isinstance(metadata, dict) or set(metadata) - FIELDS:
        raise ValueError('Invalid package metadata')
    release = json.loads((package / 'RELEASE.json').read_text('utf-8'))
    if {key: release[key] for key in FIELDS if key in release} != metadata:
        raise ValueError('Release metadata differs from its manifest')
    if stage is not None and {key: stage[key] for key in FIELDS if key in stage} != metadata:
        raise ValueError('Stage metadata differs from its manifest')
