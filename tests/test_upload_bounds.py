import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import UploadFile
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
            self.assertTrue(response.headers['content-disposition'].startswith('attachment'))

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
