"""Work restored after an install names a conversation the new process no longer has in memory (harness 8016e379).
It must run, not be refused as 'Conversation budget reached' and dropped; manager check-ins keep their fence."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from coordination_queue import WakeQueue
from project_room import DEFAULT_LIMITS, ProjectRoom
from room_runtime import RoomRuntime


class RestoredChainTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self):
        room = object.__new__(ProjectRoom)
        room.chains, room.current_chain, room.limits = {}, {}, dict(DEFAULT_LIMITS)
        room.runtime = SimpleNamespace()  # no manager_wakes: a check-in reservation cannot be admitted
        room.state = {'receipts': {}}
        room.complete, room.save, room.handoff = Mock(), Mock(), Mock()
        r = object.__new__(RoomRuntime)
        r.states = {'a': {'state': 'ready', 'provider': 'api'}}
        r.queues = {'a': WakeQueue(maxsize=4)}
        r.running, r.deploy_held, r.approvals, r.wake_notices, r.notice_lines = {}, {}, {}, {}, {}
        r.paused, r.deployment = False, None
        r.goals, r.team_builder, r.decisions, r.manager_wakes, r.ready_wakes = Mock(goal=None), Mock(), Mock(), Mock(), Mock()
        r.project_room = room
        r.host = Mock()
        r.publish_status, r.emit, r.activity_note, r.local_turn = AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()
        r.parallel_capacity = lambda: 4
        r.turn_admission_open = lambda: True
        r.collect_diffs = AsyncMock(return_value={})
        r.context = lambda run, changes: 'context'
        r.check_room_tools = Mock()
        r.save_project = Mock()
        r.default_channel = lambda: 'general'
        r.cfg = {'runtime': {}, 'agents': {}}
        run = Mock(id='run', channel='general', settings={}, stop_reason='', stderr='', manager_audit=None,
                   previous_session={}, stop_note='', relay_problem='')
        run.name = 'a'
        run.finish_message, run.publish_last = AsyncMock(), AsyncMock()
        run.steering.close = AsyncMock()
        return r, run

    async def turn(self, r, run, request):
        entry = {'channel': 'general', 'chain': 'lost-in-restart', 'requests': [request]}
        with patch('room_runtime.Run', return_value=run), patch('room_runtime.native.terminate', AsyncMock()), \
             patch('room_runtime.native.restore_room_server'):
            await r.run_turn('a', entry)

    async def test_restored_work_runs_on_a_fresh_record_of_its_conversation(self):
        r, run = self.runtime()
        await self.turn(r, run, {'human': False, 'chain': 'lost-in-restart', 'prompt': 'Review requested'})
        r.local_turn.assert_awaited_once()
        self.assertEqual(r.project_room.chains['lost-in-restart']['turns'], 1)
        self.assertFalse(any('budget' in str(c) for c in r.host.store.add.call_args_list))

    async def test_check_in_with_a_lost_conversation_stays_refused_and_quiet(self):
        r, run = self.runtime()
        await self.turn(r, run, {'human': False, 'chain': 'lost-in-restart', 'prompt': 'audit', 'manager_audit': 'claim'})
        r.local_turn.assert_not_awaited()
        self.assertNotIn('lost-in-restart', r.project_room.chains)
        r.host.store.add.assert_not_called()

    async def test_a_real_limit_still_refuses_with_the_budget_line(self):
        r, run = self.runtime()
        r.project_room.limits['agent_turns'] = 1
        r.project_room.chains['lost-in-restart'] = ProjectRoom.fresh_chain('lost-in-restart', 'general')
        r.project_room.chains['lost-in-restart']['turns'] = 1
        await self.turn(r, run, {'human': False, 'chain': 'lost-in-restart', 'prompt': 'more'})
        r.local_turn.assert_not_awaited()
        self.assertTrue(any('budget' in str(c) for c in r.host.store.add.call_args_list))


if __name__ == '__main__':
    unittest.main()
