"""At the idle, writes-blocked acknowledgement the room saves every record to room.db, not only what touched saves
wrote, so a user or desktop update gate loses nothing when the room stops (task 3438154b)."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import roomdb  # noqa: E402
from room_deploy import checkpoint_blocked  # noqa: E402


class Room:
    """ProjectRoom.save's shape: the live state, saved through a real roomdb.Store."""
    def __init__(self, folder):
        self.store, self.state = roomdb.Store.open(folder)

    def save(self, touched=None):
        self.store.save(self.state, touched)


class GateFullSaveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.room = Room(self.folder)
        self.room.state['tasks'] = [{'id': 'a', 'title': 'first'}]
        self.room.save()
        self.runtime = SimpleNamespace(project_room=self.room, save_deploy_queue=mock.Mock())

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_record_a_touched_save_skipped_is_on_disk_after_the_gate(self):
        self.room.state['tasks'][0]['title'] = 'changed but not named'
        with mock.patch.object(roomdb, 'VERIFY', False):
            self.room.save(touched=())  # the change is left for the next full comparison
        self.assertEqual(roomdb.read_state(self.folder)['tasks'][0]['title'], 'first')
        checkpoint_blocked(self.runtime, {'seq': 7})
        self.assertEqual(roomdb.read_state(self.folder)['tasks'][0]['title'], 'changed but not named')
        self.runtime.save_deploy_queue.assert_called_once()

    def test_once_per_gate_seq_and_a_failed_save_is_not_acknowledged(self):
        save = mock.Mock(side_effect=self.room.save)
        self.room.save = save
        checkpoint_blocked(self.runtime, {'seq': 7})
        checkpoint_blocked(self.runtime, {'seq': 7})  # the supervisor polls while it waits
        self.assertEqual(save.call_count, 1)
        save.side_effect = OSError('disk full')
        with self.assertRaises(OSError):
            checkpoint_blocked(self.runtime, {'seq': 8})
        self.assertEqual(self.runtime.save_deploy_queue.call_count, 2)  # not after the failed save


if __name__ == '__main__':
    unittest.main()
