"""A Codex thread held open by another Codex process (Zero #13675) starts a new conversation instead of failing."""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import native_adapters as native
from coordination_queue import WakeQueue
from room_runtime import RoomRuntime

BUSY = 'thread 01a1257a already has an active writer'


class FakeAppServer:
    """In-process stand-in for `codex app-server --stdio`: answers each request written to stdin."""
    def __init__(self, busy_on):
        self.stdout = asyncio.StreamReader()
        self.stdin = SimpleNamespace(write=self.write, drain=AsyncMock(), close=Mock())
        self.returncode, self.pid, self.busy_on, self.methods = None, 0, busy_on, []

    def write(self, data):
        msg = json.loads(data)
        if 'id' not in msg:
            return
        self.methods.append(msg['method'])
        if msg['method'] == self.busy_on:
            reply = {'id': msg['id'], 'error': {'code': -32600, 'message': BUSY}}
        else:
            reply = {'id': msg['id'], 'result': {'thread': {'id': 'new'}, 'turn': {'id': 't1'}}}
        self.stdout.feed_data((json.dumps(reply) + '\n').encode())


class CodexAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def codex(self, busy_on, previous):
        server = FakeAppServer(busy_on)

        async def spawn(run, *args, **kwargs):
            run.process = server
            return server
        run = SimpleNamespace(previous_session=previous, automatic_tool_approvals=True, worktree='.', instructions='',
                              settings={'model': '', 'effort': ''}, manager=SimpleNamespace(cfg={'agents': {'a': {}}}),
                              name='a', report_permissions=AsyncMock(), report_settings=AsyncMock())
        with patch.object(native, 'spawn', spawn), patch.object(native, 'room_server', return_value={'command': 'x', 'args': []}), \
             patch.object(native, 'local_setup', return_value=None), patch.object(native, 'room_environment', return_value={}), \
             patch.object(native, 'relay_setup', return_value=({}, [])), patch.object(native, 'command', return_value=['codex']):
            await native.codex(run, 'prompt')
        return server

    async def test_resume_refused_by_another_writer_raises_thread_busy(self):
        for busy_on in ('thread/resume', 'turn/start'):
            with self.subTest(busy_on=busy_on):
                with self.assertRaises(native.ThreadBusy):
                    await self.codex(busy_on, {'id': 'old'})

    async def test_new_thread_with_the_same_error_is_not_thread_busy(self):
        # Only a resumed thread can be held elsewhere; a fresh thread failing is a real failure.
        with self.assertRaises(RuntimeError) as caught:
            await self.codex('turn/start', {})
        self.assertNotIsInstance(caught.exception, native.ThreadBusy)


class RoomRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_turn_restarts_once_on_a_new_conversation_with_full_context(self):
        r = object.__new__(RoomRuntime)
        r.states = {'a': {'state': 'ready', 'provider': 'codex'}}
        r.queues = {'a': WakeQueue(maxsize=4)}
        r.running, r.deploy_held, r.approvals = {}, {}, {}
        r.paused, r.deployment = False, None
        r.goals, r.team_builder, r.decisions, r.manager_wakes, r.ready_wakes = Mock(goal=None), Mock(), Mock(), Mock(), Mock()
        r.project_room = Mock(state={'receipts': {}})
        r.host = Mock()
        r.publish_status, r.emit, r.activity_note = AsyncMock(), AsyncMock(), AsyncMock()
        r.parallel_capacity = lambda: 4
        r.turn_admission_open = lambda: True
        r.collect_diffs = AsyncMock(return_value={})
        # A resumed session gets only what is new; a new conversation needs everything.
        r.context = lambda run, changes: 'new since last turn' if run.previous_session else 'full room context'
        r.save_project = Mock()
        r.default_channel = lambda: 'general'
        r.wake_notices, r.cfg = {}, {'runtime': {}, 'agents': {}}
        r.check_room_tools = Mock()
        run = Mock(id='run', channel='general', settings={}, stop_reason='', stderr='', manager_audit=None,
                   previous_session={'id': 'old'}, stop_note='', relay_problem='')
        run.name = 'a'
        run.finish_message, run.publish_last = AsyncMock(), AsyncMock()
        run.steering.close = AsyncMock()
        prompts = []

        async def codex(run, prompt):
            prompts.append(prompt)
            if len(prompts) == 1:
                raise native.ThreadBusy(BUSY)
        terminate = AsyncMock()
        entry = {'channel': 'general', 'chain': 'c', 'requests': [{'human': True, 'chain': 'c', 'prompt': 'hi'}]}
        with patch('room_runtime.Run', return_value=run), patch('room_runtime.native.codex', codex), \
             patch('room_runtime.native.terminate', terminate), patch('room_runtime.native.restore_room_server'):
            await r.run_turn('a', entry)
        self.assertEqual(prompts, ['new since last turn', 'full room context'])
        self.assertEqual(run.previous_session, {})
        self.assertEqual(r.states['a']['state'], 'ready')
        r.project_room.complete.assert_called_once_with(run)
        self.assertEqual(r.activity_note.await_args.args[1], 'new conversation')
        self.assertGreaterEqual(terminate.await_count, 2)  # the busy child, then the normal end of turn
        r.host.store.add.assert_not_called()  # nothing in chat


if __name__ == '__main__':
    unittest.main()
