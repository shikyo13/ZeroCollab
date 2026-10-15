"""Exclusive ownership of a Windows desktop profile, independent of APPDATA.

The trusted launcher passes both paths. Keep the returned file open until the
runtime has closed. Closing/hiding a window must not close this file.
"""

import os
from pathlib import Path
from typing import BinaryIO


def open_profile(profile: Path, expected_profile: Path) -> BinaryIO:
    """Validate the chosen profile before mkdir and hold its byte lock.

    Neither path is inferred from environment variables or the working directory.
    This helper creates only the chosen directory and its owner.lock; it never
    reads, imports, resets or migrates room records. An occupied lock fails
    immediately. The caller owns the returned handle and must close it on every
    shutdown/error path. Windows also releases the lock if the owner process dies.
    """
    if os.name != "nt":
        raise RuntimeError("Windows profile locking is required")
    profile, expected_profile = Path(profile), Path(expected_profile)
    if not profile.is_absolute() or not expected_profile.is_absolute():
        raise ValueError("Absolute profile paths are required")
    profile, expected_profile = profile.resolve(), expected_profile.resolve()
    if profile != expected_profile:
        raise ValueError("Profile does not match the launcher path")

    profile.mkdir(parents=True, exist_ok=True)
    import msvcrt

    # No append mode: acquire ownership before initializing an empty lock file.
    descriptor = os.open(profile / "owner.lock", os.O_RDWR | os.O_CREAT | os.O_BINARY, 0o600)
    try:
        os.set_inheritable(descriptor, False)
        handle = os.fdopen(descriptor, "r+b", buffering=0)
    except BaseException:
        os.close(descriptor)
        raise
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
        handle.seek(0)
        return handle
    except BaseException:
        handle.close()
        raise
