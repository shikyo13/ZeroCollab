"""Read-only views of files referenced in chat messages.

A reference ("static/chat.js:42", "app.py", "C:/repo/x.py:10-20") is resolved against the
sender's worktrees first, then the project repositories, then the other agents' worktrees.
Only files inside those roots, or files Git tracks in another local repository, are shown;
private tool folders (.git, .env, .ssh ...) never are. Content is size-bounded and labelled
with the Git state it came from, so a reader knows whether it is what the message meant.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

import git_preview
from room_runtime import PRIVATE_PARTS

MAX_BYTES = 1_000_000  # About 20k lines of code; larger files open in the editor instead.
SNIFF_BYTES = 8192
MAX_CANDIDATES = 20
# Line suffixes: ":42", ":42-60", ":42:7" (column ignored), "#L42", "#L42-L60".
REF = re.compile(r'^(?P<path>.+?)(?::(?P<start>\d+)(?:-(?P<end>\d+)|:\d+)?|#L(?P<lstart>\d+)(?:-L?(?P<lend>\d+))?)?$')
MSYS_DRIVE = re.compile(r'^/([a-zA-Z])(/|$)')
COMMIT = re.compile(r'^[0-9a-fA-F]{4,40}$')
# cmd.exe re-parses the arguments of code.cmd; refuse paths it could misread as commands.
CMD_UNSAFE = set('&|<>^%!"')


class FileViewError(ValueError):
    """A reference that cannot be shown; the message is meant for the reader."""


def parse_ref(ref):
    """(path, first line, last line) with Windows separators and MSYS drives normalised."""
    text = str(ref or '').strip().strip('`\'"')
    match = REF.match(text)
    if not text or not match:
        raise FileViewError('Empty file reference')
    path = match['path'].replace('\\', '/')
    path = MSYS_DRIVE.sub(lambda m: m[1].upper() + ':/', path)
    start = int(match['start'] or match['lstart'] or 0) or None
    end = int(match['end'] or match['lend'] or 0) or start
    if start and end and end < start:
        start, end = end, start
    return path, start, end


def candidate_roots(sender, states, repositories):
    """Ordered, de-duplicated [(label, Path)]: sender's worktrees, project repositories, other worktrees."""
    roots, seen = [], set()

    def add(label, path):
        if not path:
            return
        resolved = Path(path).resolve()
        key = os.path.normcase(str(resolved))
        if key not in seen and resolved.is_dir():
            seen.add(key)
            roots.append((label, resolved))

    def worktrees(name, state):
        repos = state.get('repositories') or {}
        if len(repos) > 1:
            for key, repo in repos.items():
                add(f'{name} worktree ({key.rsplit("-", 1)[0]})', repo.get('worktree'))
        else:
            add(f'{name} worktree', state.get('worktree'))

    if sender in states:
        worktrees(sender, states[sender])
    for repo in repositories:
        add('project repository' if repo.get('primary') else f'repository {Path(repo["path"]).name}', repo.get('path'))
    for name, state in states.items():
        if name != sender:
            worktrees(name, state)
    return roots


def _git(root, *args, limit=git_preview.OUTPUT_BYTES):
    return git_preview.capture(['git', '-c', 'core.hooksPath=' + os.devnull, '-C', str(root), *args], limit=limit)


def _git_or_none(root, *args):
    try:
        return _git(root, *args).strip()
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return None


def _private(rel):
    """Same boundary as room_runtime.scoped_path: tool folders, Git metadata and .env files."""
    return any(part.lower() in PRIVATE_PARTS or part.lower().startswith('.env.') for part in PurePosixPath(rel).parts)


def _inside(path, root):
    try:
        return path.is_relative_to(root)
    except ValueError:
        return False


def _tracked_root(path):
    """(repository top, relative path) when Git tracks this file in some local repository."""
    if not path.parent.is_dir():
        return None
    top = _git_or_none(path.parent, 'rev-parse', '--show-toplevel')
    if not top:
        return None
    top = Path(top).resolve()
    rel = path.relative_to(top).as_posix() if _inside(path, top) else None
    if rel and _git_or_none(top, 'ls-files', '--error-unmatch', '--', rel) is not None:
        return top, rel
    return None


