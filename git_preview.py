"""Bounded retained output for read-only Git previews (called off the event loop)."""
import os
import signal
import time
import subprocess
import threading

OUTPUT_BYTES = 96000  # Enough UTF-8 bytes for the existing 24,000-character preview.
ERROR_BYTES = 4096
READ_BYTES = 8192
TIMEOUT_SECONDS = 15
CLEANUP_SECONDS = 2


def capture(command, *, env=None, limit=OUTPUT_BYTES, timeout=TIMEOUT_SECONDS):
    """Drain both pipes concurrently, retaining only prefixes; never buffer full output."""
    output, errors = bytearray(), bytearray()
    read_errors = []

    def drain(stream, target, cap):
        try:
            with stream:
                while chunk := stream.read(READ_BYTES):
                    remaining = max(0, cap - len(target))
                    target.extend(chunk[:remaining])
        except OSError as exc:
            read_errors.append(exc)

    # Readers own/close their pipes. A Popen context manager could block on a
    # stream lock at exit if an escaped descendant still holds the write handle.
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=env, start_new_session=os.name != 'nt',
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    readers = [threading.Thread(target=drain, args=(process.stdout, output, limit), daemon=True),
               threading.Thread(target=drain, args=(process.stderr, errors, ERROR_BYTES), daemon=True)]
    deadline = time.monotonic() + timeout
    for reader in readers:
        reader.start()
    try:
        process.wait(timeout=max(0, deadline - time.monotonic()))
        for reader in readers:
            reader.join(max(0, deadline - time.monotonic()))
        if any(reader.is_alive() for reader in readers):
            raise subprocess.TimeoutExpired(command, timeout)
    except BaseException:
        _terminate_tree(process)
        raise
    finally:
        cleanup_deadline = time.monotonic() + CLEANUP_SECONDS
        for reader in readers:
            reader.join(max(0, cleanup_deadline - time.monotonic()))
    if read_errors:
        raise read_errors[0]
    if process.returncode:
        raise RuntimeError(errors.decode('utf-8', errors='replace').strip() or 'Git command failed')
    return output.decode('utf-8', errors='replace')


def _terminate_tree(process):
    if os.name == 'nt':
        try:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=CLEANUP_SECONDS, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=CLEANUP_SECONDS)
    except subprocess.TimeoutExpired:
        pass  # Reader joins are also bounded; never turn cleanup into an unlimited wait.


def preview(path, *args, env=None):
    return capture(['git', '-c', 'core.hooksPath=' + os.devnull,
                    '-C', str(path), *args], env=env)
