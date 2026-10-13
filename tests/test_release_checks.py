"""L1/L2 backend seams (Docs/lead-independence.md r6, task 497d7197): the room's own release checklist, one checklist
per manifest, the live-check gate on finishing or handing on a pinned task, and recorded review severity."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from project_room import ProjectRoom

MANIFEST = 'a' * 64
OLDER = 'b' * 64


class ReleaseChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.runtime = SimpleNamespace(data=root / 'data', host=SimpleNamespace(router=None),
            states={name: {'worktree': str(root)} for name in ('worker', 'peer', 'other')},
            activity=lambda name: {'running': False, 'queued': 0},
            deployment=SimpleNamespace(version={'id': 'abc123def456', 'version': '0.12.99', 'manifest_sha': MANIFEST}))
        self.room = ProjectRoom(self.runtime)
        self.room.use_project('test')
        self.shipped = self.room.task('worker', 'create', title='Shipped feature')['id']
        self.other = self.room.task('worker', 'create', title='Not shipped')['id']

    def task(self, task_id):
        return next(t for t in self.room.state['tasks'] if t['id'] == task_id)


class Binding(ReleaseChecks):
    def test_the_room_binds_as_system_and_only_once_per_manifest(self):
        note = self.room.bind_installed(MANIFEST, [self.shipped], 'Release 0.12.99 installed: live checks')
        self.assertEqual((note['by'], note['kind'], note['status']), ('system', 'decision', 'active'))
        self.assertEqual(note['release_checks'], {'manifest_sha': MANIFEST, 'tasks': [{'id': self.shipped, 'owner': 'worker'}]})
        with self.assertRaisesRegex(ValueError, f"Decision {note['id']} already binds"):
            self.room.bind_installed(MANIFEST, [self.other], 'again')
        # C1: a person's second checklist for the same manifest is refused unless it supersedes the active one.
        with self.assertRaisesRegex(ValueError, f"Decision {note['id']} already binds"):
            self.room.note('user', 'decision', 'mine', release_checks={'manifest_sha': MANIFEST, 'tasks': [self.other]})
        replaced = self.room.note('user', 'decision', 'mine', supersedes=note['id'],
                                  release_checks={'manifest_sha': MANIFEST, 'tasks': [self.shipped, self.other]})
        self.assertEqual(self.room.active_release_checks(MANIFEST)['id'], replaced['id'])
        self.room.bind_installed(OLDER, [self.other], 'another manifest binds separately')

    def test_an_agent_cannot_bind_through_note(self):
        with self.assertRaisesRegex(ValueError, "Only the room's manager or the user"):
            self.room.note('worker', 'decision', 'x', release_checks={'manifest_sha': MANIFEST, 'tasks': [self.shipped]})


class Gate(ReleaseChecks):
    def setUp(self):
        super().setUp()
        self.room.bind_installed(MANIFEST, [self.shipped], 'Release 0.12.99 installed: live checks')

    def test_a_pinned_task_is_not_finished_closed_or_handed_on_before_its_pass(self):
        for change in (dict(action='update', status='done'),
                       dict(action='update', close_reason='superseded', close_pointer=self.other),
                       dict(action='delegate', owner='peer'),
                       dict(action='release')):
            action = change.pop('action')
            with self.assertRaisesRegex(ValueError, 'L1: record your live check of 0.12.99 first'):
                self.room.task('worker', action, self.shipped, **change)
        self.assertEqual((self.task(self.shipped)['status'], self.task(self.shipped)['owner']), ('open', 'worker'))
        self.room.task('worker', 'update', self.other, status='done')  # not pinned: unaffected

    def test_a_pass_in_the_same_call_lets_it_close_and_a_failure_does_not(self):
        with self.assertRaisesRegex(ValueError, 'L1'):
            self.room.task('worker', 'update', self.shipped, status='done',
                           acceptance={'manifest_sha': MANIFEST, 'result': 'failed'})
        self.room.task('worker', 'update', self.shipped, status='done',
                       acceptance={'manifest_sha': MANIFEST, 'result': 'passed', 'text': 'checked live'})
        self.assertEqual(self.task(self.shipped)['status'], 'done')

    def test_the_user_is_never_refused_and_an_older_manifest_does_not_gate(self):
        self.room.task('user', 'delegate', self.shipped, owner='peer')
        self.assertEqual(self.task(self.shipped)['owner'], 'peer')
        self.runtime.deployment.version = {'id': 'next', 'manifest_sha': OLDER}  # a newer install, not bound yet
        self.room.task('peer', 'update', self.shipped, status='done')


class Severity(ReleaseChecks):
    def setUp(self):
        super().setUp()
        self.review = self.room.records.review('worker', 'request', task_id=self.shipped, reviewer='peer',
                                               revision='r1', text='check it')['item']

    def respond(self, verdict, **severity):
        return self.room.records.review('peer', 'respond', review_id=self.review['id'], revision='r1',
                                        text='findings', verdict=verdict, **severity)

    def test_changes_need_a_critical_and_approval_carries_notes_only(self):
        with self.assertRaisesRegex(ValueError, "L2: a finding that doesn't break anything is a note"):
            self.respond('changes_requested', notes=['style'])
        with self.assertRaisesRegex(ValueError, 'approve with notes only'):
            self.respond('approved', critical=['it crashes'])
        with self.assertRaisesRegex(ValueError, 'at most 12'):
            self.respond('approved', notes=['n'] * 13)
        item = self.respond('changes_requested', critical=['empty input returns 500'])['item']
        self.assertEqual(item['critical'], [{'text': 'empty input returns 500'}])

    def test_the_user_is_never_refused_by_l2(self):
        # harness cd540632 C1: Zero answers from the UI (/api/runtime/reviews) with plain text and no severity.
        review = self.room.records.review('worker', 'request', task_id=self.shipped, reviewer='user',
                                          revision='u1', text='your call')['item']
        self.room.task('worker', 'update', self.shipped, labels=['release-pipeline'])
        item = self.room.records.review('user', 'respond', review_id=review['id'], revision='u1',
                                        text='not like this', verdict='changes_requested')['item']
        self.assertEqual(item['status'], 'changes_requested')

    def test_full_notes_fit_one_follow_up_and_nothing_changes_first(self):
        # harness cd540632 C2: twelve notes of the full length can't fail after the verdict is applied.
        notes = [str(n) * 1000 for n in range(10)] + ['a' * 1000, 'b' * 1000]
        item = self.respond('approved', notes=notes)['item']
        self.assertEqual((item['status'], len(item['notes'])), ('approved', 12))
        follow_up, = [f for f in self.room.state['followups'] if f['created_by'] == 'system']
        self.assertTrue(follow_up['text'].endswith(f"the rest are on review {self.review['id']}"))
        self.assertLessEqual(len(follow_up['text']), 8000)

    def test_notes_become_one_system_follow_up_for_the_owner(self):
        self.respond('approved', notes=['rename x', 'add a docstring'])
        item, = [f for f in self.room.state['followups'] if f['created_by'] == 'system']
        self.assertEqual((item['assigned_to'], item['task_id']), ('worker', self.shipped))
        self.assertIn('- rename x\n- add a docstring', item['text'])

    def test_a_release_pipeline_critical_names_its_class(self):
        self.room.task('worker', 'update', self.shipped, labels=['release-pipeline'])
        with self.assertRaisesRegex(ValueError, 'names its class'):
            self.respond('changes_requested', critical=['slow'])
        with self.assertRaisesRegex(ValueError, 'class is one of'):
            self.respond('changes_requested', critical=[{'text': 'slow', 'class': 'annoying'}])
        item = self.respond('changes_requested', critical=[{'text': 'Stop is ignored mid-install', 'class': 'stop_overridden'}])['item']
        self.assertEqual(item['critical'][0]['class'], 'stop_overridden')


if __name__ == '__main__':
    unittest.main()
