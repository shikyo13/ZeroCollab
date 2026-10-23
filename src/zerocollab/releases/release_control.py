"""Host-wide manual Stop state, independent of any project or rollback snapshot.

The app is the sole writer. User-only Stop/Resume routes call these methods;
room tools, maintenance and rollback must never call the mutators. The host
supervisor reads the atomic file and uses the runtime's reopen endpoint, never
an unconditional Resume. Construct once after initial user stop state is known.
"""
import inspect
import json
import hashlib
import time
from pathlib import Path

from zerocollab.storage.durable import atomic_write


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


class ReleaseGrant:
    """User-controlled grant; identity mutations can only lapse it, never enable it.

    Share DeploymentState.lock with request admission. Call set_user_grant only
    from the authenticated user Updates route, not an agent tool or a migration.
    The fixed grantee is Astra; the room run supplies its permanent profile id.
    """
    def __init__(self, deployment_root, lock):
        self.path = Path(deployment_root) / 'release-manager-grant.json'
        self.lock = lock

    @staticmethod
    def profile_stamp(profile):
        if not isinstance(profile, dict) or profile.get('id') != 'astra' or profile.get('archived'):
            raise ValueError('The active Astra profile is required')
        identity = {key: profile.get(key, '') for key in ('id', 'name', 'template')}
        identity['handle'] = profile.get('handle', profile['id'])
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode('utf-8')).hexdigest()

    def _read(self):
        try:
            value = json.loads(self.path.read_text('utf-8'))
        except FileNotFoundError:
            return {'version': 1, 'revision': 0, 'enabled': False}
        if (not isinstance(value, dict) or value.get('version') != 1
                or type(value.get('revision')) is not int or value['revision'] < 0
                or type(value.get('enabled')) is not bool):
            raise ValueError('Release-manager grant is invalid; deployment is disabled')
        return value

    def _write(self, value):
        atomic_write(self.path, lambda stream: json.dump(value, stream, sort_keys=True))

    def snapshot(self):
        with self.lock:
            return self._read()

    def set_user_grant(self, *, enabled, expected_revision, project, profile):
        if type(enabled) is not bool or type(expected_revision) is not int:
            raise ValueError('Grant state and expected revision are required')
        with self.lock:
            current = self._read()
            if current['revision'] != expected_revision:
                raise ValueError('Grant changed; reload Updates before choosing again')
            if enabled:
                if not isinstance(project, str) or not project:
                    raise ValueError('Choose the active project')
                stamp = self.profile_stamp(profile)
                current.update(project=project, profile='astra', profile_stamp=stamp)
            current.update(enabled=enabled, revision=current['revision'] + 1,
                           changed_by='user', changed_at=time.time(), lapse_reason='')
            self._write(current)
            return current

    def invalidate_profile(self, project, profile):
        """Server identity-edit hook: rename/archive/replacement permanently lapses authority."""
        with self.lock:
            current = self._read()
            if current.get('project') != project or current.get('profile') != profile or not current['enabled']:
                return current
            current.update(enabled=False, revision=current['revision'] + 1,
                           lapse_reason='Profile identity changed; user re-enablement required',
                           changed_at=time.time(), changed_by='profile-identity-change')
            self._write(current)
            return current

    def require(self, caller, profile):
        """Caller is host-derived {profile, project, run}, never copied from tool arguments."""
        with self.lock:
            current = self._read()
            if (not current['enabled'] or caller.get('profile') != 'astra'
                    or current.get('profile') != caller.get('profile')
                    or current.get('project') != caller.get('project')
                    or not caller.get('run')):
                raise ValueError('No enabled release-manager grant for this run')
            if current.get('profile_stamp') != self.profile_stamp(profile):
                raise ValueError('Release-manager profile changed; user re-enablement required')
            return current
