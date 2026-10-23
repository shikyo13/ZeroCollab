"""Optional external media tool. The app never imports its renderer or encoder."""
import asyncio
import importlib.util
import json
import shutil
import sys


def command(config):
    configured = ((config.get('tools') or {}).get('motion') or {}).get('command')
    if configured is not None:
        if not isinstance(configured, list) or not configured or any(not isinstance(v, str) or not v for v in configured):
            raise ValueError('tools.motion.command must be a nonempty argument list')
        return configured[:]
    executable = shutil.which('zerocollab-motion')
    if executable:
        return [executable]
    if importlib.util.find_spec('zerocollab_motion') is not None:
        return [sys.executable, '-m', 'zerocollab_motion']
    raise ValueError('Recording encoding needs optional zerocollab-motion. Install tools/motion separately or set tools.motion.command.')


async def probe(config):
    argv = command(config)
    proc = await asyncio.create_subprocess_exec(*argv, 'record-probe', stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, **({'creationflags': 0x08000000} if sys.platform == 'win32' else {}))
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), 40)
        if proc.returncode:
            raise ValueError('Motion recording probe failed: ' + stderr.decode(errors='replace')[-400:])
        if not json.loads(stdout).get('available'):
            raise ValueError('The motion tool cannot encode recordings')
        return argv
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


def encode_args(argv, folder, samples, duration_ms, fps):
    request = folder / 'record-request.json'
    request.write_text(json.dumps({'folder': str(folder.resolve()), 'samples': samples,
        'duration_ms': duration_ms, 'fps': fps}), 'utf-8')
    return [*argv, 'record-encode', '--request', str(request)]


def encoded_frames(folder):
    return int(json.loads((folder / 'record-result.json').read_text('utf-8'))['frames'])
