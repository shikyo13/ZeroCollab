"""runtime_events.jsonl starts a new file past its size limit and keeps the old one (harness a10204ae)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import room_runtime
from room_runtime import RoomRuntime


class EventLogTests(unittest.TestCase):
    def test_rotates_past_the_limit_and_keeps_earlier_events(self):
        folder = Path(tempfile.mkdtemp())
        r = object.__new__(RoomRuntime)
        r.audit = folder / 'runtime_events.jsonl'
        with patch.object(room_runtime, 'EVENT_LOG_LIMIT', 200):
            for i in range(40):
                r.append_event(json.dumps({'type': 'runtime_tool', 'n': i}) + '\n')
        files = sorted(folder.glob('runtime_events*.jsonl'))
        self.assertGreater(len(files), 1)  # rotated at least once
        lines = [json.loads(l)['n'] for f in files for l in f.read_text('utf-8').splitlines()]
        self.assertEqual(sorted(lines), list(range(40)))  # nothing lost or duplicated
        self.assertLess(r.audit.stat().st_size, 200 + 64)  # the live file stays small

    def test_a_file_held_open_elsewhere_keeps_appending(self):
        folder = Path(tempfile.mkdtemp())
        r = object.__new__(RoomRuntime)
        r.audit = folder / 'runtime_events.jsonl'
        r.audit.write_text('x' * 300 + '\n', 'utf-8')
        with patch.object(room_runtime, 'EVENT_LOG_LIMIT', 200), \
             patch.object(Path, 'replace', side_effect=PermissionError('in use')):
            r.append_event('{"n": 1}\n')
        self.assertTrue(r.audit.read_text('utf-8').endswith('{"n": 1}\n'))


if __name__ == '__main__':
    unittest.main()
