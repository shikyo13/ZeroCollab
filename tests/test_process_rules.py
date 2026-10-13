"""Process rules the room enforces itself (Docs/process-enforcement.md, task a7da2f10, Zero #16116)."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import process_enforcement
import room_runtime
from coordination_records import RuleRefused
from project_room import RELEASE_GRANTEE, SCOPE_REVIEW_ROUNDS, ProjectRoom


class Rules(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.runtime = SimpleNamespace(data=root / 'data', host=SimpleNamespace(router=None),
            states={name: {'worktree': str(root)} for name in ('worker', 'peer', 'other')},
            activity=lambda name: {'running': False, 'queued': 0})
        self.room = ProjectRoom(self.runtime)
        self.room.use_project('test')
        self.first = self.room.task('worker', 'create', title='First')['id']
        self.second = self.room.task('worker', 'create', title='Second')['id']

    def status(self, task_id):
        return next(t for t in self.room.state['tasks'] if t['id'] == task_id)['status']


class OneWorkingTask(Rules):
    """E1: an agent can't move a second task into working."""

    def test_second_working_task_is_refused_by_update_and_claim(self):
        self.room.task('worker', 'update', self.first, status='working')
        with self.assertRaisesRegex(ValueError, f'E1: you already have {self.first} working'):
            self.room.task('worker', 'update', self.second, status='working')
        with self.assertRaisesRegex(ValueError, 'E1'):
            self.room.task('worker', 'claim', self.second)
        self.assertEqual(self.status(self.second), 'open')

    def test_reclaiming_moving_aside_and_other_agents_pass(self):
        self.room.task('worker', 'claim', self.first)
        self.room.task('worker', 'claim', self.first)  # already working: not a new transition
        self.room.task('worker', 'update', self.first, update='still on it')
        self.room.task('other', 'claim', self.room.task('other', 'create', title='Theirs')['id'])
        self.room.task('worker', 'update', self.first, status='review')
        self.room.task('worker', 'update', self.second, status='working')
        self.assertEqual((self.status(self.first), self.status(self.second)), ('review', 'working'))

    def test_the_user_is_never_refused(self):
        self.room.task('worker', 'update', self.first, status='working')
        self.room.task('user', 'update', self.second, status='working')
        self.assertEqual(self.status(self.second), 'working')


class ScopeCap(Rules):
    """E5: past SCOPE_REVIEW_ROUNDS distinct revisions, an agent's new review request waits for the user."""

    def request(self, revision, reviewer='peer', actor='worker'):
        return self.room.records.review(actor, 'request', task_id=self.first, reviewer=reviewer,
                                        revision=revision, text='check')

    def fill(self):
        for n in range(SCOPE_REVIEW_ROUNDS):
            self.request(f'r{n}')

    def bell(self):
        return [f for f in self.room.state.get('followups', []) if f.get('kind') == 'scope_cap']

    def test_round_nine_is_refused_once_with_one_bell_item(self):
        self.fill()
        self.request('r0', reviewer='other')  # a parallel review of a counted revision is the same round
        with self.assertRaises(RuleRefused) as first:
            self.request('r8')
        self.assertIn(f'has had {SCOPE_REVIEW_ROUNDS} review rounds', str(first.exception))
        item = first.exception.record
        self.assertEqual((item['created_by'], item['assigned_to'], item['task_id']), ('system', 'user', self.first))
        with self.assertRaises(RuleRefused) as again:
            self.request('r9')
        self.assertIsNone(again.exception.record)  # still open: no second item
        self.assertEqual(len(self.bell()), 1)
        self.request('r8', actor='user')  # the user is never refused
        pending = next(r for r in self.room.state['reviews'] if r['reviewer'] == 'other' and r['status'] == 'pending')
        self.room.records.review('other', 'respond', review_id=pending['id'], revision='r0', verdict='approved',
                                 text='ok')  # verdicts are never refused, even past the cap

    def test_only_the_user_answers_and_resolve_grants_eight_more(self):
        self.fill()
        with self.assertRaises(RuleRefused) as refused:
            self.request('r8')
        item = refused.exception.record
        for actor, action in (('worker', 'resolve'), ('peer', 'cancel')):
            with self.assertRaisesRegex(ValueError, 'only the user answers'):
                self.room.records.followup(actor, action, followup_id=item['id'], result='go on')
        with self.assertRaisesRegex(ValueError, 'only the user answers'):
            self.room.records.resolve_followups('worker', [item['id']], 'go on')
        result = self.room.records.followup('user', 'resolve', followup_id=item['id'], result='continue')
        self.assertEqual(result['notify'], ['system'])  # the runtime skips delivery to the room itself
        for n in range(SCOPE_REVIEW_ROUNDS, 2 * SCOPE_REVIEW_ROUNDS):
            self.request(f'r{n}')
        with self.assertRaises(RuleRefused) as next_window:
            self.request('r99')
        self.assertNotEqual(next_window.exception.record['id'], item['id'])  # a new window, a new item

    def test_cancel_stops_new_requests_without_another_item(self):
        self.fill()
        with self.assertRaises(RuleRefused) as refused:
            self.request('r8')
        self.room.records.followup('user', 'cancel', followup_id=refused.exception.record['id'], result='stop')
        with self.assertRaisesRegex(ValueError, 'E5: stopped by the user'):
            self.request('r9')
        self.assertEqual(len(self.bell()), 1)
        self.request('r9', actor='user')


