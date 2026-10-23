"""Pending wakes survive provider failure, but explicit Stop cancels them."""
import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from zerocollab.coordination.coordination_queue import WakeQueue
from zerocollab.runtime.room_runtime import RoomRuntime


class PendingRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self):
        r = object.__new__(RoomRuntime)
        r.states = {'a': {'state': 'ready', 'provider': 'api'}}
        r.queues = {'a': WakeQueue(maxsize=4)}
        r.running, r.deploy_held, r.approvals = {}, {}, {}
        r.paused, r.deployment = False, None
        r.goals = Mock(goal=None)
        r.team_builder, r.decisions, r.manager_wakes, r.ready_wakes, r.response_claims = Mock(), Mock(), Mock(), Mock(), Mock()
        r.wake_notices, r.notice_lines = {}, {}
        r.check_room_tools = Mock()
        r.project_room = Mock(state={'receipts': {}, 'tasks': []})
        r.host = Mock()
        r.host.store.add.return_value = {'id': 1}
        r.cfg = {'agents': {}, 'runtime': {}}
        r.publish_status, r.emit = AsyncMock(), AsyncMock()
        r.parallel_capacity = lambda: 4
        r.turn_admission_open = lambda: True
        r.collect_diffs = AsyncMock(return_value={})
        r.context = lambda run, changes: 'context'
        r.save_project = Mock()
        r.default_channel = lambda: 'general'
        return r

    def entry(self, channel):
        return {'channel': channel, 'chain': 'c',
                'requests': [{'human': True, 'chain': 'c', 'prompt': channel}]}

    def provider(self, r):
        run = Mock(id='run', channel='general', settings={}, stop_reason='', stderr='')
        run.name = 'a'
        run.focus_history, run.focus_seed = [], None
        run.created_at, run.first_output_at, run.progress = 0, None, {}
        run.manager_audit, run.stop_note, run.relay_problem = None, '', ''
        run.finish_message, run.publish_last = AsyncMock(), AsyncMock()
        run.steering.close = AsyncMock()
        self.enterContext(patch('zerocollab.runtime.room_runtime.Run', return_value=run))
        self.enterContext(patch('zerocollab.runtime.room_runtime.native.terminate', new=AsyncMock()))
        self.enterContext(patch('zerocollab.runtime.room_runtime.native.restore_room_server'))
        r.local_turn = AsyncMock(side_effect=RuntimeError('provider unavailable'))

    async def wait_until(self, predicate):
        async with asyncio.timeout(1):
            while not predicate():
                await asyncio.sleep(.01)

    async def test_blocked_after_empty_get_retains_held_entry(self):
        r = self.runtime()
        self.provider(r)
        worker = asyncio.create_task(r.worker('a'))
        try:
            await asyncio.sleep(0)  # worker is suspended in empty queue.get()
            r.states['a']['state'] = 'login_required'
            pending = self.entry('general')
            r.queues['a'].put_nowait(pending)
            await self.wait_until(lambda: r.states['a'].get('waiting'))
            await asyncio.sleep(.25)
            self.assertIs(r.deploy_held['a'], pending)
            r.local_turn.assert_not_awaited()
            r.states['a']['state'] = 'ready'
            await self.wait_until(lambda: r.local_turn.await_count == 1)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_blocked_during_capacity_wait_survives_until_recovery(self):
        r = self.runtime()
        self.provider(r)
        r.parallel_capacity = lambda: 1
        r.running['other'] = Mock()
        pending = self.entry('general')
        r.queues['a'].put_nowait(pending)
        worker = asyncio.create_task(r.worker('a'))
        try:
            await self.wait_until(lambda: r.states['a'].get('waiting'))
            r.states['a']['state'] = 'blocked'
            r.running.clear()
            await asyncio.sleep(.25)
            self.assertIs(r.deploy_held['a'], pending)
            r.local_turn.assert_not_awaited()
            r.states['a']['state'] = 'ready'
            await self.wait_until(lambda: r.local_turn.await_count == 1)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_stop_cancels_held_admission_without_replaying_on_resume(self):
        r = self.runtime()
        self.provider(r)
        r.turn_admission_open = lambda: False
        r.queues['a'].put_nowait(self.entry('general'))
        worker = asyncio.create_task(r.worker('a'))
        try:
            await self.wait_until(lambda: r.states['a'].get('waiting'))
            await r.stop_agent('a')
            r.states['a']['paused'] = False
            r.turn_admission_open = lambda: True
            await asyncio.sleep(.25)
            self.assertNotIn('a', r.deploy_held)
            r.local_turn.assert_not_awaited()
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_provider_failure_keeps_other_channel_pending(self):
        r = self.runtime()
        pending = self.entry('other')
        r.queues['a'].put_nowait(pending)
        run = Mock(id='run', channel='general', settings={}, stop_reason='', stderr='')
        run.name = 'a'
        run.focus_history, run.focus_seed = [], None
        run.created_at, run.first_output_at, run.progress = 0, None, {}
        run.manager_audit, run.stop_note, run.relay_problem = None, '', ''
        run.finish_message, run.publish_last = AsyncMock(), AsyncMock()
        run.steering.close = AsyncMock()
        r.local_turn = AsyncMock(side_effect=RuntimeError('provider unavailable'))
        with patch('zerocollab.runtime.room_runtime.Run', return_value=run), \
             patch('zerocollab.runtime.room_runtime.native.terminate', new=AsyncMock()), \
             patch('zerocollab.runtime.room_runtime.native.restore_room_server'):
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