def resolve(ref, roots, root_index=None):
    """Find the referenced file. Returns a dict whose 'kind' is ok, ambiguous, missing or outside."""
    path, start, end = parse_ref(ref)
    lines = {'start': start, 'end': end}
    if _private(path):
        raise FileViewError('Private tool and credential folders are not shown')
    if ':' in re.sub(r'^[A-Za-z]:', '', path):
        raise FileViewError('Alternate data streams are not shown')
    # Keep each root's position in the full list, so a chosen copy is reopened as the same copy.
    indexed = list(enumerate(roots))
    if root_index is not None:
        if not 0 <= root_index < len(roots):
            raise FileViewError('That copy of the code is no longer available')
        indexed = [indexed[root_index]]
    labels = [label for _, (label, _) in indexed]
    absolute = re.match(r'^[A-Za-z]:/', path) or path.startswith('/')
    if absolute:
        target = Path(path).resolve()
        for index, (label, root) in indexed:
            if _inside(target, root):
                return _found(label, root, target.relative_to(root).as_posix(), index, lines)
        if not target.is_file():
            return {'kind': 'missing', 'path': path, **lines, 'searched': labels, 'directory': target.is_dir()}
        tracked = _tracked_root(target)
        if tracked:
            return _found(f'repository {tracked[0].name}', tracked[0], tracked[1], None, lines)
        return {'kind': 'outside', 'path': str(target), **lines}
    while path.startswith('./'):
        path = path[2:]
    rel = PurePosixPath(path)
    if '..' in rel.parts:
        raise FileViewError('References may not leave the repository')
    if len(rel.parts) > 1:
        for index, (label, root) in indexed:
            target = (root / rel).resolve()
            if _inside(target, root) and target.is_file():
                return _found(label, root, target.relative_to(root).as_posix(), index, lines)
    else:
        # A bare file name: the first copy of the code that has it decides; several matches there are listed.
        for index, (label, root) in indexed:
            listed = _git_or_none(root, 'ls-files', '--cached', '--others', '--exclude-standard', '--',
                                  ':(glob)**/' + rel.name)
            matches = sorted({line for line in (listed or '').splitlines() if line and not _private(line)})
            if len(matches) == 1:
                return _found(label, root, matches[0], index, lines)
            if matches:
                return {'kind': 'ambiguous', 'path': path, **lines, 'root_label': label, 'root_index': index,
                        'candidates': matches[:MAX_CANDIDATES], 'more': max(0, len(matches) - MAX_CANDIDATES)}
    return {'kind': 'missing', 'path': path, **lines, 'searched': labels}


def _found(label, root, rel, index, lines):
    # resolve() follows symlinks and junctions, including parent directories; check where it really lands.
    target = (root / rel).resolve()
    if not _inside(target, root):
        raise FileViewError('That file links outside its repository')
    if _private(rel) or _private(target.relative_to(root).as_posix()):
        raise FileViewError('Private tool and credential folders are not shown')
    return {'kind': 'ok', 'root_label': label, 'root': str(root), 'root_index': index,
            'path': rel, 'absolute': str(target), **lines}


def _text(data):
    if b'\0' in data[:SNIFF_BYTES]:
        return None
    return data.decode('utf-8', errors='replace')


def view(found, message_time=None, at=None):
    """Bounded content plus the Git context a reader needs to trust it."""
    root, rel = Path(found['root']), found['path']
    result = dict(found)
    top = _git_or_none(root, 'rev-parse', '--show-toplevel')
    in_git = bool(top)
    if in_git:
        result['branch'] = _git_or_none(root, 'rev-parse', '--abbrev-ref', 'HEAD')
        result['head'] = _git_or_none(root, 'rev-parse', '--short', 'HEAD')
    if at:
        if not in_git or not COMMIT.match(at):
            raise FileViewError('Unknown revision')
        try:
            data = _git(root, 'show', f'{at}:{rel}', limit=MAX_BYTES + 1).encode('utf-8')
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            raise FileViewError(f'{rel} does not exist at {at}') from exc
        result.update(source='revision', at=at, state='committed')
    else:
        target = Path(found['absolute'])
        if not target.is_file():
            raise FileViewError(f'{rel} no longer exists in the {found["root_label"]}')
        with open(target, 'rb') as handle:
            data = handle.read(MAX_BYTES + 1)
        stat = target.stat()
        result.update(source='working copy', size=stat.st_size, modified=stat.st_mtime,
                      state=_state(root, rel) if in_git else 'not in Git')
        if message_time and stat.st_mtime > float(message_time) + 1:
            result['changed_since_message'] = True
            if in_git:
                # Only an inference: the last commit on the current branch dated before the message. The sender
                # may have meant another branch or uncommitted edits, and commit dates can be rewritten.
                before = _git_or_none(root, 'rev-list', '-1', f'--before=@{int(float(message_time))}', 'HEAD', '--', rel)
                result['committed_before_message'] = (before or '')[:12] or None
    text = _text(data[:MAX_BYTES])
    if text is None:
        result.update(binary=True, text='')
    else:
        result.update(binary=False, text=text, truncated=len(data) > MAX_BYTES)
    return result


def _state(root, rel):
    status = _git_or_none(root, 'status', '--porcelain', '--ignored', '--', rel)
    if status is None:
        return 'unknown'
    code = status[:2]
    if not code.strip():
        return 'clean'
    if code == '??':
        return 'untracked'
    if code == '!!':
        return 'ignored'
    return 'modified'


def open_externally(found, target, line=None):
    """Open a resolved file in VS Code at the line, or reveal it in the file manager."""
    path = Path(found['absolute'])
    if not path.is_file():
        raise FileViewError('The file no longer exists')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    if target == 'editor':
        # Never fall back to the file's default association: for .py, .cmd or .exe that runs the file.
        code = shutil.which('code')
        if not code:
            raise FileViewError('VS Code was not found on this computer; use Show in Explorer')
        if CMD_UNSAFE & set(str(path)):
            raise FileViewError('This path has characters the editor launcher cannot pass safely; use Show in Explorer')
        subprocess.Popen([code, '-g', f'{path}:{int(line or 1)}'], creationflags=flags,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return 'VS Code'
    if target == 'explorer':
        if os.name == 'nt':
            subprocess.Popen(['explorer', '/select,', str(path)])
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', '-R', str(path)])
        else:
            subprocess.Popen(['xdg-open', str(path.parent)])
        return 'file manager'
    raise FileViewError('Choose editor or explorer')
