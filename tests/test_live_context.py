"""Per-turn live context from the board (Docs/agent-profiles.md layer 3, opus 02fb548e)."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import project_room
from project_room import ProjectRoom


class LiveContext(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.runtime = SimpleNamespace(data=root / 'data', host=SimpleNamespace(router=None),
            states={name: {'worktree': str(root)} for name in ('lead', 'worker', 'peer')},
            activity=lambda name: {'running': False, 'queued': 0})
        self.room = ProjectRoom(self.runtime)
        self.room.use_project('test')
        self.room.state['responders'] = {'lead': 'lead'}
        self.main = self.room.task('worker', 'create', title='Build the loader', next_action='write the check')['id']
        self.room.task('worker', 'update', self.main, status='working')
        self.side = self.room.task('worker', 'create', title='Side task')['id']
        self.room.task('worker', 'update', self.side, status='working')
        self.room.records.review('worker', 'request', task_id=self.main, reviewer='peer', revision='v1', text='Check it')
        self.room.records.followup('lead', 'create', task_id=self.main, assigned_to='worker', text='Report the hash')

    def run_for(self, name, task_ids=()):
        return SimpleNamespace(name=name, task_ids=set(task_ids))

    def test_owner_sees_main_task_and_items_waiting_on_it(self):
        text = self.room.live_context(self.run_for('worker', [self.main]))
        self.assertIn(f'Main task: {self.main} [working] Build the loader; next: write the check', text)
        self.assertIn('2 are working at once, keep one main task working', text)
        self.assertIn('Follow-up', text)
        self.assertNotIn('Team builder', text)

    def test_reviewer_sees_the_request_and_lead_sees_load_and_team_builder(self):
        self.assertIn('requested by worker', self.room.live_context(self.run_for('peer')))
        lead = self.room.live_context(self.run_for('lead'))
        self.assertIn('worker 2w/2o/0r OVERLOADED', lead)
        self.assertIn('room_team_builder(action="suggest")', lead)
        self.assertIn('Main task: none owned.', lead)

    def test_lead_sees_release_train_scope_and_cycle_time(self):
        deploy = Path(self.temp.name) / 'deploy'
        deploy.mkdir()
        (deploy / 'current.json').write_text('{}', 'utf-8')
        three_hours_ago = time.time() - 3 * 3600
        os.utime(deploy / 'current.json', (three_hours_ago, three_hours_ago))
        # reload(): the maintenance gate read by the live context (release-48 0012), open here.
        self.runtime.deployment = SimpleNamespace(root=deploy, reload=lambda: {'admission': 'open'})
        self.room.task('worker', 'update', self.main, labels=['frontend'])
        review = next(r for r in self.room.state['reviews'] if r['task_id'] == self.main)
        self.room.records.review('peer', 'respond', review_id=review['id'], revision='v1', verdict='approved', text='ok')
        for n in range(project_room.SCOPE_REVIEW_ROUNDS + 1):
            self.room.records.review('worker', 'request', task_id=self.side, reviewer='peer', revision=f'r{n}', text='again')
        lead = self.room.live_context(self.run_for('lead'))
        self.assertIn('Release train: 1 open tasks approved and waiting (1 user-visible); last install 3.0 h ago. CUT A RELEASE', lead)
        self.assertIn(f'{self.side} {project_room.SCOPE_REVIEW_ROUNDS + 1} rounds', lead)
        self.assertNotIn('Release train', self.room.live_context(self.run_for('worker')))

    def test_busy_lead_keeps_release_train_and_points_to_the_inbox(self):
        # backend cf8b999d: a full inbox must cost inbox items, never the lead's delivery lines.
        self.test_lead_sees_release_train_scope_and_cycle_time()
        for n in range(project_room.LIVE_CONTEXT_ITEMS + 4):
            self.room.records.followup('peer', 'create', task_id=self.main, assigned_to='lead', text=f'ask {n} ' + 'y' * 190)
        lead = self.room.live_context(self.run_for('lead'))
        self.assertIn('CUT A RELEASE', lead)
        self.assertIn('more: room_read(section="inbox")', lead)
        self.assertNotIn('Excerpt limited', lead)
        self.assertLessEqual(len(lead), project_room.LIVE_CONTEXT_CHARS)

    def test_budget_and_overflow_pointer(self):
        for n in range(project_room.LIVE_CONTEXT_ITEMS + 3):
            self.room.records.followup('lead', 'create', task_id=self.main, assigned_to='peer', text=f'item {n} ' + 'x' * 150)
        text = self.room.live_context(self.run_for('peer'))
        self.assertIn('more: room_read(section="inbox")', text)
        self.assertLessEqual(len(text), project_room.LIVE_CONTEXT_CHARS)


if __name__ == '__main__':
    unittest.main()
