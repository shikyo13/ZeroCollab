"""Server-side release checks. These functions do not grant authority or queue deploys.

All paths and records come from the host, never room_release arguments. In
particular artifact_root is the room's immutable review-artifacts directory.
The caller must serialize staging against changes to the installed package and
revalidate the returned fingerprints before admitting a deployment.
"""
import hashlib
import re
from pathlib import Path

from release_pack import check_release, safe_relative

TEXT_SUFFIXES = {'.py', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.css',
                 '.html', '.svg', '.json', '.md', '.txt', '.toml', '.yaml', '.yml',
                 '.ps1', '.cmd', '.bat', '.sh', '.rs', '.lock'}


def normalized_hash(data):
    """Normalise UTF-8 text CRLF only; binary assets retain every byte."""
    if b'\0' not in data:
        try:
            data.decode('utf-8')
        except UnicodeDecodeError:
            pass
        else:
            data = data.replace(b'\r\n', b'\n')
    return hashlib.sha256(data).hexdigest()


def _files(manifest):
    result, names = {}, set()
    for item in manifest['files']:
        path = item['path']
        if str(safe_relative(path)) != path or path.casefold() in names:
            raise ValueError('Noncanonical or duplicate payload path: ' + path)
        if not re.fullmatch(r'[0-9a-f]{64}', str(item.get('sha256', ''))):
            raise ValueError('Invalid payload digest: ' + path)
        result[path] = item['sha256']
        names.add(path.casefold())
    return result


def _read_inside(root, relative, limit=None):
    root = Path(root).resolve()
    path = root.joinpath(*relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Release evidence escapes its host-owned directory')
    with path.open('rb') as stream:
        data = stream.read() if limit is None else stream.read(limit + 1)
    if limit is not None and len(data) > limit:
        raise ValueError('Review artifact exceeds its pinned size')
    return data


def _same_source_path(entry, payload_path):
    # Modern captures pin their repository-relative path. An unrelated scratch
    # copy must not cover shipped bytes merely because its suffix matches.
    # A nested-checkout capture with an outer-root git_path needs a fresh
    # canonical-path review; never infer a path remapping from equal contents.
    for field in ('git_path', 'relative'):
        if field in entry:
            return entry[field] == payload_path
    path = entry.get('path')
    return isinstance(path, str) and (path == payload_path or path.endswith('/' + payload_path))


def review_coverage(package, predecessor, review_ids, records, artifact_root, *, report_uncovered=False):
    """Verify Security f92cae36 P1 against immutable review snapshots.

    Both packages must already be produced by release_pack. Review ids select
    host-loaded records; snapshot blobs are read by their pinned raw digest,
    checked, then normalised exactly as candidate files. Mutable author trees,
    patch text and caller-provided result hashes cannot supply coverage.

    This strict path has no exception flag. Deletions and files lacking exact
    approved bytes refuse; a future grant-bound urgent exception must be handled
    explicitly by admission, never inferred here. VERSION and built files have
    no implicit exemption. The return is provenance, not deployment permission,
    rehearsal success, integrity of the live room or feature acceptance.
    """
    if (not isinstance(review_ids, list) or len(review_ids) > 12
            or any(not isinstance(i, str) or not re.fullmatch(r'[0-9a-f]{8}', i)
                   for i in review_ids) or len(set(review_ids)) != len(review_ids)):
        raise ValueError('Choose at most twelve distinct review ids')
    candidate_manifest, candidate = check_release(Path(package))
    old_manifest, old = check_release(Path(predecessor))
    files, old_files = _files(candidate_manifest), _files(old_manifest)
    removed = sorted(set(old_files) - set(files))
    if sorted(candidate_manifest['deletions']) != removed:
        raise ValueError('Candidate deletions do not match the installed predecessor')
    if removed:
        raise ValueError('Deletion requires explicit fingerprint-bound approval: ' + ', '.join(removed))

    approved = {}
    for identifier in review_ids:
        record = records.get(identifier)
        if not record or record.get('id') != identifier or record.get('status') != 'approved':
            raise ValueError('Review is not approved: ' + identifier)
        entries = record.get('snapshot', {}).get('files', [])
        if not isinstance(entries, list) or len(entries) > 32:
            raise ValueError('Invalid review snapshot: ' + identifier)
        total = 0
        for entry in entries:
            digest = entry.get('sha256', '')
            size = entry.get('bytes')
            if (not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
                    or type(size) is not int or not 0 <= size <= 2_000_000):
                raise ValueError('Invalid review artifact metadata: ' + identifier)
            total += size
            if total > 2_000_000:
                raise ValueError('Review snapshot exceeds its capture limit: ' + identifier)
            data = _read_inside(artifact_root, (identifier, digest), size)
            if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('Review artifact fingerprint changed: ' + identifier)
            evidence = {
                'review_id': identifier, 'snapshot_path': entry.get('path'),
                'snapshot_sha256': digest,
            }
            source = {key: entry[key] for key in ('path', 'git_path', 'relative') if key in entry}
            evidence['_source'] = source
            approved.setdefault(('binary', digest), []).append(evidence)
            approved.setdefault(('text', normalized_hash(data)), []).append(evidence)

    coverage, missing = {}, []
    for path, digest in files.items():
        if digest == old_files.get(path):
            continue
        data = _read_inside(Path(package) / 'payload', safe_relative(path).parts)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('Candidate payload changed during review: ' + path)
        mode = 'text' if path == 'VERSION' or Path(path).suffix.lower() in TEXT_SUFFIXES else 'binary'
        normalized = normalized_hash(data) if mode == 'text' else digest
        matches = [{key: value for key, value in entry.items() if key != '_source'}
                   for entry in approved.get((mode, normalized), [])
                   if _same_source_path(entry['_source'], path)]
        if not matches:
            missing.append(path)
        else:
            coverage[path] = {'sha256': digest, 'normalized_sha256': normalized,
                              'reviews': matches}
    if missing and not report_uncovered:
        raise ValueError('No approved snapshot covers payload bytes: ' + ', '.join(sorted(missing)))
    return {'manifest_sha': candidate['manifest_sha'],
            'predecessor_manifest_sha': old['manifest_sha'],
            'coverage': coverage, 'deletions': [], **({'uncovered': sorted(missing)} if report_uncovered else {})}
