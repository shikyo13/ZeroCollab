import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zerocollab.coordination.coordination_records import CoordinationRecords
from zerocollab.coordination.project_room import ProjectRoom


class ReviewHashingTests(unittest.TestCase):
    def test_shared_path_read_once_per_response_but_next_response_is_fresh(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.py'
            path.write_bytes(b'old')
            review = {'snapshot': {'root': directory, 'files': [
                {'path': path.name, 'sha256': hashlib.sha256(b'old').hexdigest()}]}}
            room = object.__new__(ProjectRoom)
            room.state = {'tasks': [], 'reviews': [review, review]}
            room.limits, room.chains = {}, {}
            room.records = CoordinationRecords(room)
            with patch('zerocollab.coordination.coordination_records.hashlib.file_digest', wraps=hashlib.file_digest) as digest:
                result = room.snapshot()
                self.assertEqual(digest.call_count, 1)
                self.assertEqual([r['changed_files'] for r in result['reviews']], [[], []])
                stat = path.stat()
                path.write_bytes(b'new')
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # Same size and timestamp.
                result = room.snapshot()
                self.assertEqual(digest.call_count, 2)
                self.assertEqual([r['changed_files'] for r in result['reviews']], [[path.name], [path.name]])
                self.assertEqual(room.records.changed_files(review), [path.name])
                self.assertEqual(digest.call_count, 3)

    def test_missing_and_outside_files_are_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            review = {'snapshot': {'root': directory, 'files': [
                {'path': 'missing.py', 'sha256': 'none'},
                {'path': '../outside.py', 'sha256': 'none'}]}}
            self.assertEqual(CoordinationRecords(None).changed_files(review),
                             ['missing.py', '../outside.py'])
