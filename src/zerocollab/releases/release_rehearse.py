import sys as _bootstrap_sys
from pathlib import Path as _BootstrapPath
if __package__ in ('', None):
    _bootstrap_sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[2]))

"""Fixed host rehearsal: exact package, empty room, private homes and loopback.

This is process isolation for reviewed same-user code, not an OS sandbox.
No caller paths, commands, environment, acceptance or success flags are accepted.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

REHEARSE_SECONDS = 120


def environment(directory):
    # Read only these OS bootstrap values; never enumerate/copy secret values.
    env = {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE') if key in os.environ}
    home, temp = directory / 'home', directory / 'temp'
    home.mkdir(exist_ok=True)
    temp.mkdir(exist_ok=True)
    for name in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'XDG_CONFIG_HOME',
                 'XDG_CACHE_HOME', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR'):
        env[name] = str(home / name.lower())
        Path(env[name]).mkdir(exist_ok=True)
    env.update(TEMP=str(temp), TMP=str(temp), ZC_REHEARSAL='1', PYTHONUTF8='1',
               PYTHONDONTWRITEBYTECODE='1', PATH=str(Path(sys.executable).parent))
    return env


def prepare(manager, caller, values):
    from zerocollab.releases.release_interface import arguments
    from zerocollab.releases.release_stage import prepare as stage_prepare
    prefix = 'releases/' + values['release_id']
    receipt = manager._record(prefix + '/stage.json')
    if not receipt or (receipt.get('release_id'), receipt.get('manifest_sha'), receipt.get('project')) != (
            values['release_id'], values['manifest_sha'], caller['project']):
        raise ValueError('An exact stage receipt for this project is required')
    stage_values = arguments('stage', {'repository': receipt.get('repository'), 'commit': receipt.get('commit'),
                                      'reviews': receipt.get('reviews')})
    context = stage_prepare(manager, caller, stage_values)
    if receipt.get('predecessor_manifest_sha') != context['current'].get('manifest_sha'):
        raise ValueError('Installed predecessor changed; stage the candidate again')
    context.update(release_id=values['release_id'], manifest_sha=values['manifest_sha'],
                   package=str(manager._path(prefix)), stage_receipt=receipt)
    revalidate(manager, context)
    return context


def revalidate(manager, context):
    from zerocollab.releases.release_stage import revalidate as stage_revalidate
    stage_revalidate(manager, context)
    if manager._record('releases/' + context['release_id'] + '/stage.json') != context['stage_receipt']:
        raise ValueError('Staging evidence changed during rehearsal')


def worker(directory):
    """Child is already in the parent's owned job/process group and safe env."""
    import httpx
    from zerocollab.releases.release_agent import review_coverage
    from zerocollab.releases.release_pack import check_release, materialize, write_json
    context = json.loads((directory / 'input.json').read_text('utf-8'))
    package = Path(context['package'])
    # Recheck provenance before executing any candidate source.
    coverage = review_coverage(package, Path(context['predecessor']), context['reviews'], context['records'],
                               Path(context['artifacts']))
    if (coverage['manifest_sha'], coverage['predecessor_manifest_sha'], coverage['coverage']) != (
            context['manifest_sha'], context['current']['manifest_sha'], context['stage_receipt']['coverage']):
        raise ValueError('Rehearsal package differs from staging')
    manifest, release = check_release(package)
    candidate = directory / 'candidate'
    with contextlib.redirect_stdout(io.StringIO()):
        materialize(SimpleNamespace(spool=package.parent.parent, release_id=package.name,
                                    target=candidate, source=None))
    # This host-owned module is deliberately excluded from application packages,
    # but ReleaseManager imports it on startup. Supply the same trusted helper
    # that performed materialization, without copying host config or data.
    import zerocollab.releases.release_pack as release_pack
    helper = Path(release_pack.__file__).resolve().read_bytes()
    (candidate / 'release_pack.py').write_bytes(helper)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    if port in (8300, 8200, 8201):
        raise ValueError('Refusing a live room port')
    (candidate / 'config.toml').write_text(
        f'[server]\nhost="127.0.0.1"\nport={port}\ndata_dir="./data"\n'
        '[images]\nupload_dir="./uploads"\n[agents]\n', encoding='utf-8')
    write_json(candidate / 'data' / 'deploy' / 'current.json', release)
    # Direct run.py starts no MCP listeners under ZC_REHEARSAL; no host supervisor
    # or live configuration is copied or invoked. Absolute Python, no shell.
    proc = subprocess.Popen([sys.executable, '-I', '-B', str(candidate / 'run.py')],
                            cwd=candidate, env=environment(directory), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    deadline = time.monotonic() + 60
    served = 0
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=2,
                          follow_redirects=False) as client:
            while True:
                if proc.poll() is not None or time.monotonic() >= deadline:
                    raise ValueError('Candidate did not start within the rehearsal budget')
                try:
                    response = client.get('/static/modern/workspace.js')
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            for item in manifest['files']:
                if hashlib.sha256((candidate / item['path']).read_bytes()).hexdigest() != item['sha256']:
                    raise ValueError('Materialized bytes changed during startup')
                static_prefix = 'apps/desktop/renderer/static/'
                if item['path'].startswith(('static/', static_prefix)):
                    # Stream/bound responses so a faulty server cannot exhaust the worker.
                    digest, size = hashlib.sha256(), 0
                    asset = ('static/' + item['path'][len(static_prefix):]
                             if item['path'].startswith(static_prefix) else item['path'])
                    with client.stream('GET', '/' + asset) as response:
                        response.raise_for_status()
                        for data in response.iter_bytes():
                            size += len(data)
                            if size > (candidate / item['path']).stat().st_size:
                                raise ValueError('Served asset exceeded its expected size')
                            digest.update(data)
                    if digest.hexdigest() != item['sha256']:
                        raise ValueError('Served asset differs from the package')
                    served += 1
            if not served or client.get('/api/version', headers={'Host': 'rebind.invalid'}).status_code != 400:
                raise ValueError('Candidate host restriction failed')
            if client.get('/api/version').status_code != 403:
                raise ValueError('Candidate unauthenticated API restriction failed')
        # Package tampering while the candidate ran also invalidates this proof.
        check_release(package)
        if (candidate / 'release_pack.py').read_bytes() != helper:
            raise ValueError('Host packaging helper changed during rehearsal')
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
    write_json(directory / 'result.json', {'release_id': package.name, 'manifest_sha': context['manifest_sha'],
        'status': 'passed', 'files_verified': len(manifest['files']), 'served_assets_verified': served,
        'foreign_host_status': 400, 'unauthenticated_api_status': 403, 'real_provider_turns': 0,
        'owner_acceptance': 'not_evaluated'})


