"""The media tool owns recording transport probes and encoding, never live capture."""
import asyncio
import json
import math
import os
from pathlib import Path
import re
import subprocess
from . import studio, recording_transport as transport
from .browser import WindowsBrowserProcess


async def encode_take(request, workspace, *, check_cancel=lambda: None):
    folder = Path(request['folder']).resolve(strict=True)
    samples, fps, duration = request['samples'], request['fps'], request['duration_ms']
    if type(fps) is not int or fps not in (30, 60) or not isinstance(duration, (int, float)) or not 0 < duration <= 120000:
        raise ValueError('Recording requires 30/60 fps and a duration up to 120 seconds')
    if not samples or len(samples) > 30000:
        raise ValueError('Recording sample count is invalid')
    for at, name in samples:
        if not isinstance(at, (int, float)) or not math.isfinite(at) or not re.fullmatch(r'[0-9]{8}\.jpg', name):
            raise ValueError('Invalid recording timestamp or spool filename')
        if not (folder / name).is_file():
            raise ValueError('Recording sample is missing')
    binary = studio.ffmpeg_path(workspace)
    await asyncio.to_thread(transport.probe_transport, binary)
    check_cancel()
    await asyncio.to_thread(transport.concat_file, folder, samples, duration)
    frames = max(1, math.ceil(duration * fps / 1000))
    destination = folder / 'clip.partial.webm'
    args = transport.encode_args(binary, folder, fps, frames, destination)
    proc = WindowsBrowserProcess(args, priority_class=subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name == 'nt' else await asyncio.create_subprocess_exec(*args)
    try:
        waiter = asyncio.create_task(proc.wait())
        while not waiter.done():
            check_cancel()
            await asyncio.wait({waiter}, timeout=.1)
        code = await waiter
        check_cancel()
        actual = transport.encoded_frames(folder)
        if code or actual != frames or not destination.is_file():
            raise ValueError(f'Recording encode failed ({code}; {actual}/{frames} frames)')
        result = {'path': str(destination), 'frames': actual, 'bytes': destination.stat().st_size}
        (folder / 'record-result.json').write_text(json.dumps(result), 'utf-8')
        return result
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        if hasattr(proc, 'close_handles'):
            proc.close_handles()
