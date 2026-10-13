"""Disposable ASGI checks for the real write gate and composing protocol.

The grant/evidence validator is a stand-in; no supervisor, providers, live data,
credentials or installed files are used. Real 20s/60s bounds are intentional.
"""
import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import release_maintenance
import room_deploy
from ui_composing import Composing

IDENTIFIER = 'a' * 32
PATH = '/api/runtime/deployments/control/' + IDENTIFIER


class BlockWaitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        data = Path(self.tmp.name)
        self.socket = object()
        self.acknowledge = True
        self.authority_error = ''
        self.validations = 0
        self.events = []
        self.host = SimpleNamespace(app=FastAPI())
        self.host.composing = Composing({self.socket}, self.send)
        self.runtime = SimpleNamespace(data=data, running={}, states={})
        room_deploy.install(self.host, self.runtime)
        self.state = self.host.deployment
        self.spool = self.state.root
        self.spool.mkdir(parents=True, exist_ok=True)
        room_deploy.atomic_json(self.spool / 'journal.json', {'id': IDENTIFIER, 'request': {
            'id': IDENTIFIER, 'protocol': 1, 'approved_by': 'release-manager:astra', 'stop_generation': 0}})
        room_deploy.atomic_json(self.spool / 'maintenance.json', {'seq': 1,
            'deployment_id': IDENTIFIER, 'admission': 'closed', 'writes': 'open', 'reason': 'Drained'})
        room_deploy.atomic_json(self.spool / 'status.json', {
            'updated': time.time(), 'agent_install_interrupt': 1})
        self.runtime.release_manager.validate_queued = self.validate
        self.report(False)
        self.client = AsyncClient(transport=ASGITransport(app=self.host.app), base_url='http://fixture')
        self.addAsyncCleanup(self.client.aclose)

    def validate(self, request):
        self.validations += 1
        if self.authority_error:
            raise ValueError(self.authority_error)
        return True

    def report(self, busy):
        self.host.composing.receive(self.socket, {'type': 'ui_composing', 'busy': busy})

    async def send(self, socket, text):
        event = json.loads(text)
        self.events.append(event['type'])
        if event['type'] == 'deployment_prepare' and self.acknowledge:
            self.host.composing.receive(socket, {'type': 'deployment_prepare_ack', 'id': event['id'], 'busy': False})

    async def block(self):
        return await self.client.post(PATH, json={'action': 'block'})

    def assert_open(self):
        self.assertEqual(self.state.reload()['writes'], 'open')
        self.assertEqual(self.state.mutations, 0)
        self.assertFalse(self.host.composing.attempts)

    async def test_editor_busy_twenty_seconds_then_closes(self):
        self.report(True)
        async def editor():
            for _ in range(20):
                await asyncio.sleep(1)
                self.report(True)
            self.report(False)
        task = asyncio.create_task(editor())
        start = time.monotonic()
        response = await self.block()
        await task
        elapsed = time.monotonic() - start
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreaterEqual(elapsed, 20)
        self.assertLess(elapsed, 25)
        self.assertEqual(self.state.reload()['writes'], 'blocked')
        self.assertEqual(self.state.mutations, 0)
        self.assertGreater(self.validations, 50)
        print(f'20s editor elapsed={elapsed:.3f}s validations={self.validations}', flush=True)

    async def test_editor_past_sixty_seconds_refuses_and_keeps_reason(self):
        self.report(True)
        start = time.monotonic()
        with self.assertLogs('release_maintenance', level='WARNING') as logs:
            response = await self.block()
        elapsed = time.monotonic() - start
        self.assertEqual(response.status_code, 409, response.text)
        reason = response.json()['error']
        self.assertIn('timed out after 60s', reason)
        self.assertIn('editors', reason)
        self.assertIn(reason, self.state.reload()['reason'])
        self.assertTrue(any(reason in line for line in logs.output))
        self.assertGreaterEqual(elapsed, 60)
        self.assertLess(elapsed, 65)
        self.assert_open()
        print(f'60s editor elapsed={elapsed:.3f}s reason={reason}', flush=True)

    async def test_legacy_host_refuses_at_fifteen_seconds(self):
        room_deploy.atomic_json(self.spool / 'status.json', {'updated': time.time()})
        self.report(True)
        start = time.monotonic()
        with self.assertLogs('release_maintenance', level='WARNING'):
            response = await self.block()
        elapsed = time.monotonic() - start
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn('timed out after 15s', response.json()['error'])
        self.assertGreaterEqual(elapsed, 15)
        self.assertLess(elapsed, 18)
        self.assert_open()
        print(f'legacy host elapsed={elapsed:.3f}s', flush=True)

    async def test_stale_or_nonliteral_capability_uses_legacy_bound(self):
        for record in ({'updated': time.time()-30, 'agent_install_interrupt': 1},
                       {'updated': time.time(), 'agent_install_interrupt': '1'},
                       {'updated': time.time(), 'agent_install_interrupt': True},
                       {'updated': time.time(), 'agent_install_interrupt': 2},
                       {'updated': time.time()+30, 'agent_install_interrupt': 1}):
            with self.subTest(record=record):
                room_deploy.atomic_json(self.spool / 'status.json', record)
                self.report(True)
                with patch.object(release_maintenance, 'LEGACY_BLOCK_WAIT_SECONDS', .1):
                    with self.assertLogs('release_maintenance', level='WARNING'):
                        response = await self.block()
                self.assertEqual(response.status_code, 409)
                self.assertIn('timed out after 0.1s', response.json()['error'])
                self.assert_open()

    async def test_another_write_finishes_then_closes(self):
        self.assertTrue(self.state.enter_mutation())
        async def writer():
            await asyncio.sleep(.3)
            self.state.leave_mutation()
        task = asyncio.create_task(writer())
        response = await self.block()
        await task
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.state.reload()['writes'], 'blocked')
        self.assertEqual(self.state.mutations, 0)

    async def test_missing_ack_retries_then_closes(self):
        self.acknowledge = False
        async def tab():
            await asyncio.sleep(.8)
            self.acknowledge = True
        task = asyncio.create_task(tab())
        with patch.object(release_maintenance, 'TAB_ACK_SECONDS', .5):
            response = await self.block()
        await task
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreaterEqual(self.events.count('deployment_prepare'), 2)
        self.assertIn('deployment_release', self.events)

    async def test_delayed_ack_keeps_the_attempt_and_rechecks_authority(self):
        self.acknowledge = False
        original = self.send
        async def delayed(socket, text):
            await original(socket, text)
            event = json.loads(text)
            if event['type'] == 'deployment_prepare':
                async def ack():
                    await asyncio.sleep(1)
                    self.host.composing.receive(socket, {'type': 'deployment_prepare_ack',
                        'id': event['id'], 'busy': False})
                self.ack_task = asyncio.create_task(ack())
        self.host.composing.send = delayed
        response = await self.block()
        await self.ack_task
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.events.count('deployment_prepare'), 1)
        self.assertGreaterEqual(self.validations, 3)

    async def test_authority_changes_refuse_during_wait(self):
        for reason in ('User Stop prevents deployment', 'Release grant was revoked'):
            with self.subTest(reason=reason):
                self.authority_error = ''
                self.report(True)
                async def revoke():
                    await asyncio.sleep(.1)
                    self.authority_error = reason
                task = asyncio.create_task(revoke())
                with self.assertLogs('release_maintenance', level='WARNING'):
                    response = await self.block()
                await task
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()['error'], reason)
                self.assertIn(reason, self.state.reload()['reason'])
                self.assert_open()

    async def test_new_agent_run_refuses_immediately(self):
        self.runtime.running['agent'] = object()
        start = time.monotonic()
        with self.assertLogs('release_maintenance', level='WARNING'):
            response = await self.block()
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(response.status_code, 409)
        self.assertIn('agent turn', response.json()['error'])
        self.assert_open()

    async def test_journal_changes_refuse_during_wait(self):
        self.report(True)
        async def change():
            await asyncio.sleep(.1)
            room_deploy.atomic_json(self.spool / 'journal.json', {'id': 'b' * 32})
        task = asyncio.create_task(change())
        with self.assertLogs('release_maintenance', level='WARNING'):
            response = await self.block()
        await task
        self.assertEqual(response.status_code, 409)
        self.assertIn('current supervisor journal', response.json()['error'])
        self.assert_open()

    async def test_status_logs_policy_refusal_without_touching_foreign_gate(self):
        self.authority_error = 'Release grant was revoked'
        with self.assertLogs('release_maintenance', level='WARNING') as logs:
            response = await self.client.get(PATH)
        self.assertEqual(response.status_code, 409)
        self.assertIn(self.authority_error, self.state.reload()['reason'])
        self.assertTrue(any(self.authority_error in line for line in logs.output))
        gate = self.state.reload()
        gate.update(deployment_id='b' * 32, reason='Another deployment')
        room_deploy.atomic_json(self.spool / 'maintenance.json', gate)
        with self.assertLogs('release_maintenance', level='WARNING'):
            response = await self.client.get(PATH)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.state.reload()['reason'], 'Another deployment')

    async def test_late_run_after_ack_releases_tabs_without_closing(self):
        original = self.send
        async def late_run(socket, text):
            await original(socket, text)
            if json.loads(text)['type'] == 'deployment_prepare':
                self.runtime.running['agent'] = object()
        self.host.composing.send = late_run
        with self.assertLogs('release_maintenance', level='WARNING'):
            response = await self.block()
        self.assertEqual(response.status_code, 409)
        self.assertIn('deployment_release', self.events)
        self.assert_open()

    async def test_disconnect_during_ack_services_stop_promptly(self):
        self.acknowledge = False
        original = self.send
        disconnected = []
        stopped = []
        async def stop():
            await asyncio.sleep(.05)
            self.authority_error = 'User Stop prevents deployment'
            stopped.append(time.monotonic())
        async def disconnect(socket, text):
            await original(socket, text)
            if json.loads(text)['type'] == 'deployment_prepare':
                self.host.composing.clients.remove(socket)
                self.host.composing.gone(socket)
                disconnected.append(time.monotonic())
                self.stop_task = asyncio.create_task(stop())
        self.host.composing.send = disconnect
        with self.assertLogs('release_maintenance', level='WARNING'):
            response = await self.block()
        await self.stop_task
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['error'], self.authority_error)
        self.assert_open()
        delay = stopped[0] - disconnected[0]
        print(f'disconnect scheduled Stop serviced after={delay:.3f}s', flush=True)
        self.assertLess(delay, 1, 'The ack loop blocked the event loop and delayed Stop')


if __name__ == '__main__':
    unittest.main()
