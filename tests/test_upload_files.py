"""Agents see where a dropped video or file is on disk (harness 014e8523, Zero #14495)."""
import tempfile
import unittest
from pathlib import Path

from zerocollab.coordination.project_room import message_text


class UploadFileLines(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.uploads = self.root / 'uploads'
        self.uploads.mkdir()
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

    def test_composer_link_keeps_its_label(self):
        out = self.text('[Q3 plan.pdf](/uploads/1a2b3c4d.pdf) and again http://127.0.0.1:8300/uploads/1a2b3c4d.pdf',
                        ['not a dict'])
        self.assertIn('File: Q3 plan.pdf is at ', out)
        self.assertEqual(out.count('File: '), 1)

    def test_images_missing_and_outside_files_add_nothing(self):
        out = self.text('/uploads/9c0d1e2f.png /uploads/00000000.zip /uploads/../secret.txt /uploads/..secret.txt')
        self.assertNotIn('File:', out)

    def test_pasted_reference_resolves_through_the_registry_only(self):
        # harness 86468487, Zero #14949: an original pasted by path is read in place; the client's path is ignored.
        import zerocollab.storage.local_files as local_files
        from zerocollab.coordination.project_room import Files
        original = self.root / 'm2 promo.mp4'
        original.write_bytes(b'm' * 3072)
        registry = local_files.Registry(self.root / 'file_references.json')
        key, _, _ = registry.register(str(original))
        url = f'/api/files/ref/{key}/m2%20promo.mp4'
        attach = {'url': url, 'name': 'm2 promo.mp4', 'path': 'C:/Windows/System32/evil.dll'}

        def text(body, attachments):
            return message_text({'id': 7, 'sender': 'Zero', 'text': body, 'attachments': attachments}, '',
                                Files(self.uploads, registry))
        out = text(f'[m2 promo.mp4]({url})', [attach])
        self.assertIn(f'File: m2 promo.mp4 is at {original} (video, 3.0 KB)', out)
        self.assertNotIn('evil.dll is at', out)
        self.assertEqual(out.count('File: '), 1)
        original.write_bytes(b'm' * 4096)  # edited after it was pasted
        self.assertIn('(video, 4.0 KB) (changed since pasted)', text('', [attach]))
        original.unlink()
        self.assertIn('File: m2 promo.mp4 is unavailable: The original file is gone', text('', [attach]))
        self.assertIn('is unavailable: Unknown file reference', text('/api/files/ref/0123456789abcdef/x.pdf', []))

    def test_original_vanishing_after_lookup_costs_one_line(self):
        # backend 236cdebc: lookup saw the file, then it went; the prompt must still build.
        from zerocollab.coordination.project_room import Files
        gone = self.root / 'gone.pdf'
        registry = type('Registry', (), {'lookup': lambda self, key: (gone, False)})()
        message = {'id': 7, 'sender': 'Zero', 'text': '',
                   'attachments': [{'url': '/api/files/ref/0123456789abcdef/gone.pdf', 'name': 'two\nlines'}]}
        out = message_text(message, '', Files(self.uploads, registry))
        # The client's name is collapsed to one line, so it can't add lines to the prompt.
        self.assertIn(f'File: two lines is unavailable: The original file is gone: {gone}', out)

    def test_without_upload_folder_is_unchanged(self):
        message = {'id': 7, 'sender': 'Zero', 'text': '/uploads/1a2b3c4d.pdf', 'attachments': []}
        self.assertEqual(message_text(message, ''), '#7 Zero:\n/uploads/1a2b3c4d.pdf')


if __name__ == '__main__':
    unittest.main()
