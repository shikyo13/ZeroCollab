"""Files referenced in place by their own path (Zero #14949: no copies). A pasted local path, a UNC path or a
file:// URI resolves to an existing regular file that the room only reads; nothing is copied into uploads/.

Used by the read-only /api/files/local route (app.py) and by the agent's "File: ... is at ..." line
(project_room), so both accept exactly the same references.
"""
import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

from zerocollab.storage.durable import atomic_write

LOGGER = logging.getLogger(__name__)
MAX_REFERENCE = 4096
MAX_REGISTERED = 5000  # oldest references are forgotten past this; their files are never touched
ID_HEX = 16
REFERENCE_ID = re.compile(rf'^[0-9a-f]{{{ID_HEX}}}$')
# \\.\ and \\?\ (and their forward-slash forms) reach devices and raw volumes, never ordinary files.
DEVICE_PREFIX = re.compile(r'^[\\/]{2}[.?][\\/]')
DRIVE_URI_PATH = re.compile(r'^/[A-Za-z]:')
# Windows reserves these names in every folder; opening one reads a device, not a file.
RESERVED_NAMES = frozenset({'CON', 'PRN', 'AUX', 'NUL', *(f'COM{n}' for n in range(1, 10)),
                            *(f'LPT{n}' for n in range(1, 10))})


def as_path(reference):
    """A pasted reference as an absolute path: "C:\\x\\y.mp4" (quotes from Explorer's Copy as path are
    removed), C:/x/y.mp4, \\\\server\\share\\y.mp4, file:///C:/x/y.mp4 or file://server/share/y.mp4."""
    if not isinstance(reference, str) or not reference.strip() or len(reference) > MAX_REFERENCE:
        raise ValueError(f'A file reference is a path or file:// URI of at most {MAX_REFERENCE} characters')
    text = reference.strip().strip('"')
    if text.lower().startswith('file:'):
        parts = urlsplit(text)
        path = unquote(parts.path)
        if parts.netloc and parts.netloc.lower() != 'localhost':
            text = '\\\\' + parts.netloc + path.replace('/', '\\')  # file://server/share/x is a UNC path
        else:
            text = path[1:] if DRIVE_URI_PATH.match(path) else path  # file:///C:/x is C:/x
    if DEVICE_PREFIX.match(text):
        raise ValueError('Device paths (\\\\.\\ and \\\\?\\) are not files')
    path = Path(text)
    if not path.is_absolute():
        raise ValueError('A file reference must be a full path, such as C:\\Users\\you\\file.mp4')
    if path.name.split('.')[0].upper() in RESERVED_NAMES:
        raise ValueError(f'{path.name} is a reserved device name, not a file')
    return path


def resolve(reference):
    """The existing regular file a reference names (symlinks followed), or ValueError. Blocking: it touches
    the disk and possibly a network share, so callers on the event loop run it in a thread."""
    path = as_path(reference)
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        raise ValueError(f'No file at {path}') from None
    if not resolved.is_file():
        raise ValueError(f'{path} is not a regular file (folders and devices are refused)')
    return resolved


REGISTRY_NAME = 'file_references.json'  # in the server's data_dir
_shared = {}
_shared_lock = threading.Lock()


def shared_registry(cfg):
    """The one Registry per registry file in this process, from a room config ({server: {data_dir}}). Every
    writer (pasted references, finished browser recordings) goes through it, so no save overwrites another's."""
    path = Path(((cfg or {}).get('server') or {}).get('data_dir', './data')) / REGISTRY_NAME
    key = os.path.normcase(str(path.resolve()))
    with _shared_lock:
        if key not in _shared:
            _shared[key] = Registry(path)
        return _shared[key]


class Registry:
    """Pasted references by a stable id, so links carry an id, not a path, and the server keeps the original
    path. Persisted as JSON in the room's data folder. Methods block on disk; call them off the event loop."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.entries = self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text('utf-8'))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            LOGGER.warning('Ignoring unreadable file reference registry %s', self.path, exc_info=True)
            return {}
        return {key: entry for key, entry in data.items() if REFERENCE_ID.match(key) and isinstance(entry, dict)
                and isinstance(entry.get('path'), str)} if isinstance(data, dict) else {}

    def _save(self):
        content = json.dumps(self.entries, indent=1, ensure_ascii=False)
        atomic_write(self.path, lambda stream: stream.write(content))

    def register(self, reference):
        """Validate a pasted reference and remember it: (id, resolved path, stat). The same file always gets
        the same id (case-insensitive on Windows), so pasting it again adds nothing."""
        path = resolve(reference)
        key = hashlib.sha256(os.path.normcase(str(path)).encode('utf-8')).hexdigest()[:ID_HEX]
        info = path.stat()
        with self.lock:
            known = self.entries.get(key)
            if known and (known.get('path'), known.get('size'), known.get('mtime_ns')) == (
                    str(path), info.st_size, info.st_mtime_ns):
                return key, path, info  # unchanged: a repeat paste or status poll writes nothing
            self.entries[key] = {'path': str(path), 'size': info.st_size, 'mtime_ns': info.st_mtime_ns,
                                 'registered': time.time()}
            for stale in sorted(self.entries, key=lambda k: self.entries[k].get('registered', 0))[
                    :max(0, len(self.entries) - MAX_REGISTERED)]:
                del self.entries[stale]
            self._save()
        return key, path, info

    def lookup(self, key):
        """(path, changed) for a registered id; changed means its size or modified time differ from when it was
        pasted. ValueError when the id is unknown or the original is gone (nothing is ever re-copied)."""
        entry = self.entries.get(key) if isinstance(key, str) and REFERENCE_ID.match(key) else None
        if entry is None:
            raise ValueError('Unknown file reference; paste the path again')
        path = Path(entry['path'])
        try:
            info = path.stat()
        except OSError:
            raise ValueError(f'The original file is gone: {path}') from None
        if not path.is_file():
            raise ValueError(f'The original is no longer a regular file: {path}')
        return path, (info.st_size, info.st_mtime_ns) != (entry.get('size'), entry.get('mtime_ns'))
