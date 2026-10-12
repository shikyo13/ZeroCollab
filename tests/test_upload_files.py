"""Agents see where a dropped video or file is on disk (harness 014e8523, Zero #14495)."""
import tempfile
import unittest
from pathlib import Path

from project_room import message_text


class UploadFileLines(unittest.TestCase):
    def setUp(self):
        self.uploads = Path(tempfile.mkdtemp())
        (self.uploads / '1a2b3c4d.pdf').write_bytes(b'x' * 2048)
        (self.uploads / '5e6f7a8b.webm').write_bytes(b'v' * 10)
        (self.uploads / '9c0d1e2f.png').write_bytes(b'p')
        (self.uploads.parent / 'secret.txt').write_text('no')

    def text(self, body, attachments=()):
        return message_text({'id': 7, 'sender': 'Zero', 'text': body, 'attachments': list(attachments)},
                            'http://127.0.0.1:8300', self.uploads)

    def test_attachment_and_pasted_link(self):
        out = self.text('see http://127.0.0.1:8300/uploads/5e6f7a8b.webm.',
                        [{'url': '/uploads/1a2b3c4d.pdf', 'name': 'spec.pdf'}])
        self.assertIn(f"File: spec.pdf is at {(self.uploads / '1a2b3c4d.pdf').resolve()} (file, 2.0 KB)", out)
        self.assertIn(f"File: 5e6f7a8b.webm is at {(self.uploads / '5e6f7a8b.webm').resolve()} (video, 10 bytes)", out)

    def test_images_missing_and_outside_files_add_nothing(self):
        out = self.text('/uploads/9c0d1e2f.png /uploads/00000000.zip /uploads/../secret.txt /uploads/..secret.txt')
        self.assertNotIn('File:', out)

    def test_without_upload_folder_is_unchanged(self):
        message = {'id': 7, 'sender': 'Zero', 'text': '/uploads/1a2b3c4d.pdf', 'attachments': []}
        self.assertEqual(message_text(message, ''), '#7 Zero:\n/uploads/1a2b3c4d.pdf')


if __name__ == '__main__':
    unittest.main()
