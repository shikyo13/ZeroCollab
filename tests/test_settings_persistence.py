import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class SettingsPersistenceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'nested' / 'settings.json'
        self.enterContext(patch.object(app, '_settings_path', return_value=self.path))
        self.enterContext(patch.object(app, 'room_settings', {
            'title': 'ZeroCollab', 'subtitle': 'Private room', 'channels': ['general', 'dev']}))

    def test_save_and_reload(self):
        app._save_settings()
        saved = dict(app.room_settings)
        app.room_settings.clear()
        app._load_settings()
        self.assertEqual(app.room_settings, saved)
        self.assertEqual(json.loads(self.path.read_text('utf-8')), saved)

    def test_failed_replace_preserves_previous_file(self):
        app._save_settings()
        previous = self.path.read_bytes()
        app.room_settings['subtitle'] = 'New value'
        with patch('durable.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                app._save_settings()
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])
