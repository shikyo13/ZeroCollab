"""Bounded retained output for read-only Git previews (called off the event loop)."""
import os
import subprocess
import threading

OUTPUT_BYTES = 96000  # Enough UTF-8 bytes for the existing 24,000-character preview.
ERROR_BYTES = 4096
READ_BYTES = 8192
TIMEOUT_SECONDS = 15


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

    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0) as process:
        readers = [threading.Thread(target=drain, args=(process.stdout, output, limit)),
                   threading.Thread(target=drain, args=(process.stderr, errors, ERROR_BYTES))]
        for reader in readers:
            reader.start()
        try:
            process.wait(timeout=timeout)
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            for reader in readers:
                reader.join()
        if read_errors:
            raise read_errors[0]
        if process.returncode:
            raise RuntimeError(errors.decode('utf-8', errors='replace').strip() or 'Git command failed')
    return output.decode('utf-8', errors='replace')


def preview(path, *args, env=None):
    return capture(['git', '-c', 'core.hooksPath=' + os.devnull,
                    '-C', str(path), *args], env=env)
