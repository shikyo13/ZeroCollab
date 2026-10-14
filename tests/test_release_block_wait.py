"""Disposable ASGI checks for the real write gate.

The grant/evidence validator is a stand-in; no supervisor, providers, live data,
credentials or installed files are used. Open tabs and typing never hold the
gate (Zero #17109, task abc2b8e3); only another in-flight write does.
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
        self.events.append(json.loads(text)['type'])

    async def block(self):
        return await self.client.post(PATH, json={'action': 'block'})

    def hold_write(self):
        """Another write in flight: the one thing the gate still waits for."""
        self.assertTrue(self.state.enter_mutation())
        self.addCleanup(lambda: self.state.mutations and self.state.leave_mutation())

    def assert_open(self):
        if self.state.mutations:
            self.state.leave_mutation()
        self.assertEqual(self.state.reload()['writes'], 'open')
        self.assertEqual(self.state.mutations, 0)
        self.assertFalse(self.host.composing.attempts)

    async def test_typing_never_holds_writes_or_the_supervisor(self):
        self.report(True)
        response = await self.client.get(PATH)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['composing']['busy'], 0)  # the supervisor's tab wait passes at once
        self.assertEqual(response.json()['composing']['clients'], 1)
        start = time.monotonic()
        response = await self.block()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(self.state.reload()['writes'], 'blocked')
        self.assertEqual(self.state.mutations, 0)
        self.assertNotIn('deployment_prepare', self.events)

    async def test_a_write_past_the_bound_refuses_and_keeps_reason(self):
        self.hold_write()
        with patch.object(release_maintenance, 'BLOCK_WAIT_SECONDS', .5):
            with self.assertLogs('release_maintenance', level='WARNING') as logs:
                response = await self.block()
        self.assertEqual(response.status_code, 409, response.text)
        reason = response.json()['error']
        self.assertIn('timed out after 0.5s', reason)
        self.assertIn('other active writes', reason)
        self.assertIn(reason, self.state.reload()['reason'])
        self.assertTrue(any(reason in line for line in logs.output))
        self.assert_open()

    async def test_stale_or_nonliteral_capability_uses_legacy_bound(self):
        for record in ({'updated': time.time()}, {'updated': time.time()-30, 'agent_install_interrupt': 1},
                       {'updated': time.time(), 'agent_install_interrupt': '1'},
                       {'updated': time.time(), 'agent_install_interrupt': True},
                       {'updated': time.time(), 'agent_install_interrupt': 2},
                       {'updated': time.time()+30, 'agent_install_interrupt': 1}):
            with self.subTest(record=record):
                room_deploy.atomic_json(self.spool / 'status.json', record)
                self.hold_write()
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

    async def test_authority_changes_refuse_during_wait(self):
        for reason in ('User Stop prevents deployment', 'Release grant was revoked'):
            with self.subTest(reason=reason):
                self.authority_error = ''
                self.hold_write()
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
        self.hold_write()
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


if __name__ == '__main__':
    unittest.main()
