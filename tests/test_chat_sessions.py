"""Project chats: only the project's own open chats are tabs; closing keeps history and stops routing."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import project_registry as registry
from coordination_queue import WakeQueue


class ChatSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        data = Path(self.tmp.name)
        folder = data / 'projects' / 'aaaaaaaa'
        folder.mkdir(parents=True)
        (folder / 'coordination.json').write_text('{}', 'utf-8')
        other = data / 'projects' / 'bbbbbbbb'
        other.mkdir(parents=True)
        (other / 'coordination.json').write_text('{}', 'utf-8')
        (other / 'project.json').write_text('{"sessions": [{"id": "cother01", "title": "Theirs"}]}', 'utf-8')
        self.r = SimpleNamespace(data=data, project='', running={},
                                 project_room=SimpleNamespace(path=folder / 'coordination.json'),
                                 host=SimpleNamespace(room_settings={'channels': ['general', 'ui-overhaul', 'cother01']}))
        self.first = registry.create_session(self.r, 'Main')
        self.second = registry.create_session(self.r, 'Side')

    def payload(self):
        return registry.sessions_payload(self.r)

    def test_tabs_are_this_projects_chats_and_old_channels_are_history(self):
        p = self.payload()
        self.assertEqual([s['title'] for s in p['sessions']], ['Main', 'Side'])
        self.assertEqual([h['id'] for h in p['history_sessions']], ['general', 'ui-overhaul'])
        self.assertEqual(p['closed_sessions'], [])

    def test_close_keeps_history_stops_routing_and_reopens(self):
        registry.open_session(self.r, self.second['id'])
        registry.close_session(self.r, self.second['id'])
        p = self.payload()
        self.assertEqual([s['id'] for s in p['sessions']], [self.first['id']])
        self.assertEqual(p['closed_sessions'], [{'id': self.second['id'], 'title': 'Side'}])
        self.assertNotIn(self.second['id'], registry.owned(self.r))
        self.assertEqual(p['last_session'], self.first['id'])
        with self.assertRaisesRegex(ValueError, 'at least one open chat'):
            registry.close_session(self.r, self.first['id'])
        # Closed chats keep their name, so a new chat can't reuse it.
        with self.assertRaisesRegex(ValueError, 'already has a chat with that name'):
            registry.create_session(self.r, 'Side')
        registry.reopen_session(self.r, self.second['id'])
        self.assertIn(self.second['id'], registry.owned(self.r))
        self.assertEqual(self.payload()['closed_sessions'], [])

    def test_cannot_close_while_an_agent_works_there_or_another_projects_chat(self):
        self.r.running = {'astra': SimpleNamespace(channel=self.second['id'])}
        with self.assertRaisesRegex(ValueError, 'astra is working in this chat'):
            registry.close_session(self.r, self.second['id'])
        with self.assertRaisesRegex(ValueError, 'open chat in the current project'):
            registry.close_session(self.r, 'cother01')
        self.r.running = {}
        # Queued, not yet taken by the worker.
        queue = WakeQueue(maxsize=4)
        queue.put_nowait({'channel': self.second['id'], 'chain': 'c', 'requests': [{'prompt': 'review', 'ref': {'id': 'r1'}}]})
        self.r.queues = {'native': queue}
        with self.assertRaisesRegex(ValueError, 'native has work waiting to start in this chat'):
            registry.close_session(self.r, self.second['id'])
        # Taken by the worker and waiting for capacity in run_turn.
        self.r.queues = {'native': WakeQueue(maxsize=4)}
        self.r.deploy_held = {'gameui': {'channel': self.second['id'], 'chain': 'c', 'requests': []}}
        with self.assertRaisesRegex(ValueError, 'gameui has work waiting to start in this chat'):
            registry.close_session(self.r, self.second['id'])
        self.r.deploy_held = {'gameui': {'channel': self.first['id'], 'chain': 'c', 'requests': []}}
        self.r.goals = SimpleNamespace(goal={'channel': self.second['id'], 'status': 'active'})
        with self.assertRaisesRegex(ValueError, 'shared goal runs in this chat'):
            registry.close_session(self.r, self.second['id'])


if __name__ == '__main__':
    unittest.main()
