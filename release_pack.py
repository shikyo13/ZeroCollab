#!/usr/bin/env python3
"""Package a committed revision as a release the supervisor can deploy.

  python release_pack.py build --repo REPO --commit REV --spool LIVE/data/deploy [--base REV]
  python release_pack.py materialize RELEASE_ID TARGET_DIR --spool LIVE/data/deploy [--from LIVE]
  python release_pack.py check RELEASE_ID --spool LIVE/data/deploy

A release is every file tracked at the commit, minus the paths the supervisor owns. It is
written to SPOOL/releases/<id>/ as manifest.json (canonical bytes; id = sha256[:12]),
RELEASE.json (the manifest plus metadata) and payload/<path>.

Deletions are the files of the previous release that the new one no longer has. The previous
release is SPOOL/current.json when the supervisor has deployed before, otherwise --base (the
commit the live tree was last deployed from). Without either, nothing is deleted.
"""
import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath

# Never shipped by a release: the supervisor, its launchers and the old helper must stay
# stable while a release changes everything else; data and environments are not source.
SUPERVISOR_OWNED = {'supervisor.py', 'release_pack.py', 'start-room.ps1', 'stop-room.ps1',
                    'start-room.cmd', 'stop-room.cmd', 'deploy_when_idle.py'}
PROTECTED_TOP = {'data', 'uploads', '.git', '.venv', 'providers', '.github', '.claude', '.gemini', '.codex'}
SKIP_FILES = {'.gitattributes', '.gitignore'}
# Live configuration and credentials: never shipped, never copied into a rehearsal tree (it brings
# its own config). config.toml is included because the live room's settings must survive deployments.
LOCAL_ONLY = ('config.toml', 'config.local.toml', '.env', '*.local.toml', '.env.*', '*.pem', '*.key', '*.pfx', 'credentials*', 'secrets*')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    """Atomic: readers see the old file or the new one, never a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def shipped(path):
    """True when a repository path belongs in a release."""
    parts = PurePosixPath(path).parts
    if not parts or path in SKIP_FILES or any(fnmatch.fnmatch(parts[-1].lower(), p) for p in LOCAL_ONLY):
        return False
    return parts[0].lower() not in PROTECTED_TOP and path.lower() not in SUPERVISOR_OWNED


def safe_relative(path):
    rel = PurePosixPath(path)
    if rel.is_absolute() or '..' in rel.parts or ':' in path or '\\' in path or not rel.parts:
        raise ValueError('Unsafe release path: ' + path)
    if not shipped(path):
        raise ValueError('Release path is supervisor-owned or protected: ' + path)
    return rel


def git(repo, *args):
    run = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, check=False)
    if run.returncode:
        raise RuntimeError(f'git {" ".join(args)} failed: {run.stderr.decode(errors="replace")[-800:]}')
    return run.stdout


def tree(repo, commit):
    """{path: blob sha} of regular files at a commit (submodules and symlinks refused)."""
    entries = {}
    for line in git(repo, 'ls-tree', '-r', '-z', '--full-tree', commit).split(b'\0'):
        if not line:
            continue
        meta, path = line.split(b'\t', 1)
        mode, kind, blob = meta.decode().split()
        path = path.decode('utf-8')
        if not shipped(path):
            continue
        if kind != 'blob' or mode not in ('100644', '100755'):
            raise RuntimeError(f'{path} is a {kind} ({mode}); releases hold regular files only')
        entries[path] = blob
    return entries


def canonical(manifest):
    return json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode('utf-8')


def build(args):
    repo, spool = args.repo.resolve(), args.spool.resolve()
    commit = git(repo, 'rev-parse', '--verify', args.commit + '^{commit}').decode().strip()
    files = tree(repo, commit)
    previous = spool / 'current.json'
    if previous.is_file():
        prior = {f['path'] for f in json.loads(previous.read_text(encoding='utf-8'))['files']}
        base = 'current.json'
    elif args.base:
        prior = set(tree(repo, args.base))
        base = git(repo, 'rev-parse', '--verify', args.base + '^{commit}').decode().strip()
    else:
        prior, base = set(), None
    releases = spool / 'releases'
    releases.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.build-', dir=releases))  # Same volume: the final rename is atomic.
    try:
        listing = []
        for path in sorted(files):
            # Working-tree bytes (eol and filters applied), so files match a normal checkout of the commit.
            data = git(repo, 'cat-file', '--filters', f'{commit}:{path}')
            target = staging / 'payload' / safe_relative(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            listing.append({'path': path, 'sha256': sha256(data)})
        manifest = {'commit': commit, 'files': listing, 'deletions': sorted(prior - set(files))}
        for path in manifest['deletions']:
            safe_relative(path)
        raw = canonical(manifest)
        digest = sha256(raw)
        dest = releases / digest[:12]
        if dest.exists():
            check_release(dest, digest)
            print(json.dumps({'id': dest.name, 'manifest_sha': digest, 'existing': True}))
            return
        (staging / 'manifest.json').write_bytes(raw)
        write_json(staging / 'RELEASE.json', {
            'id': dest.name, 'manifest_sha': digest, 'commit': commit, 'base': base,
            'version': git(repo, 'show', f'{commit}:VERSION').decode().strip() if 'VERSION' in files else '',
            'subject': git(repo, 'log', '-1', '--format=%s', commit).decode(errors='replace').strip(),
            'created': time.strftime('%Y-%m-%d %H:%M:%S'),
            'files': listing, 'deletions': manifest['deletions']})
        os.replace(staging, dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    check_release(dest, digest)
    print(json.dumps({'id': dest.name, 'manifest_sha': digest, 'commit': commit,
                      'files': len(listing), 'deletions': len(manifest['deletions'])}))


def check_release(dest, digest=None):
    """Validate a release directory; returns (manifest, release). Raises on any mismatch."""
    raw = (dest / 'manifest.json').read_bytes()
    actual = sha256(raw)
    if digest and actual != digest.lower():
        raise RuntimeError(f'{dest.name}: manifest sha {actual} does not match {digest}')
    if actual[:12] != dest.name:
        raise RuntimeError(f'{dest.name}: directory name does not match manifest sha {actual}')
    manifest = json.loads(raw)
    if canonical(manifest) != raw:
        raise RuntimeError(f'{dest.name}: manifest is not canonical')
    release = json.loads((dest / 'RELEASE.json').read_text(encoding='utf-8'))
    if (release.get('id'), release.get('manifest_sha'), release.get('commit')) != (dest.name, actual, manifest['commit']) \
            or release.get('files') != manifest['files'] or release.get('deletions') != manifest['deletions']:
        raise RuntimeError(f'{dest.name}: RELEASE.json disagrees with manifest.json')
    for item in manifest['files']:
        payload = dest / 'payload' / safe_relative(item['path'])
        if not payload.is_file() or sha256(payload.read_bytes()) != item['sha256']:
            raise RuntimeError(f'{dest.name}: payload changed: {item["path"]}')
    for path in manifest['deletions']:
        safe_relative(path)
    return manifest, release


def materialize(args):
    """Candidate tree for rehearsal: a copy of --from (default: no base) with the release applied."""
    dest = args.spool.resolve() / 'releases' / args.release_id
    manifest, _ = check_release(dest)
    target = args.target.resolve()
    if target.exists() and any(target.iterdir()):
        sys.exit(f'{target} is not empty')
    if args.source:
        source = args.source.resolve()
        skip = shutil.ignore_patterns(*PROTECTED_TOP, *LOCAL_ONLY, '__pycache__', 'node_modules', '*.log')
        shutil.copytree(source, target, ignore=skip, dirs_exist_ok=True)
    for path in manifest['deletions']:
        (target / safe_relative(path)).unlink(missing_ok=True)
    for item in manifest['files']:
        out = target / safe_relative(item['path'])
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dest / 'payload' / item['path'], out)
    print(json.dumps({'id': args.release_id, 'target': str(target), 'files': len(manifest['files'])}))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build')
    b.add_argument('--repo', required=True, type=Path)
    b.add_argument('--commit', required=True)
    b.add_argument('--base', default='')
    b.add_argument('--spool', required=True, type=Path)
    m = sub.add_parser('materialize')
    m.add_argument('release_id')
    m.add_argument('target', type=Path)
    m.add_argument('--spool', required=True, type=Path)
    m.add_argument('--from', dest='source', type=Path)
    c = sub.add_parser('check')
    c.add_argument('release_id')
    c.add_argument('--spool', required=True, type=Path)
    args = parser.parse_args()
    if args.command == 'build':
        build(args)
    elif args.command == 'materialize':
        materialize(args)
    else:
        manifest, release = check_release(args.spool.resolve() / 'releases' / args.release_id)
        print(json.dumps({'id': release['id'], 'commit': release['commit'], 'files': len(manifest['files']),
                          'deletions': len(manifest['deletions'])}))


if __name__ == '__main__':
    main()
