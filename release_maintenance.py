"""App half of agent deployment drain; reuses the existing maintenance record.

Called by the authenticated supervisor, never by a managed run token. Request
policy is supplied by ReleaseManager.validate_queued; absent policy fails closed.
"""
import asyncio
import logging
import re
import sqlite3
import time

import roomdb

PROTOCOL = 1
ID = re.compile(r'[0-9a-f]{32}')
BLOCK_WAIT_SECONDS = 60
LEGACY_BLOCK_WAIT_SECONDS = 15
BLOCK_POLL_SECONDS = 0.25
LOG = logging.getLogger(__name__)


def active_runs(runtime):
    return len(runtime.running) + sum(bool(s.get('task')) and not s.get('waiting')
        and name not in runtime.running for name, s in runtime.states.items())


class ReleaseMaintenance:
    def __init__(self, host, runtime, state):
        self.host, self.runtime, self.state = host, runtime, state
        self.preparing = asyncio.Lock()

    def request(self, identifier):
        if not isinstance(identifier, str) or not ID.fullmatch(identifier):
            raise ValueError('An exact deployment id is required')
        manager = self.runtime.release_manager
        journal = manager._record('journal.json', {})
        if journal.get('id') != identifier:
            raise ValueError('This deployment is not the current supervisor journal')
        request = journal.get('request', {})
        if (request.get('id'), request.get('protocol'), request.get('approved_by')) != (
                identifier, PROTOCOL, 'release-manager:astra'):
            raise ValueError('Journal is not an agent deployment under this protocol')
        if type(request.get('stop_generation')) is not int or request['stop_generation'] < 0:
            raise ValueError('Journal has no valid Stop generation')
        return request

    def validate(self, identifier):
        request = self.request(identifier)
        policy = getattr(self.runtime.release_manager, 'validate_queued', None)
        if not callable(policy):
            raise ValueError('Agent deployment admission policy is unavailable')
        # Synchronous: authoritative grant/Stop/current/acceptance checks must
        # finish without yielding between this check and final gate closure.
        if policy(request) is not True:
            raise ValueError('Agent deployment admission policy did not approve this request')
        return request

    def status(self, identifier):
        try:
            self.validate(identifier)
            # Typing never holds or aborts an install (Zero #17109, task abc2b8e3): the supervisor waits while
            # composing.busy is non-zero, so it is always 0 here. The tab counts stay for diagnostics.
            return {'protocol': PROTOCOL, 'composing': {**self.host.composing.state(), 'busy': 0},
                    'active_runs': active_runs(self.runtime), 'active_mutations': self.state.mutations}
        except (OSError, ValueError, TypeError) as exc:
            self.refusal(identifier, 'status', exc)
            raise

    def refusal(self, identifier, action, error):
        """Keep the diagnostic without changing gates, Stop or another deployment."""
        from room_deploy import atomic_json
        reason = f'Agent deployment {action} refused: {error}'
        LOG.warning('%s (%s)', reason, identifier)
        try:
            with self.state.lock:
                gate = self.state.reload()
                if gate.get('deployment_id') == identifier and ID.fullmatch(str(identifier)):
                    gate.update(seq=gate['seq'] + 1, reason=reason)
                    atomic_json(self.state.root / 'maintenance.json', gate)
                    self.state.reload()
        except (OSError, ValueError, TypeError):
            LOG.exception('Could not persist maintenance refusal reason')

    def announce(self, identifier):
        request = self.validate(identifier)
        release = self.state.release(request['release_id'])
        uid = 'release-maintenance:' + identifier
        # Existing durable chat uid deduplicates a retry even if the supervisor
        # crashed immediately after add(). No room-data writes behind the gate.
        if not any(m.get('uid') == uid for m in self.host.store.get_since()):
            self.host.store.add('system', f"Deploying {release.get('version') or release['id']}; "
                'current turns are finishing. The room resumes automatically unless you press Stop.',
                uid=uid, channel=self.runtime.default_channel(), metadata={'no_route': True})
        return {'ok': True}

    def save_board(self):
        """A full room.db comparison before writes close, so the install snapshot holds every record, including
        one a touched save left for the next full comparison (roomdb.FULL_DIFF_SECONDS). A failure refuses the block."""
        room = getattr(self.runtime, 'project_room', None)
        if getattr(room, 'store', None) is None:
            return  # no project open, so no board to save
        try:
            room.save()
            # Every project's coordination.json beside its room.db, so a rollback to a release before room.db reads
            # the current boards (harness c8d41869). The snapshot is taken after this block, so it holds them too.
            roomdb.export_all(self.runtime.data / 'projects')
        except (sqlite3.Error, RuntimeError, OSError) as exc:
            raise OSError(f'The board could not be saved before writes close ({exc}); the block was refused, '
                          'so the deployment can be retried') from exc

    async def block(self, identifier):
        from room_deploy import atomic_json
        waiting = 'another maintenance preparation'
        wait_seconds = LEGACY_BLOCK_WAIT_SECONDS
        try:
            status = self.runtime.release_manager._record('status.json', {})
            # Paired host capability 1 includes a 75s block HTTP timeout.
            advertised = status.get('agent_install_interrupt')
            if (type(advertised) is int and advertised == 1
                    and 0 <= time.time() - float(status.get('updated', 0)) <= 15):
                wait_seconds = BLOCK_WAIT_SECONDS
            if self.preparing.locked():
                raise ValueError('Another maintenance preparation is already active')
            async with asyncio.timeout(wait_seconds):
                async with self.preparing:
                    while True:
                        # Open tabs and typing never hold writes open (Zero #17109, task abc2b8e3); only other
                        # in-flight writes do. Checked and closed in one synchronous step under the gate lock.
                        with self.state.lock:
                            self.validate(identifier)  # journal, grant and Stop on every pass
                            gate = self.state.reload()
                            if (gate.get('deployment_id'), gate['admission'], gate['writes']) != (identifier, 'closed', 'open'):
                                raise ValueError('Admission must be drained before writes close')
                            if active_runs(self.runtime):
                                raise ValueError('An agent turn became active before writes could close')
                            if self.state.mutations == 1:  # this HTTP request is the only counted write
                                self.save_board()
                                gate.update(seq=gate['seq'] + 1, writes='blocked', interrupt=False,
                                    reason='Deployment in progress; room resumes automatically unless stopped')
                                atomic_json(self.state.root / 'maintenance.json', gate)
                                self.state.reload()
                                return {'ok': True, 'seq': gate['seq']}
                            waiting = f'other active writes (count {self.state.mutations})'
                        await asyncio.sleep(BLOCK_POLL_SECONDS)
        except TimeoutError as exc:
            error = ValueError(f'Maintenance block timed out after {wait_seconds}s waiting for {waiting}')
            self.refusal(identifier, 'block', error)
            raise error from exc
        except (OSError, ValueError, TypeError) as exc:
            self.refusal(identifier, 'block', exc)
            raise

    async def release(self, identifier):
        # Revoke and Stop may have happened during apply. They never prevent
        # unlocking the human UI; only the persisted Stop latch decides resume.
        request = self.request(identifier)
        gate = self.state.reload()
        if gate.get('deployment_id') != identifier or not self.state.admission_open():
            raise ValueError('The supervisor must reopen the gate first')
        await self.host.composing.release(identifier)
        await self.runtime.reconcile_manual_stop()
        if self.runtime.manual_stop_pending:
            return {'ok': True, 'reopened': False, 'reason': 'Manual control reconciliation is pending'}
        resumed = await self.runtime.reopen_if(request.get('stop_generation'))
        return {'ok': True, 'reopened': resumed}
