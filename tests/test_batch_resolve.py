"""One result for several follow-ups posts one chat line (harness retro 1b999d3b, Opus ea1cd426)."""
import asyncio
import unittest
from types import SimpleNamespace

from coordination_records import CoordinationRecords
from room_runtime import RoomRuntime


def followup(identifier, created_by, task_id='t1'):
    return {'id': identifier, 'task_id': task_id, 'created_by': created_by, 'assigned_to': 'astra',
            'text': 'stage it', 'status': 'open', 'result': '', 'created': 1, 'updated': 1}


class BatchResolve(unittest.TestCase):
    def setUp(self):
        self.state = {'tasks': [{'id': 't1', 'updates': [], 'updated': 0}],
                      'followups': [followup('f1', 'backend'), followup('f2', 'harness'), followup('f3', 'backend')]}
        room = SimpleNamespace(state=self.state, save=lambda: None, bump_work=lambda *a: None)
        records = object.__new__(CoordinationRecords)
        records.room = room
        room.records = records
        self.lines, self.woken = [], []
        self.runtime = object.__new__(RoomRuntime)
        self.runtime.project_room = room
        self.runtime.host = SimpleNamespace(store=SimpleNamespace(add=lambda *a, **k: self.lines.append((a, k))))
        self.runtime.notify_participant = lambda name, *a: self.woken.append(name) or {'status': 'queued'}
        self.runtime.ready_wakes = SimpleNamespace(schedule=lambda: None)
        self.runtime.project_state_soon = lambda: None
        self.runtime.live = None

    def resolve(self, ids, actor='astra'):
        return asyncio.run(self.runtime.resolve_followups(actor, ids, 'STAGED 0.12.47 package abc', 'chat', 'chain'))

    def test_three_records_one_chat_line_each_requester_told_once(self):
        out = self.resolve(['f1', 'f2', 'f3'])
        self.assertEqual([f['status'] for f in self.state['followups']], ['resolved'] * 3)
        self.assertEqual(len(self.lines), 1)
        self.assertIn('Follow-ups f1, f2, f3 resolved by astra', self.lines[0][0][1])
        self.assertEqual(sorted(self.woken), ['backend', 'harness'])
        self.assertEqual(len(self.state['tasks'][0]['updates']), 1)
        self.assertTrue(out['changed'])

    def test_a_bad_id_refuses_the_whole_call(self):
        for ids, actor in ((['f1', 'nope'], 'astra'), (['f1', 'f2'], 'design')):
            with self.assertRaises(ValueError):
                self.resolve(ids, actor)
        self.assertEqual({f['status'] for f in self.state['followups']}, {'open'})
        self.assertEqual(self.lines, [])

    def test_planning_manager_result_is_deferred_not_woken(self):
        # backend 7c9ce73b: same rule as a single resolve, through the shared deliver_record.
        self.state['followups'][0]['planning_manager'] = 'backend'
        out = self.resolve(['f1', 'f2', 'f3'])
        self.assertEqual(out['deliveries']['backend']['status'], 'deferred')
        self.assertEqual(self.woken, ['harness'])
        self.assertEqual(len(self.lines), 1)

    def test_already_closed_are_skipped_and_nothing_closed_posts_nothing(self):
        self.state['followups'][0]['status'] = 'resolved'
        self.resolve(['f1', 'f2'])
        self.assertIn('Follow-ups f2 resolved', self.lines[0][0][1])
        self.lines.clear()
        self.assertFalse(self.resolve(['f1', 'f2'])['changed'])
        self.assertEqual(self.lines, [])


if __name__ == '__main__':
    unittest.main()
