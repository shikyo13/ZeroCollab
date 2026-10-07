"""Pending wakes survive provider failure, but explicit Stop cancels them."""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from coordination_queue import WakeQueue
from room_runtime import RoomRuntime


class PendingRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self):
        r = object.__new__(RoomRuntime)
        r.states = {'a': {'state': 'ready', 'provider': 'api'}}
        r.queues = {'a': WakeQueue(maxsize=4)}
        r.running, r.deploy_held, r.approvals = {}, {}, {}
        r.paused, r.deployment = False, None
        r.goals = Mock(goal=None)
        r.team_builder = Mock()
        r.project_room = Mock(state={'receipts': {}})
        r.host = Mock()
        r.publish_status, r.emit = AsyncMock(), AsyncMock()
        r.parallel_capacity = lambda: 4
        r.turn_admission_open = lambda: True
        r.context = lambda run: 'context'
        r.save_project = Mock()
        r.default_channel = lambda: 'general'
        return r

    def entry(self, channel):
        return {'channel': channel, 'chain': 'c',
                'requests': [{'human': True, 'chain': 'c', 'prompt': channel}]}

    async def test_provider_failure_keeps_other_channel_pending(self):
        r = self.runtime()
        pending = self.entry('other')
        r.queues['a'].put_nowait(pending)
        run = Mock(id='run', channel='general', settings={}, stop_reason='', stderr='')
        run.finish_message = AsyncMock()
        run.steering.close = AsyncMock()
        r.local_turn = AsyncMock(side_effect=RuntimeError('provider unavailable'))
        with patch('room_runtime.Run', return_value=run), \
             patch('room_runtime.native.terminate', new=AsyncMock()), \
             patch('room_runtime.native.restore_room_server'):
            await r.run_turn('a', self.entry('general'))
        self.assertEqual(r.states['a']['state'], 'blocked')
        self.assertIs(r.queues['a'].get_nowait(), pending)

    async def test_worker_retains_then_delivers_after_recovery(self):
        r = self.runtime()
        r.states['a']['state'] = 'blocked'
        pending = self.entry('other')
        r.queues['a'].put_nowait(pending)
        delivered = asyncio.Event()
        async def turn(name, entry):
            self.assertIs(entry, pending)
            delivered.set()
        r.run_turn = turn
        worker = asyncio.create_task(r.worker('a'))
        try:
            await asyncio.sleep(.15)
            self.assertEqual(r.queues['a'].qsize(), 1)
            self.assertFalse(delivered.is_set())
            r.states['a']['state'] = 'ready'
            await asyncio.wait_for(delivered.wait(), 1)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_explicit_stop_clears_retained_work(self):
        r = self.runtime()
        r.states['a']['state'] = 'blocked'
        r.queues['a'].put_nowait(self.entry('other'))
        r.run_turn = AsyncMock()
        worker = asyncio.create_task(r.worker('a'))
        try:
            await asyncio.sleep(0)
            await r.stop_agent('a')
            self.assertTrue(r.queues['a'].empty())
            r.states['a'].update(state='ready', paused=False)
            await asyncio.sleep(.15)
            r.run_turn.assert_not_awaited()
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
