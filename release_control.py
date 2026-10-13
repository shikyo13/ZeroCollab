"""Host-wide manual Stop state, independent of any project or rollback snapshot.

The app is the sole writer. User-only Stop/Resume routes call these methods;
room tools, maintenance and rollback must never call the mutators. The host
supervisor reads the atomic file and uses the runtime's reopen endpoint, never
an unconditional Resume. Construct once after initial user stop state is known.
"""
import inspect
import json
from pathlib import Path

from durable import atomic_write


class ManualStopState:
    def __init__(self, deployment_root, lock, *, initially_stopped):
        if type(initially_stopped) is not bool:
            raise ValueError('Initial stop state must be a boolean')
        self.path = Path(deployment_root) / 'manual-control.json'
        self.lock = lock
        with self.lock:
            if not self.path.exists():
                # One-time host bootstrap preserves the currently known user
                # state. Restart never replaces an existing persisted latch.
                self._write({'version': 1, 'generation': 0, 'stopped': initially_stopped})
            self._read()

    def _read(self):
        value = json.loads(self.path.read_text('utf-8'))
        if (not isinstance(value, dict) or value.get('version') != 1
                or type(value.get('generation')) is not int or value['generation'] < 0
                or type(value.get('stopped')) is not bool):
            raise ValueError('Manual Stop state is invalid; automatic deployment is disabled')
        return value

    def _write(self, value):
        atomic_write(self.path, lambda stream: json.dump(value, stream, sort_keys=True))

    def snapshot(self):
        with self.lock:
            return self._read()

    def set_user_stop(self):
        """User Stop only: persist before runtime cancels any active turn."""
        with self.lock:
            value = self._read()
            value.update(generation=value['generation'] + 1, stopped=True)
            self._write(value)
            return value

    @staticmethod
    def _transition(callback):
        # Callbacks only assign synchronous runtime state; no I/O or awaits.
        if not callable(callback) or inspect.iscoroutinefunction(callback):
            raise TypeError('The resume transition must be synchronous')

    def clear_user_stop(self, resume):
        """User Resume only: clear and change runtime state in one critical section."""
        self._transition(resume)
        with self.lock:
            value = self._read()
            value['stopped'] = False
            self._write(value)
            resume()
            return value

    def reopen_if(self, generation, resume):
        """Automatic reopen: compare AND transition atomically; never clear Stop.

        Runtime passes a synchronous callback setting its paused flag. It must
        not check this method then resume later. Post-transition queue work can
        happen outside the lock, still subject to normal runtime admission.
        """
        if type(generation) is not int or generation < 0:
            raise ValueError('Invalid Stop generation')
        self._transition(resume)
        with self.lock:
            value = self._read()
            if value['stopped'] or value['generation'] != generation:
                return False
            resume()
            return True