async def rehearse(manager, job, context):
    from zerocollab.releases.release_stage import run_worker
    from zerocollab.releases.room_deploy import approval_lock, atomic_json
    directory = manager._path('work/' + job['id'])
    directory.mkdir(parents=True, exist_ok=False)
    try:
        atomic_json(directory / 'input.json', context)
        def check():
            if job['stop'].is_set():
                raise ValueError('Rehearsal cancelled')
            revalidate(manager, context)
        check()
        await run_worker(directory, check, helper=Path(__file__).resolve(), env=environment(directory),
                         seconds=REHEARSE_SECONDS)
        result = manager._record('work/' + job['id'] + '/result.json')
        if not result or (result.get('release_id'), result.get('manifest_sha'), result.get('status')) != (
                context['release_id'], context['manifest_sha'], 'passed'):
            raise ValueError('Rehearsal did not return the expected package identity')
        # The owned process tree has been torn down before recording success.
        with manager.state.lock, approval_lock(manager.root):
            check()
            atomic_json(manager._path('releases/' + context['release_id'] + '/rehearsal.json'),
                        {**result, 'job_id': job['id'], 'created': time.time(),
                         'predecessor_manifest_sha': context['current']['manifest_sha'],
                         'grant_revision': context['grant_revision'], 'project': context['caller']['project']})
        return result
    finally:
        target, work = directory.resolve(), manager._path('work')
        if target.parent != work or target.name != job['id']:
            raise ValueError('Refusing cleanup outside the owned rehearsal directory')
        shutil.rmtree(target)


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    worker(Path(sys.argv[1]).resolve())
