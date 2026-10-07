import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from registry import RuntimeRegistry


class RegistryPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = RuntimeRegistry(self.temp.name)

    def test_concurrent_save_captures_state_after_previous_writer(self):
        first_writing = threading.Event()
        release_first = threading.Event()
        second_finished = threading.Event()
        real_replace = os.replace
        writes = []

        def paused_replace(source, target):
            writes.append(json.loads(Path(source).read_text()))
            if len(writes) == 1:
                first_writing.set()
                if not release_first.wait(3):
                    raise AssertionError('test writer timed out')
            return real_replace(source, target)

        self.registry._renames = {'old': 'first'}
        with mock.patch('durable.os.replace', side_effect=paused_replace):
            first = threading.Thread(target=self.registry._save_renames)
            def save_second():
                self.registry._save_renames()
                second_finished.set()
            second = threading.Thread(target=save_second)
            first.start()
            try:
                self.assertTrue(first_writing.wait(3))
                with self.registry._lock:
                    self.registry._renames['old'] = 'second'
                second.start()
                self.assertFalse(second_finished.wait(.05))
            finally:
                release_first.set()
                first.join(3)
                if second.ident is not None:
                    second.join(3)
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
        self.assertEqual(writes, [{'old': 'first'}, {'old': 'second'}])
        self.assertEqual(json.loads((self.root / 'renames.json').read_text()), {'old': 'second'})
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_failed_save_preserves_previous_file_and_reports_error(self):
        self.registry._renames = {'old': 'first'}
        self.registry._save_renames()
        original = (self.root / 'renames.json').read_bytes()
        self.registry._renames['old'] = 'second'
        with mock.patch('durable.os.replace', side_effect=PermissionError('locked')), self.assertLogs('registry', level='ERROR') as logs:
            self.registry._save_renames()
        self.assertEqual((self.root / 'renames.json').read_bytes(), original)
        self.assertIn('renames.json', logs.output[0])
        self.assertEqual(list(self.root.glob('*.tmp')), [])
        self.registry._save_renames()
        self.assertEqual(json.loads((self.root / 'renames.json').read_text()), {'old': 'second'})

    def test_managed_identity_survives_reload(self):
        self.registry.register_managed('agent', {'label': 'Agent', 'color': '#ffffff'})
        loaded = RuntimeRegistry(self.temp.name)
        self.assertEqual(loaded._reclaimable['agent'].identity_id,
                         self.registry._instances['agent'].identity_id)


if __name__ == '__main__':
    unittest.main()

