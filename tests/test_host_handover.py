"""Concrete bridge blockers with disposable state; no process/credential access."""
import asyncio
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import host_handover as hh
from ui_composing import Composing


class State:
    def __init__(self, root):
        self.root, self.lock = root, threading.RLock()
        self.mutations, self.rehearsal = 0, False
        self.release_grant = NS(require=lambda *a: {'revision': 1, 'enabled': True})

    def reload(self):
        return hh.read_record(self.root / 'maintenance.json') or {
            'seq': 0, 'admission': 'open', 'writes': 'open', 'deployment_id': ''}

    def admission_open(self):
        return self.reload()['admission'] == 'open' and self.reload()['writes'] == 'open'


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / 'data' / 'deploy'
        self.root.mkdir(parents=True)
        self.state = State(self.root)
        self.latch = {'version': 1, 'generation': 0, 'stopped': False}
        self.queues = {'project_id': 'p', 'queues': {'astra': [{'prompt': 'verify handover'}]}}
        self.exits, self.reopens = [], []
        self.host_hash = hh.OLD

        async def send(*args):
            pass

        async def reconcile():
            pass

        async def reopen(generation):
            self.reopens.append(generation)
            return not self.latch['stopped'] and generation == self.latch['generation']

        self.rt = NS(deployment=self.state, identity='p', paused=False, manual_stop_pending=False,
                     manual_stop=NS(snapshot=lambda: dict(self.latch)), running={}, states={},
                     profiles=NS(items={'astra': {'id': 'astra'}}),
                     host=NS(composing=Composing({}, send), media_jobs=NS(tasks=set(), entries={}, active=None)),
                     browser=NS(instances={}), reconcile_manual_stop=reconcile, reopen_if=reopen)
        self.rt.save_deploy_queue = lambda: hh.write_record(self.root / 'pending-wakes.json', self.queues)
        self.rt.append_event = lambda line: None
        self.req = {'version': 1, 'nonce': 'a' * 32, 'created': time.time(),
                    'expected_manifest_sha': 'b' * 64, 'from_sha256': hh.OLD,
                    'to_sha256': hh.NEW, 'helper_pid': 123, 'helper_started': 456}
        hh.write_record(self.root / hh.REQUEST, self.req)
        hh.write_record(self.root / 'current.json', {'manifest_sha': 'b' * 64})
        original = hh.file_sha

        def sha(path, *args):
            return self.host_hash if path.name == 'supervisor.py' else original(path, *args)

        self.sha_patch = patch.object(hh, 'file_sha', side_effect=sha)
        self.sha_patch.start()
        self.addCleanup(self.sha_patch.stop)
        self.helper = patch.object(hh, 'helper_alive', return_value=True).start()
        self.addCleanup(patch.stopall)
        self.bridge = self.make_bridge()

    def make_bridge(self):
        return hh.HostHandover(self.rt, self.home, lambda: self.exits.append('graceful'))

    async def test_drain_checkpoint_exit_and_pinned_reopen(self):
        before = dict(self.latch)
        await self.bridge.tick()
        ack = hh.read_record(self.root / hh.ACK)
        checkpoint = (self.root / 'pending-wakes.json').read_bytes()
        self.assertEqual(ack['phase'], 'ready')
        self.assertTrue(self.bridge.held)
        self.assertEqual(self.state.reload()['writes'], 'blocked')
        self.assertEqual(self.exits, ['graceful'])
        self.assertEqual(self.latch, before)
        self.assertEqual(json.loads(checkpoint), self.queues)
        restarted = self.make_bridge()
        self.assertTrue(restarted.startup_hold())
        self.assertFalse(self.state.admission_open())
        self.host_hash = hh.NEW
        hh.write_record(self.root / hh.REOPEN, {'version': 1, 'nonce': ack['nonce'],
            'expected_manifest_sha': ack['expected_manifest_sha'], 'host_sha256': hh.NEW,
            'outcome': 'applied', 'checkpoint_sha256': ack['checkpoint_sha256'], 'created': time.time()})
        await restarted.tick()
        self.assertFalse(restarted.held)
        self.assertEqual(hh.read_record(self.root / hh.ACK)['phase'], 'completed')
        self.assertEqual((self.root / 'pending-wakes.json').read_bytes(), checkpoint)
        self.assertEqual(self.latch, before)
        self.assertFalse(self.make_bridge().startup_hold())

    async def test_unstarted_turn_and_media_owner_prevent_ack(self):
        self.rt.states = {'astra': {'task': object(), 'waiting': False}}
        await self.bridge.tick()
        self.assertIsNone(hh.read_record(self.root / hh.ACK))
        self.assertEqual(self.exits, [])
        self.rt.states.clear()
        pending = asyncio.get_running_loop().create_future()
        self.rt.host.media_jobs.entries['job'] = NS(done=pending)
        await self.bridge.tick()
        self.assertIsNone(hh.read_record(self.root / hh.ACK))
        pending.set_result(None)
        await self.bridge.tick()
        self.assertEqual(self.exits, ['graceful'])

    async def test_helper_death_and_real_stop_never_exit(self):
        self.helper.return_value = False
        await self.bridge.tick()
        self.assertEqual(hh.read_record(self.root / hh.ACK)['reason'], 'helper_unavailable')
        self.assertEqual(self.exits, [])
        self.assertFalse(self.bridge.held)

    async def test_stop_during_prepare_is_preserved(self):
        original = self.rt.host.composing.wait_acks

        async def stop(attempt, timeout):
            self.latch.update(stopped=True, generation=1)
            return await original(attempt, timeout)

        self.rt.host.composing.wait_acks = stop
        await self.bridge.tick()
        self.assertEqual(self.exits, [])
        self.assertEqual(self.latch, {'version': 1, 'generation': 1, 'stopped': True})
        self.assertEqual(hh.read_record(self.root / hh.ACK)['phase'], 'refused')

    async def test_checkpoint_failure_holds_without_ready_or_exit(self):
        def fail():
            raise OSError('fixture write failure')

        self.rt.save_deploy_queue = fail
        await self.bridge.tick()
        self.assertEqual(self.exits, [])
        self.assertTrue(self.bridge.held)
        self.assertEqual(self.state.reload()['writes'], 'blocked')
        self.assertEqual(hh.read_record(self.root / hh.ACK)['phase'], 'failed')

    async def test_settled_taken_is_allowed_unsettled_refuses(self):
        folder = self.root / 'requests'
        folder.mkdir()
        (self.root / 'receipts').mkdir()
        request = {'id': 'legacy'}
        hh.write_record(folder / 'legacy.taken', request)
        hh.write_record(self.root / 'receipts/legacy.json', {'id': 'legacy', 'request': request, 'status': 'deployed'})
        await self.bridge.tick()
        self.assertEqual(self.exits, ['graceful'])

    async def test_rollback_reopen_keeps_old_host_and_queue(self):
        await self.bridge.tick()
        ack = hh.read_record(self.root / hh.ACK)
        checkpoint = (self.root / 'pending-wakes.json').read_bytes()
        restarted = self.make_bridge()
        restarted.startup_hold()
        hh.write_record(self.root / hh.REOPEN, {'version': 1, 'nonce': ack['nonce'],
            'expected_manifest_sha': ack['expected_manifest_sha'], 'host_sha256': hh.OLD,
            'outcome': 'rolled_back', 'checkpoint_sha256': ack['checkpoint_sha256'], 'created': time.time()})
        await restarted.tick()
        self.assertFalse(restarted.held)
        self.assertEqual(hh.read_record(self.root / hh.ACK)['outcome'], 'rolled_back')
        self.assertEqual((self.root / 'pending-wakes.json').read_bytes(), checkpoint)

    async def test_mutation_and_unsettled_deploy_refuse_idle(self):
        self.state.mutations = 1
        await self.bridge.tick()
        self.assertIsNone(hh.read_record(self.root / hh.ACK))
        self.state.mutations = 0
        folder = self.root / 'requests'
        folder.mkdir()
        hh.write_record(folder / 'unknown.taken', {'id': 'unknown'})
        await self.bridge.tick()
        self.assertEqual(hh.read_record(self.root / hh.ACK)['reason'], 'deployment_unsettled')
        self.assertEqual(self.exits, [])

    async def test_timeout_refuses_and_user_can_release_restart_hold(self):
        self.req['created'] -= hh.SECONDS + 1
        hh.write_record(self.root / hh.REQUEST, self.req)
        await self.bridge.tick()
        self.assertEqual(hh.read_record(self.root / hh.ACK)['reason'], 'request_expired')
        self.assertFalse(self.bridge.held)
        ack = {'version': 1, 'nonce': self.req['nonce'], 'phase': 'failed', 'created': self.req['created']}
        hh.write_record(self.root / hh.ACK, ack)
        restarted = self.make_bridge()
        self.assertTrue(restarted.startup_hold())
        self.assertTrue(await restarted.release_for_user())
        self.assertTrue(self.state.admission_open())
        self.assertEqual(self.latch['generation'], 0)

    async def test_forged_reopen_cannot_release(self):
        await self.bridge.tick()
        ack = hh.read_record(self.root / hh.ACK)
        restarted = self.make_bridge()
        restarted.startup_hold()
        hh.write_record(self.root / hh.REOPEN, {'version': 1, 'nonce': 'c' * 32,
            'expected_manifest_sha': ack['expected_manifest_sha'], 'host_sha256': hh.NEW,
            'outcome': 'applied', 'checkpoint_sha256': ack['checkpoint_sha256'], 'created': time.time()})
        with self.assertRaises(ValueError):
            await restarted.tick()
        self.assertTrue(restarted.held)
        self.assertFalse(self.state.admission_open())


if __name__ == '__main__':
    unittest.main()
