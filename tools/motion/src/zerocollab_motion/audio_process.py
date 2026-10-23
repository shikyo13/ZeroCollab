"""Owned, cancellable audio subprocesses; reuse the desktop Windows job authority."""
import asyncio
import logging
import os
import signal
import subprocess
import time

POLL_SECONDS = 0.05
STOP_SECONDS = 5
PREPARE_TIMEOUT_SECONDS = 180
LOG = logging.getLogger(__name__)


async def _join(task, *, propagate=False):
    """Join cleanup despite repeat cancellation, then let the caller re-raise."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
            continue
    result = task.result()
    if cancelled and propagate:
        raise asyncio.CancelledError()
    return result


async def _stop(proc):
    try:
        if proc.returncode is None:
            if os.name == 'nt':
                proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        await asyncio.wait_for(proc.wait(), STOP_SECONDS)
    finally:
        if os.name == 'nt':
            proc.close_handles()


async def run_owned(argv, check_cancel=lambda: None, timeout=PREPARE_TIMEOUT_SECONDS):
    """Only trusted internal argv, never an author-provided command line."""
    proc = None
    deadline = time.monotonic() + timeout
    try:
        check_cancel()
        if os.name == 'nt':
            from zerocollab_motion.browser import WindowsBrowserProcess
            # Assignment to the kill-on-close job happens before the process runs.
            proc = WindowsBrowserProcess(argv, priority_class=subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            spawning = asyncio.create_task(asyncio.create_subprocess_exec(
                *argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
            try:
                proc = await asyncio.shield(spawning)
            except BaseException:
                proc = await _join(spawning)
                raise
        LOG.info('Started owned audio worker pid=%s', proc.pid)
        while proc.returncode is None:
            check_cancel()
            if time.monotonic() >= deadline:
                raise TimeoutError('Audio preparation exceeded its time budget')
            await asyncio.sleep(POLL_SECONDS)
        check_cancel()
        return proc.returncode
    finally:
        if proc is not None:
            await _join(asyncio.create_task(_stop(proc)), propagate=True)
            LOG.info('Closed owned audio worker pid=%s', proc.pid)
