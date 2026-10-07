"""Coordinate JSONL queue producers, consumers and explicit clears across processes."""
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

LOCK_TIMEOUT_SECONDS = 5
LOCK_RETRY_SECONDS = .02


@contextmanager
def _locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the sidecar inode stable: unlinking it would split competing locks.
    with path.with_name(path.name + '.lock').open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b'\0')
                lock.flush()
            def acquire():
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            def release():
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                acquire()
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Queue file lock timed out')
                time.sleep(LOCK_RETRY_SECONDS)
            except OSError:
                if os.name != 'nt' or time.monotonic() >= deadline:
                    raise
                time.sleep(LOCK_RETRY_SECONDS)
        try:
            yield path
        finally:
            release()


def append(path, entry):
    line = json.dumps(entry) + '\n'
    with _locked(path) as path:
        with path.open('a', encoding='utf-8') as stream:
            stream.write(line)


def drain(path):
    with _locked(path) as path:
        if not path.exists():
            return []
        with path.open('r+', encoding='utf-8') as stream:
            lines = stream.readlines()
            stream.seek(0)
            stream.truncate()
        return lines


def clear(path):
    with _locked(path) as path:
        path.write_text('', encoding='utf-8')
