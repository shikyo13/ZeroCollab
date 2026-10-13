"""App half of agent deployment drain; reuses the existing maintenance record.

Called by the authenticated supervisor, never by a managed run token. Request
policy is supplied by ReleaseManager.validate_queued; absent policy fails closed.
"""
import asyncio
import re

PROTOCOL = 1
ID = re.compile(r'[0-9a-f]{32}')


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
        self.validate(identifier)
        return {'protocol': PROTOCOL, 'composing': self.host.composing.state(),
                'active_runs': active_runs(self.runtime), 'active_mutations': self.state.mutations}

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

    async def block(self, identifier):
        from room_deploy import atomic_json
        async with self.preparing:
            self.validate(identifier)
            gate = self.state.reload()
            if (gate.get('deployment_id'), gate['admission'], gate['writes']) != (identifier, 'closed', 'open'):
                raise ValueError('Admission must be drained before composing acknowledgement')
            composing = self.host.composing
            if active_runs(self.runtime) or self.state.mutations != 1 or composing.state()['busy']:
                raise ValueError('The room still has active turns, writes or editors')
            completed = False
            try:
                async with asyncio.timeout(12):
                    await composing.prepare(identifier)
                    if not await composing.wait_acks(identifier, timeout=10):
                        raise ValueError('Open tabs did not acknowledge an idle maintenance state')
                with self.state.lock:
                    self.validate(identifier)
                    gate = self.state.reload()
                    if (gate.get('deployment_id'), gate['admission'], gate['writes']) != (identifier, 'closed', 'open'):
                        raise ValueError('Maintenance changed during preparation')
                    if active_runs(self.runtime) or self.state.mutations != 1 or not composing.ready(identifier):
                        raise ValueError('Activity changed before writes could close')
                    # No await from ready() to durable close. This HTTP request
                    # is the sole counted mutation; middleware leaves it once.
                    gate.update(seq=gate['seq'] + 1, writes='blocked', interrupt=False,
                                reason='Deployment in progress; room resumes automatically unless stopped')
                    atomic_json(self.state.root / 'maintenance.json', gate)
                    self.state.reload()
                    completed = True
                    return {'ok': True, 'seq': gate['seq']}
            finally:
                if not completed:
                    await composing.release(identifier)

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
