import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import UploadFile
from starlette.datastructures import Headers
import app
import archive

class UploadBoundsTests(unittest.TestCase):
    def test_oversized_image_reads_only_limit_plus_one_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            source = io.BytesIO(b'x' * 100)
            file = UploadFile(filename='test.png', file=source)
            with patch.object(app, 'config', {'images': {'upload_dir': root, 'max_size_mb': 16 / (1024 * 1024)}}):
                response = asyncio.run(app.upload_image(file))
            self.assertEqual(response.status_code, 400)
            self.assertEqual(source.tell(), 17)
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_exact_image_limit_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            content = b'x' * 16
            with patch.object(app, 'config', {'images': {'upload_dir': root, 'max_size_mb': 16 / (1024 * 1024)}}):
                response = asyncio.run(app.upload_image(UploadFile(filename='test.png', file=io.BytesIO(content))))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(next(Path(root).iterdir()).read_bytes(), content)

    def upload(self, root, filename, content, content_type=None, **limits):
        headers = Headers({'content-type': content_type}) if content_type else None
        source = io.BytesIO(content)
        with patch.object(app, 'config', {'images': {'upload_dir': root, **limits}}):
            response = asyncio.run(app.upload_image(UploadFile(filename=filename, file=source, headers=headers)))
        return response, json.loads(response.body), source

    def test_video_and_file_uploads_say_how_to_share_them(self):
        # Dropped or pasted into chat: images embed, videos play inline, anything else is shared as a link.
        with tempfile.TemporaryDirectory() as root:
            response, video, _ = self.upload(root, 'Demo Cut.MP4', b'\x00\x00\x00 ftypisom')
            self.assertEqual((response.status_code, video['kind'], video['type'], video['size']), (200, 'video', 'video/mp4', 12))
            self.assertRegex(video['url'], r'^/uploads/[0-9a-f]{16}\.mp4$')
            self.assertEqual(self.serve(root, video['url'].split('/')[-1]).media_type, 'video/mp4')
            _, file, _ = self.upload(root, 'notes.ZIP', b'PK\x03\x04')
            self.assertEqual((file['kind'], file['type'], file['name']), ('file', 'application/octet-stream', 'notes.ZIP'))
            self.assertTrue(self.serve(root, file['url'].split('/')[-1]).headers['content-disposition'].startswith('attachment'))
            _, image, _ = self.upload(root, 'shot.png', b'\x89PNG')
            self.assertEqual((image['kind'], image['type']), ('image', 'image/png'))

    def test_file_names_never_choose_an_unsafe_suffix(self):
        with tempfile.TemporaryDirectory() as root:
            _, odd, _ = self.upload(root, 'report.t%r gz', b'x')
            self.assertTrue(odd['url'].endswith('.bin') and odd['kind'] == 'file', odd)
            _, page, _ = self.upload(root, 'page.HTML', b'<script>')
            self.assertTrue(page['url'].endswith('.html') and page['kind'] == 'file', page)
            _, pasted, _ = self.upload(root, '', b'\x89PNG', content_type='image/png')
            self.assertTrue(pasted['url'].endswith('.png') and pasted['kind'] == 'image', pasted)
            _, bare, _ = self.upload(root, 'Makefile', b'all:')
            self.assertTrue(bare['url'].endswith('.bin') and bare['kind'] == 'file', bare)

    def test_the_same_bytes_uploaded_twice_are_stored_once(self):
        # Zero #14949: no pile of duplicate copies. The stored name is content-addressed.
        with tempfile.TemporaryDirectory() as root:
            _, first, _ = self.upload(root, 'clip.webm', b'same bytes')
            _, again, _ = self.upload(root, 'renamed copy.webm', b'same bytes')
            _, other, _ = self.upload(root, 'clip.webm', b'different bytes')
            self.assertEqual(first['url'], again['url'])
            self.assertEqual((first['deduplicated'], again['deduplicated']), (False, True))
            self.assertNotEqual(first['url'], other['url'])
            self.assertEqual(sorted(p.name for p in Path(root).iterdir()),
                             sorted([first['url'].split('/')[-1], other['url'].split('/')[-1]]))
            self.assertEqual(again['name'], 'renamed copy.webm')  # the name the user dropped is kept

    def test_files_have_their_own_larger_limit_and_oversized_writes_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            tiny = 16 / (1024 * 1024)
            response, body, source = self.upload(root, 'big.webm', b'x' * 100, max_file_mb=tiny)
            self.assertEqual(response.status_code, 400)
            self.assertIn('too large', body['error'])
            self.assertEqual(source.tell(), 17)
            self.assertEqual(list(Path(root).iterdir()), [])
            # An image limit doesn't apply to files, and a file limit doesn't apply to images.
            response, body, _ = self.upload(root, 'clip.webm', b'x' * 100, max_size_mb=tiny)
            self.assertEqual((response.status_code, body['size']), (200, 100))
            with patch.object(app, 'UPLOAD_CHUNK', 7):  # several chunks reassemble exactly
                response, body, _ = self.upload(root, 'data.csv', bytes(range(50)), max_file_mb=50 / (1024 * 1024))
            self.assertEqual(Path(root, body['url'].split('/')[-1]).read_bytes(), bytes(range(50)))

    def serve(self, root, name):
        with patch.object(app, 'config', {'images': {'upload_dir': root}}):
            return asyncio.run(app.serve_upload(name))

    def test_uploaded_svg_is_served_sandboxed(self):
        # An SVG opened as a page must not run script with the room's session token.
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'evil.SVG').write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>fetch("/")</script></svg>')
            response = self.serve(root, 'evil.SVG')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.media_type, 'image/svg+xml')
            self.assertIn('sandbox', response.headers['content-security-policy'])
            self.assertIn("default-src 'none'", response.headers['content-security-policy'])
            self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
            self.assertNotIn('content-disposition', response.headers)  # inline: previews keep working

    def test_raster_upload_keeps_its_image_type(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'shot.jpg').write_bytes(b'\xff\xd8\xff')
            response = self.serve(root, 'shot.jpg')
            self.assertEqual(response.media_type, 'image/jpeg')
            self.assertIn('sandbox', response.headers['content-security-policy'])

    def test_non_image_upload_is_only_downloaded(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'page.html').write_text('<script>fetch("/")</script>')
            response = self.serve(root, 'page.html')
            self.assertEqual(response.media_type, 'application/octet-stream')
            # A bare "attachment": no filename, so the chat link's download="<original name>" names the file.
            self.assertEqual(response.headers['content-disposition'], 'attachment')

    def test_missing_or_directory_upload_is_not_found(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'folder').mkdir()
            self.assertEqual(self.serve(root, 'folder').status_code, 404)
            self.assertEqual(self.serve(root, 'absent.png').status_code, 404)

    def test_oversized_archive_rejected_before_import(self):
        source = io.BytesIO(b'x' * 100)
        with patch.object(archive, 'MAX_IMPORT_SIZE', 16), patch.object(archive, 'import_archive') as importer:
            response = asyncio.run(app.import_history(UploadFile(filename='test.zip', file=source)))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(source.tell(), 17)
        importer.assert_not_called()
