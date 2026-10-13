"""Private, owned release_pack worker; callers never supply paths or commands.

The parent resolves a registered repository and writes input.json beneath its
deployment work root. This worker builds there, not in the public release spool.
Only the parent can publish after checking authority again.
"""
import asyncio
import contextlib
import copy
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

STAGE_SECONDS = 300
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_BYTES = 2 * 1024 ** 3
MAX_FILES = 4096


def worker(directory):
    # Runs only as a child process. Rebinding git here cannot affect the app.
    import release_pack as pack
    from release_agent import review_coverage
    data = json.loads((directory / 'input.json').read_text('utf-8'))
    repo = Path(data['repository'])
    deadline = time.monotonic() + STAGE_SECONDS

    def bounded_git(repository, *args):
        with tempfile.TemporaryFile(dir=directory) as output:
            proc = subprocess.Popen(['git', '-C', str(repository), *args], stdout=output,
                                    stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                while proc.poll() is None:
                    if time.monotonic() >= deadline or os.fstat(output.fileno()).st_size > MAX_FILE_BYTES:
                        raise ValueError('Git exceeded the stage budget')
                    time.sleep(.02)
                if proc.returncode or os.fstat(output.fileno()).st_size > MAX_FILE_BYTES:
                    raise ValueError('Git could not produce bounded release bytes')
                output.seek(0)
                return output.read(MAX_FILE_BYTES + 1)
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
    pack.git = bounded_git
    if pack.git(repo, 'rev-parse', '--verify', data['commit'] + '^{commit}').decode().strip() != data['commit']:
        raise ValueError('The immutable commit did not resolve exactly')
    if data.get('intake_check'):
        if pack.git(repo, 'rev-parse', '--verify', data['base'] + '^{commit}').decode().strip() != data['base']:
            raise ValueError('E4: declared base did not resolve exactly')
        pack.git(repo, 'merge-base', '--is-ancestor', data['base'], data['commit'])
        for dependency in data['dependency_records'].values():
            pack.git(repo, 'merge-base', '--is-ancestor', dependency['commit'], data['commit'])
    entries = pack.tree(repo, data['commit'])
    if len(entries) > MAX_FILES:
        raise ValueError('Release exceeds the file limit')
    total = 0
    for blob in entries.values():
        size = int(pack.git(repo, 'cat-file', '-s', blob).decode())
        total += size
        if size > MAX_FILE_BYTES or total > MAX_BYTES:
            raise ValueError('Release exceeds the byte limit')
    spool = directory / 'spool'
    pack.write_json(spool / 'current.json', data['current'])
    with contextlib.redirect_stdout(io.StringIO()):
        pack.build(SimpleNamespace(repo=repo, spool=spool, commit=data['commit'], base=''))
    packages = list((spool / 'releases').iterdir())
    if len(packages) != 1:
        raise ValueError('Expected one private release package')
    package = packages[0]
    if sum(p.stat().st_size for p in (package / 'payload').rglob('*') if p.is_file()) > MAX_BYTES:
        raise ValueError('Filtered payload exceeds the byte limit')
    coverage = review_coverage(package, Path(data['predecessor']), data['reviews'], data['records'],
                               Path(data['artifacts']), report_uncovered=data.get('intake_check', False))
    pack.write_json(directory / 'result.json', {'release_id': package.name, **coverage})


async def join(task):
    """Cleanup stays owned through repeated parent cancellation."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def run_worker(directory, check, *, helper=None, env=None, seconds=STAGE_SECONDS):
    # helper/env are fixed by the host action, never supplied by a room caller.
    argv = [sys.executable, '-I', '-B', str(helper or Path(__file__).resolve()), str(directory)]
    proc = None
    deadline = time.monotonic() + seconds
    try:
        check()
        if os.name == 'nt':
            from shared_browser import WindowsBrowserProcess
            # Staging preserves the existing Git filter/EOL environment. The
            # rehearsal launcher will use an explicit isolated environment.
            proc = WindowsBrowserProcess(argv, priority_class=subprocess.BELOW_NORMAL_PRIORITY_CLASS,
                                         cwd=str(directory), env=env)
        else:
            spawning = asyncio.create_task(asyncio.create_subprocess_exec(
                *argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True, cwd=directory, env=env))
            try:
                proc = await asyncio.shield(spawning)
            except BaseException:
                proc = await join(spawning)
                raise
        while proc.returncode is None:
            check()
            if time.monotonic() >= deadline:
                raise TimeoutError('Staging exceeded its time budget')
            await asyncio.sleep(.05)
        check()
        if proc.returncode != 0:
            raise ValueError('Private release build or review coverage failed')
    finally:
        if proc is not None:
            async def cleanup():
                try:
                    if os.name == 'nt':
                        if proc.returncode is None:
                            proc.kill()
                    else:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(proc.pid, signal.SIGKILL)
                    await proc.wait()
                finally:
                    if os.name == 'nt':
                        proc.close_handles()
            await join(asyncio.create_task(cleanup()))


def prepare(manager, caller, values):
    from project_registry import repositories
    repo = next((r for r in repositories(manager.runtime) if r['key'] == values['repository']), None)
    if repo is None:
        raise ValueError('Choose a registered project repository')
    grant = manager.state.release_grant.require(caller, manager.runtime.profiles.items.get('astra'))
    current = manager._record('current.json')
    if not current or not manager.ID.fullmatch(current.get('id', '')):
        raise ValueError('An installed predecessor is required')
    records = {r['id']: copy.deepcopy(r) for r in manager.runtime.project_room.state.get('reviews', [])
               if r.get('id') in values['reviews']}
    if any(records.get(i, {}).get('status') != 'approved' for i in values['reviews']):
        raise ValueError('Every selected review must be approved')
    return {'repository': str(Path(repo['path']).resolve()), 'repository_key': repo['key'],
            'commit': values['commit'], 'reviews': values['reviews'], 'records': records,
            'current': current, 'predecessor': str(manager._path('releases/' + current['id'])),
            'artifacts': str(manager.runtime.project_room.path.parent / 'review-artifacts'),
            'caller': copy.deepcopy(caller), 'grant_revision': grant['revision']}


def revalidate(manager, context):
    rt, state = manager.runtime, manager.state
    if manager.closing or context['caller']['project'] != rt.identity or not state.admission_open():
        raise ValueError('Room changed or closed during staging')
    if rt.paused or rt.manual_stop is None or rt.manual_stop_pending or rt.manual_stop.snapshot()['stopped']:
        raise ValueError('The room is stopped or manual control is unavailable')
    grant = state.release_grant.require(context['caller'], rt.profiles.items.get('astra'))
    if grant['revision'] != context['grant_revision'] or manager._record('current.json') != context['current']:
        raise ValueError('Grant or installed predecessor changed during staging')
    if manager._path('journal.json').exists() or any(manager._path('requests').glob('*.json')):
        raise ValueError('A deployment is queued or in progress')
    records = {r.get('id'): r for r in rt.project_room.state.get('reviews', [])}
    if any(records.get(i) != context['records'].get(i) for i in context['reviews']):
        raise ValueError('Review evidence changed during staging')


async def stage(manager, job, context):
    from room_deploy import approval_lock, atomic_json
    directory = manager._path('work/' + job['id'])
    directory.mkdir(parents=True, exist_ok=False)
    try:
        atomic_json(directory / 'input.json', context)
        def check():
            if job['stop'].is_set():
                raise ValueError('Staging cancelled')
            revalidate(manager, context)
        check()
        await run_worker(directory, check)
        result = manager._record('work/' + job['id'] + '/result.json')
        identifier = result.get('release_id', '') if result else ''
        if not manager.ID.fullmatch(identifier):
            raise ValueError('Stage worker produced no valid result')
        package = directory / 'spool' / 'releases' / identifier
        release = manager._record(f'work/{job["id"]}/spool/releases/{identifier}/RELEASE.json')
        if not release or release.get('manifest_sha') != result.get('manifest_sha') or result.get('predecessor_manifest_sha') != context['current']['manifest_sha']:
            raise ValueError('Stage worker fingerprint differs from its predecessor')
        with manager.state.lock, approval_lock(manager.root):
            check()
            from release_intake import stage_snapshot
            dispositions = stage_snapshot(manager, context, result)
            receipt = {**result, 'repository': context['repository_key'], 'commit': context['commit'],
                       'reviews': context['reviews'], 'profile': context['caller']['profile'],
                       'project': context['caller']['project'], 'run': context['caller']['run'],
                       'grant_revision': context['grant_revision'], 'created': time.time(),
                       'handoff_dispositions': dispositions}
            atomic_json(package / 'stage.json', receipt)
            atomic_json(package / 'review.json', {'release_id': identifier, 'manifest_sha': result['manifest_sha'],
                                                   'reviews': context['reviews']})
            destination = manager._path('releases/' + identifier)
            if destination.exists():
                # Do not overwrite an existing package or its review/receipt.
                existing = manager._record('releases/' + identifier + '/stage.json')
                fixed = ('release_id', 'manifest_sha', 'predecessor_manifest_sha', 'coverage',
                         'repository', 'commit', 'reviews', 'project')
                if not existing or any(existing.get(key) != receipt.get(key) for key in fixed):
                    raise ValueError('Package already exists; inspect its recorded staging identity')
                # An existing package cannot acquire new inclusion claims.
                def handoff_identity(rows):
                    return [(r['id'], r['check_id'], r['state'], r.get('reason'), r.get('target')) for r in rows]
                if handoff_identity(existing.get('handoff_dispositions', [])) != handoff_identity(dispositions):
                    raise ValueError('E4: existing stage has a different handoff set; use a new reviewed commit')
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(package, destination)
        return {'release_id': identifier, 'manifest_sha': result['manifest_sha'], 'commit': context['commit'],
                'changed_files': len(result['coverage']), 'review_ids': context['reviews'],
                'rehearsal': 'required', 'owner_acceptance': 'not_evaluated'}
    finally:
        # Only this generated job directory is disposable; never follow a path
        # outside the deployment work root, including a substituted symlink.
        target, work = directory.resolve(), manager._path('work')
        if target.parent != work or target.name != job['id']:
            raise ValueError('Refusing cleanup outside the owned stage directory')
        shutil.rmtree(target)


if __name__ == '__main__':
    # -I excludes cwd and user site; only the host-owned helper directory is added.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    worker(Path(sys.argv[1]).resolve())
