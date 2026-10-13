"""Process rules the room enforces itself (Docs/process-enforcement.md, task a7da2f10, Zero #16116)."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from project_room import ProjectRoom


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


if __name__ == '__main__':
    unittest.main()
