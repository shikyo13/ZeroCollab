"""Closing merged or parked work without claiming delivery (opus 56d0015c, Zero #15381)."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from zerocollab.coordination.project_room import ProjectRoom


class TaskClosure(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = SimpleNamespace(data=self.root / 'data', host=SimpleNamespace(router=None),
            states={name: {'worktree': str(self.root)} for name in ('astra', 'opus')},
            activity=lambda name: {'running': False, 'queued': 0})
        self.room = ProjectRoom(self.runtime)
        self.room.use_project('test')
        (self.root / 'source.py').write_text('original source', encoding='utf-8')
        self.old = self.room.task('astra', 'create', title='Old demo task', paths=['source.py'])['id']
        self.new = self.room.task('astra', 'create', title='Merged demo task')['id']

    def stale_approval(self):
        item = self.room.records.review('astra', 'request', task_id=self.old, reviewer='opus',
                                        revision='v1', text='Check source')['item']
        self.room.records.review('opus', 'respond', review_id=item['id'], revision='v1',
                                 verdict='approved', text='checked')
        (self.root / 'source.py').write_text('changed later', encoding='utf-8')

    def test_superseded_closes_past_a_stale_approval_while_done_still_refuses(self):
        self.stale_approval()
        with self.assertRaises(ValueError):
            self.room.task('astra', 'update', self.old, status='done')
        view = self.room.task('astra', 'update', self.old, close_reason='superseded', close_pointer=self.new)
        self.assertEqual(view['status'], 'done')
        self.assertEqual((view['closure']['reason'], view['closure']['pointer']), ('superseded', self.new))
        self.assertIn(f'closed as superseded: {self.new}', view['updates'][-1]['text'])

    def test_closure_moves_dependents_and_cancels_open_requests(self):
        waiting = self.room.task('astra', 'create', title='Waits on old', depends_on=[self.old])['id']
        review = self.room.records.review('astra', 'request', task_id=self.old, reviewer='opus',
                                          revision='v2', text='Pending check')['item']
        follow = self.room.records.followup('astra', 'create', task_id=self.old,
                                            assigned_to='opus', text='Pending question')['item']
        self.room.task('astra', 'update', self.old, close_reason='duplicate', close_pointer=self.new)
        moved = next(t for t in self.room.state['tasks'] if t['id'] == waiting)
        self.assertEqual(moved['depends_on'], [self.new])
        self.assertEqual(review['status'], 'cancelled')
        self.assertEqual(follow['status'], 'cancelled')

    def test_refusals(self):
        self.room.task('astra', 'create', title='Waits on old', depends_on=[self.old])
        cases = [dict(close_reason='finished', close_pointer=self.new),           # unknown reason
                 dict(close_reason='superseded', close_pointer='nope'),           # pointer must exist
                 dict(close_reason='superseded', close_pointer=self.old),         # not itself
                 dict(close_reason='parked', close_pointer=self.new),             # dependents need a carrier
                 dict(close_reason='superseded', close_pointer=self.new, status='done')]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                self.room.task('astra', 'update', self.old, **case)
        with self.assertRaises(ValueError):  # only the owner or the user closes
            self.room.task('opus', 'update', self.old, close_reason='duplicate', close_pointer=self.new)
        self.assertEqual(next(t for t in self.room.state['tasks'] if t['id'] == self.old)['status'], 'open')

    def test_closure_clears_a_pending_transfer(self):
        self.room.task('astra', 'delegate', self.old, owner='opus')
        view = self.room.task('astra', 'update', self.old, close_reason='superseded', close_pointer=self.new)
        self.assertNotIn('transfer', view)
        self.assertNotIn('wait', view)

    def test_parked_points_at_a_decision_and_reopening_clears_it(self):
        decision = self.room.note('opus', 'decision', 'Park the desktop app track')['id']
        self.room.task('astra', 'update', self.old, close_reason='parked', close_pointer=decision)
        view = self.room.task('astra', 'update', self.old, status='open')
        self.assertNotIn('closure', view)


if __name__ == '__main__':
    unittest.main()
