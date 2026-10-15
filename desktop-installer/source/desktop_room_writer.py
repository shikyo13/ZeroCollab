"""Windows writer ownership shared by direct rooms and their supervisor.

This is launcher coordination, not a security boundary against arbitrary code.
Acquire before configuring stores. Keep the lease until runtime shutdown ends.
"""

import ctypes
from ctypes import wintypes as wt
import os
import json
import math
from pathlib import Path
import subprocess
import time


LOCK_NAME = ".room-writer.lock"
_INVALID = ctypes.c_void_p(-1).value
_k = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None
if _k is not None:
    def _bind(name, args, result):
        function = getattr(_k, name)
        function.argtypes, function.restype = args, result
        return function

    _create = _bind("CreateFileW", [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID,
                                   wt.DWORD, wt.DWORD, wt.HANDLE], wt.HANDLE)
    _close = _bind("CloseHandle", [wt.HANDLE], wt.BOOL)
    _flags = _bind("GetHandleInformation", [wt.HANDLE, ctypes.POINTER(wt.DWORD)], wt.BOOL)
    _set_flags = _bind("SetHandleInformation", [wt.HANDLE, wt.DWORD, wt.DWORD], wt.BOOL)
    _path = _bind("GetFinalPathNameByHandleW", [wt.HANDLE, wt.LPWSTR, wt.DWORD, wt.DWORD], wt.DWORD)
    _file_type = _bind("GetFileType", [wt.HANDLE], wt.DWORD)
    _current = _bind("GetCurrentProcess", [], wt.HANDLE)
    _duplicate = _bind("DuplicateHandle", [wt.HANDLE, wt.HANDLE, wt.HANDLE,
                                          ctypes.POINTER(wt.HANDLE), wt.DWORD, wt.BOOL, wt.DWORD], wt.BOOL)
    _wait = _bind("WaitForSingleObject", [wt.HANDLE, wt.DWORD], wt.DWORD)
    _pid = _bind("GetProcessId", [wt.HANDLE], wt.DWORD)


class RoomInUse(RuntimeError):
    """Terminal startup refusal, never a deploy failure or retry trigger."""

    def __init__(self, message, owner=None):
        super().__init__(message)
        self.owner = owner


def _windows():
    if _k is None:
        raise RuntimeError("Windows room writer ownership is required")


def _directory(data_dir):
    directory = Path(data_dir)
    if not directory.is_absolute():
        raise ValueError("An absolute configured room data directory is required")
    return directory.resolve()


def _kind(kind):
    if kind not in ("web", "desktop"):
        raise ValueError("Room writer kind must be web or desktop")
    return kind


def _summary(record):
    if not isinstance(record, dict) or record.get("kind") not in ("web", "desktop"):
        raise ValueError("Invalid room writer owner record; startup refused")
    pid, started = record.get("pid"), record.get("started")
    if type(pid) is not int or pid <= 0 or type(started) not in (int, float) or not math.isfinite(started):
        raise ValueError("Invalid room writer owner record; startup refused")
    return {"kind": record["kind"], "pid": pid, "started": started}


def _file(handle):
    """Temporary non-inheritable duplicate for bounded metadata IO."""
    if os.name == "nt":
        import msvcrt
        copy = wt.HANDLE()
        if not _duplicate(_current(), handle, _current(), ctypes.byref(copy), 0, False, 2):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            fd = msvcrt.open_osfhandle(copy.value, os.O_BINARY | os.O_RDWR)
        except BaseException:
            _close(copy)
            raise
    else:
        fd = os.dup(handle)
    try:
        os.set_inheritable(fd, False)
        return os.fdopen(fd, "r+b", buffering=0)
    except BaseException:
        os.close(fd)
        raise


def _read_record(handle):
    with _file(handle) as file:
        file.seek(0)
        raw = file.read(4097)
    if not raw:
        return None
    if len(raw) > 4096:
        raise ValueError("Room writer owner record is oversized; startup refused")
    record = json.loads(raw)
    _summary(record)
    if record.get("previous") is not None:
        _summary(record["previous"])
    return record


def _write_record(handle, record):
    raw = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
    with _file(handle) as file:
        file.seek(0)
        if file.write(raw) != len(raw):
            raise OSError("Incomplete room writer owner record")
        file.truncate()
        os.fsync(file.fileno())


def _diagnostic_owner(directory):
    """Best-effort telemetry on refusal; never decides whether a lease is free."""
    handle = None
    try:
        if os.name == "nt":
            handle = _create(str(directory / LOCK_NAME), 0x80000000, 3, None, 3, 0x80, None)
            if handle == _INVALID:
                handle = None
                return None
        else:
            handle = os.open(directory / LOCK_NAME, os.O_RDONLY)
        record = _read_record(handle)
        return _summary(record) if record else None
    except (OSError, ValueError):
        return None
    finally:
        if handle is not None:
            _close(handle) if os.name == "nt" else os.close(handle)


