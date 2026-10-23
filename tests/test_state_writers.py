import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import zerocollab.api.mcp_bridge as mcp_bridge
from zerocollab.coordination.project_room import write_json
from zerocollab.providers.provider_controls import ProviderControls
from zerocollab.coordination.room_goals import RoomGoals


class StateWriterTests(unittest.TestCase):
    def test_state_writers_preserve_previous_bytes_on_replace_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            goals = RoomGoals(SimpleNamespace(project_room=SimpleNamespace(chains={})))
            goals.path, goals.goal = path, {'chain': 'c', 'status': 'paused'}
            controls = object.__new__(ProviderControls)
            controls.path, controls.values = path, {}
            controls.validate = Mock()
            controls.runtime = SimpleNamespace(profiles=Mock())
            writers = [lambda: write_json(path, {'value': 'é'}), goals.save,
                       lambda: controls.change('a', 'model', 'effort')]
            for writer in writers:
                with self.subTest(writer=writer):
                    path.write_text('{"old": true}', encoding='utf-8')
                    with patch('zerocollab.storage.durable.os.replace', side_effect=OSError('disk failure')):
                        with self.assertRaises(OSError):
                            writer()
                    self.assertEqual(json.loads(path.read_text()), {'old': True})
                    self.assertEqual(list(path.parent.glob('*.tmp')), [])
                    writer()
                    self.assertNotEqual(json.loads(path.read_text('utf-8')), {'old': True})

    def test_cursors_log_failed_save_and_keep_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cursors.json'
            with patch.object(mcp_bridge, '_CURSORS_FILE', path), \
                 patch.object(mcp_bridge, '_cursors', {'a': {'general': 4}}):
                mcp_bridge._save_cursors()
                mcp_bridge._cursors['a']['general'] = 5
                with patch('zerocollab.storage.durable.os.replace', side_effect=OSError('disk failure')), \
                     self.assertLogs(mcp_bridge.log, level='WARNING'):
                    mcp_bridge._save_cursors()
                self.assertEqual(json.loads(path.read_text()), {'a': {'general': 4}})
                mcp_bridge._save_cursors()
                self.assertEqual(json.loads(path.read_text()), {'a': {'general': 5}})
