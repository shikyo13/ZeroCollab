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

    def test_oversized_archive_rejected_before_import(self):
        source = io.BytesIO(b'x' * 100)
        with patch.object(archive, 'MAX_IMPORT_SIZE', 16), patch.object(archive, 'import_archive') as importer:
            response = asyncio.run(app.import_history(UploadFile(filename='test.zip', file=source)))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(source.tell(), 17)
        importer.assert_not_called()
