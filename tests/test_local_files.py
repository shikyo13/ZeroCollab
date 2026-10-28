"""Files referenced in place by path (Zero #14949): parsing pasted references, the registry, and the routes
that register and serve the original without copying it."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import zerocollab.api.app as app
import zerocollab.storage.local_files as local_files


class Request:
    """Just enough of a Starlette request for reference_file."""

    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


class ReferenceParsing(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.file = self.root / 'Demo Cut.mp4'
        self.file.write_bytes(b'\x00\x00\x00 ftyp')

    def test_pasted_forms_resolve_to_the_same_file(self):
        forms = [str(self.file), f'"{self.file}"', self.file.as_posix(), self.file.as_uri()]
        self.assertEqual({local_files.resolve(form) for form in forms}, {self.file.resolve()})

    def test_unc_uri_becomes_a_unc_path(self):
        path = local_files.as_path('file://server/share/videos/demo.mp4')
        self.assertEqual(str(path), r'\\server\share\videos\demo.mp4')

    def test_refused_references(self):
        for bad, why in ((r'\\.\PhysicalDrive0', 'Device'), (r'\\?\C:\x.mp4', 'Device'),
                         ('demo.mp4', 'full path'), (str(self.root / 'NUL'), 'reserved'),
                         ('', 'at most'), (str(self.root), 'not a regular file'),
                         (str(self.root / 'missing.mp4'), 'No file')):
            with self.assertRaisesRegex(ValueError, why, msg=bad):
                local_files.resolve(bad)


class RegistryAndRoutes(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.file = self.root / 'm2 demo.mp4'
        self.file.write_bytes(b'\x00\x00\x00 ftypisom video')
        self.uploads = self.root / 'uploads'
        self.uploads.mkdir()
        self.config = {'server': {'data_dir': str(self.root / 'data')}, 'images': {'upload_dir': str(self.uploads)}}
        local_files._shared.clear()

    def tearDown(self):
        local_files._shared.clear()

    def run_route(self, coroutine):
        with patch.object(app, 'config', self.config):
            return asyncio.run(coroutine)

    def test_register_returns_an_attachment_that_serves_the_original_in_place(self):
        response = self.run_route(app.reference_file(Request({'ref': f'"{self.file}"'})))
        body = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual((body['name'], body['kind'], body['type'], body['size']),
                         ('m2 demo.mp4', 'video', 'video/mp4', self.file.stat().st_size))
        self.assertEqual(body['path'], str(self.file.resolve()))
        self.assertEqual(body['url'], f"/api/files/ref/{body['id']}/m2%20demo.mp4")
        self.assertEqual(list(self.uploads.iterdir()), [])  # nothing copied
        served = self.run_route(app.serve_referenced_file(body['id'], 'm2 demo.mp4'))
        self.assertEqual((served.status_code, served.media_type, Path(served.path)), (200, 'video/mp4', self.file.resolve()))
        self.assertNotIn('x-file-changed', served.headers)

    def test_same_file_same_id_and_the_registry_persists(self):
        first = json.loads(self.run_route(app.reference_file(Request({'ref': str(self.file)}))).body)
        local_files._shared.clear()  # a restarted room reads the registry back from disk
        again = json.loads(self.run_route(app.reference_file(Request({'ref': self.file.as_uri()}))).body)
        self.assertEqual(first['id'], again['id'])
        self.assertEqual(self.run_route(app.serve_referenced_file(first['id'], 'x')).status_code, 200)

    def test_changed_gone_and_unknown(self):
        key = json.loads(self.run_route(app.reference_file(Request({'ref': str(self.file)}))).body)['id']
        self.file.write_bytes(b'edited and longer than before')
        changed = self.run_route(app.serve_referenced_file(key, 'x'))
        self.assertEqual((changed.status_code, changed.headers.get('x-file-changed')), (200, '1'))
        self.file.unlink()
        gone = self.run_route(app.serve_referenced_file(key, 'x'))
        self.assertEqual(gone.status_code, 404)
        self.assertIn('gone', json.loads(gone.body)['error'])
        unknown = self.run_route(app.serve_referenced_file('0123456789abcdef', 'x'))
        self.assertEqual(unknown.status_code, 404)

    def test_bad_references_are_400_with_the_reason(self):
        for body in ({'ref': 'relative.mp4'}, {'ref': str(self.root)}, {}, ['not an object']):
            response = self.run_route(app.reference_file(Request(body)))
            self.assertEqual(response.status_code, 400, body)
            self.assertTrue(json.loads(response.body)['error'])

    def test_a_non_media_original_downloads_under_its_real_name(self):
        doc = self.root / 'Q3 plan.pdf'
        doc.write_bytes(b'%PDF-1.7')
        key = json.loads(self.run_route(app.reference_file(Request({'ref': str(doc)}))).body)['id']
        served = self.run_route(app.serve_referenced_file(key, 'Q3 plan.pdf'))
        self.assertEqual(served.media_type, 'application/octet-stream')
        self.assertIn('attachment', served.headers['content-disposition'])
        self.assertIn('Q3%20plan.pdf', served.headers['content-disposition'])
        self.assertIn('sandbox', served.headers['content-security-policy'])

    def test_registry_forgets_oldest_past_its_cap_without_touching_files(self):
        registry = local_files.Registry(self.root / 'refs.json')
        files = []
        for n in range(4):
            f = self.root / f'f{n}.txt'
            f.write_text(str(n))
            files.append(f)
        with patch.object(local_files, 'MAX_REGISTERED', 3):
            keys = [registry.register(str(f))[0] for f in files]
        self.assertNotIn(keys[0], registry.entries)
        self.assertTrue(all(f.exists() for f in files))


if __name__ == '__main__':
    unittest.main()