class ReleaseTrainSeams(unittest.TestCase):
    """E3's backend seams: the real delivery_signals drives process_enforcement.tick, and the room never wakes itself."""

    def test_real_signals_open_one_item_for_the_release_manager(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        deploy = root / 'deploy'
        deploy.mkdir()
        (deploy / 'current.json').write_text('{}', 'utf-8')
        os.utime(deploy / 'current.json', (time.time() - 3 * 3600,) * 2)
        chat, woken = [], []
        runtime = SimpleNamespace(
            data=root / 'data', identity='test', host=SimpleNamespace(router=None, store=SimpleNamespace(
                add=lambda sender, text, **kw: chat.append(sender))),
            states={name: {'worktree': str(root)} for name in (RELEASE_GRANTEE, 'peer')},
            activity=lambda name: {'running': False, 'queued': 0},
            ready_wakes=SimpleNamespace(room_gate=lambda: ''),
            deployment=SimpleNamespace(root=deploy, reload=lambda: {'admission': 'open'},
                                       enter_mutation=lambda: True, leave_mutation=lambda: None),
            deliver_record=lambda recipient, items, channel, chain, text, ref: woken.append(recipient),
            owns_channel=lambda channel: True, default_channel=lambda: 'main',
            decisions=SimpleNamespace(routes=lambda channel: True), manager_wakes=SimpleNamespace(record={}))
        room = runtime.project_room = ProjectRoom(runtime)
        room.use_project('test')
        task = room.task('peer', 'create', title='Visible fix', labels=['frontend'])['id']
        room.records.review('peer', 'request', task_id=task, reviewer=RELEASE_GRANTEE, revision='v1', text='check')
        review = room.state['reviews'][-1]
        room.records.review(RELEASE_GRANTEE, 'respond', review_id=review['id'], revision='v1', verdict='approved', text='ok')
        self.assertEqual(process_enforcement.tick(runtime), ['opened'])
        item = next(f for f in room.state['followups'] if f['created_by'] == 'system')
        self.assertEqual((item['assigned_to'], woken, chat), (RELEASE_GRANTEE, [RELEASE_GRANTEE], ['system']))
        self.assertIn(task, item['text'])

    def test_the_room_never_wakes_itself(self):
        result = room_runtime.RoomRuntime.deliver_record(None, 'system', [], 'main', None, 'text', {})
        self.assertEqual(result['status'], 'skipped')


if __name__ == '__main__':
    unittest.main()
