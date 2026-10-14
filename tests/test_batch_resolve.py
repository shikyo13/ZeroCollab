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
        # Each requester owns the open task its follow-up is on, so its result wakes it (task e14159ba part 5).
        self.state = {'tasks': [{'id': 't1', 'owner': 'backend', 'status': 'working', 'updates': [], 'updated': 0},
                                {'id': 't2', 'owner': 'harness', 'status': 'working', 'updates': [], 'updated': 0}],
                      'followups': [followup('f1', 'backend'), followup('f2', 'harness', 't2'), followup('f3', 'backend')]}
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

    def test_an_agents_cancel_wakes_nobody_and_the_users_still_does(self):
        # task f530bd08: cancelling review 6459b3dd started an empty turn for a reviewer who was never woken.
        self.state['tasks'][0]['owner'] = 'opus'
        self.state['reviews'] = [{'id': 'r1', 'task_id': 't1', 'requested_by': 'backend', 'reviewer': 'harness',
                                  'status': 'pending', 'updated': 1}]
        self.runtime.profiles = SimpleNamespace(recipient_arguments=lambda arguments: arguments)

        def cancel(actor, tool, **arguments):
            return asyncio.run(self.runtime.coordination_record(actor, tool, dict(arguments, action='cancel'), 'chat', 'chain'))

        out = cancel('backend', 'room_review', review_id='r1', text='probe')
        self.assertEqual(out['deliveries']['harness']['status'], 'deferred')
        out = cancel('backend', 'room_followup', followup_id='f1', result='not needed')
        self.assertEqual(out['deliveries']['astra']['status'], 'deferred')
        self.assertEqual(self.woken, [])
        cancel('user', 'room_followup', followup_id='f2', result='not needed')
        self.assertEqual(self.woken, ['astra'])
        self.assertEqual(len(self.lines), 3)  # every cancel still posts its chat line

    def test_a_delivery_names_its_skill_only_when_the_profile_pins_it(self):
        # harness d69d12c5: profiled agents answered 23 reviews and never loaded room-review.
        import agent_skills
        pinned = {'name': 'room-review', 'sha256': agent_skills.read_skill('room-review').sha256}
        self.runtime.profiles = SimpleNamespace(items={'harness': {'core': 'role', 'approved_by': 'user',
                                                                   'approved_at': 1, 'skills': [pinned]}})
        texts = []
        self.runtime.notify_participant = lambda name, channel, chain, text, ref: texts.append((name, text)) or {}
        l1 = {'id': 'f9', 'text': 'L1 live check: release 0.12.61 (1ac7fb4be38c) is installed.'}
        for name, item, collection in (('harness', {'id': 'r1'}, 'reviews'), ('harness', l1, 'followups'),
                                       ('harness', followup('f1', 'backend'), 'followups'), ('opus', {'id': 'r1'}, 'reviews')):
            self.runtime.deliver_record(name, [item], 'chat', 'chain', 'news', {'collection': collection})
        self.assertIn('Matching skill: room-review.', texts[0][1])
        for name, text in texts[1:]:  # room-release isn't pinned, a plain follow-up matches nothing, opus has no profile
            self.assertNotIn('Matching skill', text)
        # harness d07fb1eb: the lead's binding item matches room-release too, as an owner's live check does.
        pinned = {'name': 'room-release', 'sha256': agent_skills.read_skill('room-release').sha256}
        self.runtime.profiles.items['harness']['skills'] = [pinned]
        lead = {'id': 'f8', 'text': 'L1 release 0.12.62 (d0963f1b25af) is installed, and its live checks need you: ...'}
        for item in (l1, lead):
            self.runtime.deliver_record('harness', [item], 'chat', 'chain', 'news', {'collection': 'followups'})
        self.assertTrue(all('Matching skill: room-release.' in text for _, text in texts[-2:]))


if __name__ == '__main__':
    unittest.main()