def _checked_handle(handle, directory):
    """Validate only the lock handle; never read room records or credentials."""
    flags = wt.DWORD()
    if not _flags(handle, ctypes.byref(flags)):
        raise ctypes.WinError(ctypes.get_last_error())
    if flags.value & 1:
        raise ValueError("Room writer handle must not be inheritable")
    if _file_type(handle) != 1:  # FILE_TYPE_DISK
        raise ValueError("Room writer handle must refer to the lock file")
    required = _path(handle, None, 0, 0)
    if not required:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(required + 1)
    length = _path(handle, buffer, len(buffer), 0)
    if not length or length >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    final = buffer.value
    if final.startswith("\\\\?\\UNC\\"):
        final = "\\\\" + final[8:]
    elif final.startswith("\\\\?\\"):
        final = final[4:]
    if Path(final) != directory / LOCK_NAME:
        raise ValueError("Room writer handle belongs to a different data directory")


class RoomWriter:
    """An owned, non-inheritable file handle with optional child delegation."""

    def __init__(self, directory, handle, record, *, supervisor=False):
        self.directory = directory
        self.handle = handle
        self.owner = _summary(record)
        self.previous = record.get("previous")
        self._supervisor = supervisor
        self._child_process = None

    def _child_finished(self):
        if self._child_process is None:
            return True
        result = _wait(self._child_process, 0)
        if result == 258:  # WAIT_TIMEOUT: OS process is still alive
            return False
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        _close(self._child_process)
        self._child_process = None
        return True

    def duplicate_for_child(self, child: subprocess.Popen) -> int:
        """Transfer through the launcher's private stdin, before child writes.

        Uses Popen's existing Windows process handle, not PID discovery. Only
        one living child may share the supervisor reservation. The caller must
        end and wait for its child if bootstrap/acknowledgement fails.
        """
        _windows()
        if self.handle is None or not self._supervisor:
            raise ValueError("Only an open supervisor reservation may delegate")
        if not self._child_finished():
            raise RoomInUse("The previous supervised room has not exited")
        if not isinstance(child, subprocess.Popen) or child.pid == os.getpid():
            raise ValueError("A newly spawned supervised child is required")
        target = getattr(child, "_handle", None)
        if target is None or _pid(int(target)) != child.pid or child.poll() is not None:
            raise ValueError("The supervised child is unavailable")
        process = wt.HANDLE()
        if not _duplicate(_current(), int(target), _current(), ctypes.byref(process), 0, False, 2):
            raise ctypes.WinError(ctypes.get_last_error())
        remote = wt.HANDLE()
        if not _duplicate(_current(), self.handle, process, ctypes.byref(remote), 0, False, 2):
            error = ctypes.get_last_error()
            _close(process)
            raise ctypes.WinError(error)
        self._child_process = process.value
        return remote.value

    def close(self):
        if self.handle is not None:
            if not self._child_finished():
                raise RoomInUse("Stop and wait for the supervised room before releasing its data")
            if os.name != "nt":
                os.close(self.handle)
            elif not _close(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def open_room_writer(data_dir: Path, kind="web", *, supervisor=False) -> RoomWriter:
    """Own the chosen directory immediately, or raise RoomInUse.

    READ|WRITE access and FILE_SHARE_READ deny all other writers/deletion of
    the stable lock file. DuplicateHandle preserves that same reservation until
    its final handle closes. Only this directory and lock file may be created.
    """
    directory = _directory(data_dir)
    _kind(kind)
    if supervisor and (os.name != "nt" or kind not in ("web", "desktop")):
        raise ValueError("Supervisor reservation requires the Windows web launcher")
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        handle = _create(str(directory / LOCK_NAME), 0xC0000000, 1, None, 4, 0x80, None)
        if handle == _INVALID:
            error = ctypes.get_last_error()
            if error in (32, 33):
                raise RoomInUse("This room's data is in use. Stop its current web or desktop owner first.",
                                _diagnostic_owner(directory))
            raise ctypes.WinError(error)
    else:
        import fcntl
        handle = os.open(directory / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.set_inheritable(handle, False)
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException as error:
            os.close(handle)
            if isinstance(error, BlockingIOError):
                raise RoomInUse("This room's data is in use. Stop its current owner first.",
                                _diagnostic_owner(directory)) from error
            raise
    try:
        if os.name == "nt":
            if not _set_flags(handle, 1, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            _checked_handle(handle, directory)
        previous = _read_record(handle)
        record = {"kind": kind, "pid": os.getpid(), "started": time.time(),
                  "previous": _summary(previous) if previous else None}
        _write_record(handle, record)
        return RoomWriter(directory, handle, record, supervisor=supervisor)
    except BaseException:
        _close(handle) if os.name == "nt" else os.close(handle)
        raise


def adopt_room_writer(data_dir: Path, handle: int, kind="web") -> RoomWriter:
    """Take ownership of a non-inherited duplicate sent by the trusted parent.

    This value belongs only in a private bootstrap pipe. No argv/env/config
    fallback. On every validation failure the received handle is closed.
    """
    _windows()
    if isinstance(handle, bool) or not isinstance(handle, int) or handle <= 0 or handle == _INVALID:
        raise ValueError("A valid supervisor-transferred room writer handle is required")
    try:
        directory = _directory(data_dir)
        _kind(kind)
        _checked_handle(handle, directory)
        record = _read_record(handle)
        if record is None or record["kind"] != kind:
            raise ValueError("Supervisor room writer kind does not match the child")
        return RoomWriter(directory, handle, record)
    except BaseException:
        _close(handle)
        raise
