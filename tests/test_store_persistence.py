import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zerocollab.storage.store import MessageStore


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'messages.jsonl'
        self.store = MessageStore(str(self.path))
        self.first = self.store.add('user', 'first', channel='work')
        self.last = self.store.add('agent', 'last', channel='work')

    def test_failed_replacement_preserves_disk_memory_and_callbacks(self):
        original = self.path.read_bytes()
        events = []
        self.store.on_delete(events.append)
        import os
        real_replace = os.replace

        def fail_log(source, target):
            if Path(target) == self.path:
                raise OSError('simulated disk failure')
            return real_replace(source, target)

        operations = [
            lambda: self.store.update_message(0, {'text': 'changed'}),
            lambda: self.store.update_reply_to(1, 0),
            lambda: self.store.delete([1]),
            lambda: self.store.clear(),
            lambda: self.store.clear('work'),
            lambda: self.store.rename_channel('work', 'renamed'),
            lambda: self.store.rename_sender('user', 'renamed'),
            lambda: self.store.delete_channel('work'),
        ]
        for operation in operations:
            with self.subTest(operation=operation), mock.patch('zerocollab.storage.store.os.replace', side_effect=fail_log):
                with self.assertRaises(OSError):
                    operation()
            self.assertEqual(self.path.read_bytes(), original)
            self.assertEqual(self.store.get_recent(), [self.first, self.last])
            self.assertEqual(list(self.path.parent.glob('*.tmp')), [])
        self.assertEqual(events, [])

    def test_serialization_failure_preserves_original(self):
        original = self.path.read_bytes()
        with self.assertRaises(TypeError):
            self.store.update_message(0, {'text': object()})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.store.get_by_id(0)['text'], 'first')

    def test_restart_never_reuses_deleted_ids(self):
        self.store.delete([self.last['id']])
        reloaded = MessageStore(str(self.path))
        self.assertGreater(reloaded.add('user', 'after delete')['id'], self.last['id'])
        highest = reloaded.last_id
        reloaded.clear()
        reloaded = MessageStore(str(self.path))
        self.assertGreater(reloaded.add('user', 'after clear')['id'], highest)

    def test_bulk_flush_preserves_high_water(self):
        message = self.store.add('user', 'imported', _bulk=True)
        self.store.flush_bulk()
        self.store.delete_channel('general')
        self.assertGreater(MessageStore(str(self.path)).add('user', 'next')['id'], message['id'])

    def test_delete_retains_shared_upload(self):
        self.store.upload_dir = self.path.parent / 'uploads'
        self.store.upload_dir.mkdir()
        image = self.store.upload_dir / 'shared.png'
        image.write_bytes(b'image')
        message = self.store.add('user', 'evidence', attachments=[{'url': '/uploads/shared.png'}])
        self.store.delete([message['id'], message['id']])
        self.assertEqual(image.read_bytes(), b'image')
        self.assertIsNone(self.store.get_by_id(message['id']))

    def test_successful_edits_survive_restart(self):
        self.store.update_message(0, {'text': 'changed'})
        self.store.update_reply_to(1, 0)
        self.store.rename_channel('work', 'new')
        self.store.rename_sender('user', 'human')
        records = MessageStore(str(self.path)).get_recent()
        self.assertEqual(records[0]['text'], 'changed')
        self.assertEqual(records[0]['sender'], 'human')
        self.assertEqual(records[1]['reply_to'], 0)
        self.assertEqual(records[1]['channel'], 'new')


if __name__ == '__main__':
    unittest.main()
