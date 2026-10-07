import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from room_runtime import load_workspace, save_workspace

class WorkspacePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'workspace.json'
        self.old = {'project': 'first', 'project_id': 'one', 'paused': False}
        self.new = {'project': 'second', 'project_id': 'two', 'paused': False}

    def test_roundtrip_and_backup(self):
        self.assertEqual(load_workspace(self.path), {})
        save_workspace(self.path, self.old)
        save_workspace(self.path, self.new)
        self.assertEqual(load_workspace(self.path), self.new)
        self.assertEqual(json.loads(self.path.with_suffix('.json.bak').read_text()), self.old)

    def test_failed_replace_keeps_selection(self):
        save_workspace(self.path, self.old)
        with patch('room_runtime.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                save_workspace(self.path, self.new)
        self.assertEqual(load_workspace(self.path), self.old)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_corrupt_primary_recovers_paused_and_keeps_backup(self):
        save_workspace(self.path, self.old)
        save_workspace(self.path, self.new)
        self.path.write_text('{torn')
        with self.assertLogs('room_runtime', level='WARNING'):
            recovered = load_workspace(self.path)
        self.assertEqual(recovered, {**self.old, 'paused': True})
        save_workspace(self.path, recovered)
        self.assertEqual(json.loads(self.path.with_suffix('.json.bak').read_text()), self.old)

    def test_invalid_both_fails_explicitly(self):
        self.path.write_text('null')
        with self.assertRaisesRegex(RuntimeError, 'Cannot recover workspace'):
            load_workspace(self.path)

    def test_invalid_project_shape_recovers(self):
        save_workspace(self.path, self.old)
        save_workspace(self.path, self.new)
        self.path.write_text('{"project": 123}')
        with self.assertLogs('room_runtime', level='WARNING'):
            self.assertTrue(load_workspace(self.path)['paused'])
