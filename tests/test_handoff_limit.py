"""Chat @mention handoffs are guarded; tracked reviews and follow-ups always deliver."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import room_runtime
from coordination_queue import WakeQueue
from project_room import DEFAULT_LIMITS, ProjectRoom


class Admitted(Exception):
    """Raised where run_turn starts the provider run, i.e. the wake was admitted."""


class HandoffLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.notices = []
        r = room_runtime.RoomRuntime.__new__(room_runtime.RoomRuntime)
        r.data = Path(self.tmp.name)
        r.paused = False
        r.running = {}
        r.deploy_held = {}
        r.states = {'lead': {'state': 'ready', 'provider': 'api'}}
        r.queues = {'lead': WakeQueue(maxsize=4)}
        r.host = SimpleNamespace(store=SimpleNamespace(add=lambda *a, **k: self.notices.append(a[1])))
        r.goals = SimpleNamespace(goal=None, accepts=lambda request: True)
        r.team_builder = SimpleNamespace(accepts=lambda request: True, turn_finished=lambda entry: None)
        r.publish_status = AsyncMock()
        r.emit = AsyncMock()
        r.parallel_capacity = lambda: 4
        r.turn_admission_open = lambda: True
        r.project_room = ProjectRoom(r)
        r.project_room.use_project('test')
        self.r = r

    async def turn(self, chain, **request):
        entry = {'channel': 'general', 'chain': chain,
                 'requests': [{'prompt': 'wake', 'chain': chain, 'human': False, **request}]}
        def admitted(*args):
            raise Admitted
        with patch('room_runtime.current_request', lambda *a: True), patch('room_runtime.Run', admitted):
            try:
                await self.r.run_turn('lead', entry)
            except Admitted:
                return True
        return False

    async def test_tracked_records_deliver_after_chat_mentions_are_spent(self):
        self.r.project_room.change_limits({'chat_handoffs': 2}, save=False)
        chain = self.r.project_room.new_chain('general')
        self.assertTrue(await self.turn(chain, message_id=1))
        self.assertTrue(await self.turn(chain, message_id=2))
        self.assertFalse(await self.turn(chain, message_id=3))
        self.assertIn('Chat @mention handoffs', self.notices[-1])
        for ref in ({'collection': 'reviews', 'id': 'r1', 'updated': 1, 'action': 'request'},
                    {'collection': 'followups', 'id': 'f1', 'updated': 1, 'action': 'create'},
                    {'collection': 'tasks', 'id': 't1', 'updated': 1, 'action': 'delegate'}):
            self.assertTrue(await self.turn(chain, ref=ref), ref['collection'])
        self.assertEqual(self.r.project_room.chains[chain]['handoffs'], 2)

    async def test_zero_means_no_chat_handoff_limit(self):
        self.r.project_room.change_limits({'chat_handoffs': 0}, save=False)
        chain = self.r.project_room.new_chain('general')
        for message in range(60):
            self.assertTrue(await self.turn(chain, message_id=message))
        self.assertEqual(self.r.project_room.chains[chain]['handoffs'], 60)

    def test_old_hops_setting_is_dropped_on_load(self):
        (self.r.data / 'room_limits.json').write_text(json.dumps({'hops': 4, 'agent_turns': 7}), 'utf-8')
        room = ProjectRoom(self.r)
        self.assertNotIn('hops', room.limits)
        self.assertEqual(room.limits['chat_handoffs'], DEFAULT_LIMITS['chat_handoffs'])
        self.assertEqual(room.limits['agent_turns'], 7)


if __name__ == '__main__':
    unittest.main()
