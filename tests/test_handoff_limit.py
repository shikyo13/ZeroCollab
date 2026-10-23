"""The chat @mention handoff cap is retired: handoffs always deliver, and old settings still load."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zerocollab.coordination.project_room import DEFAULT_LIMITS, ProjectRoom
from zerocollab.coordination.router import Router


class HandoffLimitRetiredTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = SimpleNamespace(data=Path(self.tmp.name), host=SimpleNamespace())

    def test_handoffs_are_never_spent(self):
        room = ProjectRoom(self.runtime)
        chain = room.new_chain('general')
        for _ in range(60):
            self.assertTrue(room.handoff(chain))
            self.assertFalse(room.handoffs_spent(chain))
        self.assertEqual(room.chains[chain]['handoffs'], 60)

    def test_retired_limits_load_and_are_ignored(self):
        (self.runtime.data / 'room_limits.json').write_text(
            json.dumps({'hops': 4, 'chat_handoffs': 10, 'agent_turns': 7}), 'utf-8')
        room = ProjectRoom(self.runtime)
        self.assertNotIn('hops', room.limits)
        self.assertNotIn('chat_handoffs', room.limits)
        self.assertEqual(room.limits['agent_turns'], 7)
        self.assertNotIn('chat_handoffs', room.change_limits({'chat_handoffs': 2}, save=False))
        self.assertNotIn('chat_handoffs', DEFAULT_LIMITS)

    def test_router_never_pauses_agent_handoffs(self):
        router = Router(['a', 'b'], default_mention='none', max_hops=4)
        for _ in range(20):
            self.assertEqual(router.get_targets('a', '@b over to you', 'general'), ['b'])
        self.assertFalse(router.is_paused('general'))


if __name__ == '__main__':
    unittest.main()
