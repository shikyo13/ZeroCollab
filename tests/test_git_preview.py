import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from git_preview import capture, ERROR_BYTES
from room_runtime import _worktree_diff


class GitPreviewTests(unittest.TestCase):
    def test_both_large_pipes_are_drained_with_capped_retention(self):
        code = "import sys; sys.stdout.write('x'*2000000); sys.stderr.write('y'*2000000)"
        self.assertEqual(capture([sys.executable, '-c', code], limit=127), 'x' * 127)

    def test_error_retention_is_bounded(self):
        code = "import sys; sys.stderr.write('e'*2000000); sys.exit(1)"
        with self.assertRaises(RuntimeError) as caught:
            capture([sys.executable, '-c', code])
        self.assertEqual(len(str(caught.exception)), ERROR_BYTES)

    def test_timeout_kills_child_and_reaps_pipes(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            capture([sys.executable, '-c', 'import time; time.sleep(10)'], timeout=.1)

    def test_real_worktree_and_space_prefixed_untracked_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def git(*args):
                subprocess.run(['git', '-C', folder, *args], check=True, capture_output=True)
            git('init')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-m', 'base')
            (root / ' spaced.txt').write_text('untracked content')
            self.assertIn('New file  spaced.txt:\nuntracked content', _worktree_diff(folder, 'api'))
            (root / 'tracked.txt').write_text('base\n')
            git('add', 'tracked.txt')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'file')
            (root / 'tracked.txt').write_text('large change\n' * 20000)
            self.assertEqual(len(_worktree_diff(folder, 'api')), 24000)
