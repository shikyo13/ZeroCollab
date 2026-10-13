"""Focused blockers for the S5 page/worker integration, without launching a browser."""
import ast
import asyncio
import base64
import hashlib
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import motion_scene
import motion_studio as studio
from motion_render import ChromiumWorker, FrameClock, RenderIdentity


def scene():
    return {'version': 2, 'id': 'test', 'compositions': [{
        'id': 'main', 'width': 640, 'height': 360, 'fps': {'num': 30000, 'den': 1001},
        'duration': 6, 'tracks': [{'id': 'visual', 'kind': 'visual', 'clips': [{
            'id': 'clip', 'from': 0, 'duration': 6,
            'layers': [{'id': 'title', 'type': 'text', 'text': 'Frame accurate'}]}]}]}]}


class PageChecks(unittest.TestCase):
    def test_exact_legacy_html_and_migration(self):
        # Compare against the pre-integration builder, using the same approved scripts.
        code = subprocess.check_output(['git', 'show', 'fe65344:motion_studio.py']).decode()
        tree = ast.parse(code)
        old = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'view_html')
        scope = dict(vars(studio))
        exec(compile(ast.Module(body=[old], type_ignores=[]), '<old view_html>', 'exec'), scope)
        v1 = studio.load(studio.BUNDLED / 'm01.json')
        opts = {'render': True, 'reduced': False}
        self.assertEqual(studio.view_html(v1, opts), scope['view_html'](v1, opts))
        self.assertEqual(studio.view_html(motion_scene.upgrade(v1), opts), studio.view_html(v1, opts))

    def test_v2_geometry_clock_and_script_contract(self):
        s = scene()
        self.assertEqual(motion_scene.issues(s), [])
        plan = studio.render_plan(s)
        self.assertEqual((plan['width'], plan['height'], plan['duration']), (640, 360, 6))
        self.assertEqual(plan['fps'], {'num': 30000, 'den': 1001})
        page = studio.view_html(s, {'render': True})
        self.assertIn('"frame_input"', page)
        self.assertIn('window.__seekFrame = r.seekFrame', page)
        csp = re.search('http-equiv="Content-Security-Policy" content="([^"]+)"', page).group(1)
        self.assertNotIn('media-src', csp)
        self.assertNotIn('font-src', csp)
        self.assertIn("return new Promise", page)
        self.assertTrue(studio.summary('project:test', 'project', s)['renderable'])

    def test_assets_inlined_hashed_bounded_and_confined(self):
        with tempfile.TemporaryDirectory() as tmp:
            uploads = Path(tmp)
            (uploads / 'shot.png').write_bytes(b'example bytes')
            runtime = SimpleNamespace(host=SimpleNamespace(store=SimpleNamespace(upload_dir=uploads)))
            s = scene()
            s['assets'] = [{'id': 'shot', 'kind': 'capture', 'ref': '/uploads/shot.png'}]
            s['compositions'][0]['tracks'][0]['clips'][0]['layers'] = [
                {'id': 'capture', 'type': 'capture', 'asset': 'shot', 'label': 'synthetic'}]
            frame = studio.resolved_frame_input(s, 'main', runtime)
            self.assertEqual(frame['assets'][0]['sha256'], hashlib.sha256(b'example bytes').hexdigest())
            self.assertEqual(base64.b64decode(frame['assets'][0]['src'].split(',')[1]), b'example bytes')
            s['assets'][0]['sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                studio.resolved_frame_input(s, 'main', runtime)
            del s['assets'][0]['sha256']
            with patch.object(studio, 'MAX_INLINE_ASSET_BYTES', 2):
                with self.assertRaisesRegex(ValueError, '64 MiB'):
                    studio.resolved_frame_input(s, 'main', runtime)
            s['assets'][0]['ref'] = 'motion:a/../../outside.png'
            with self.assertRaisesRegex(ValueError, 'Asset ref'):
                studio.resolved_frame_input(s, 'main', runtime)

    def test_video_only_adds_media_csp(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'movie.webm').write_bytes(b'media bytes')
            runtime = SimpleNamespace(host=SimpleNamespace(store=SimpleNamespace(upload_dir=Path(tmp))))
            s = scene()
            s['assets'] = [{'id': 'movie', 'kind': 'video', 'ref': '/uploads/movie.webm'}]
            s['compositions'][0]['tracks'][0]['clips'][0]['layers'] = [
                {'id': 'video', 'type': 'video', 'asset': 'movie', 'muted': True}]
            self.assertIn('media-src data:', studio.view_html(s, {'render': True}, runtime))


class WorkerChecks(unittest.IsolatedAsyncioTestCase):
    async def test_integer_seek_and_rational_preview_endpoint_clamp(self):
        h = 'a' * 64
        identity = RenderIdentity('main', h, h, h, 640, 360, FrameClock(30000, 1001))
        worker = ChromiumWorker('page.html', 'profile', identity, lambda: None, frame_seek=True)
        worker.duration = 6
        calls = []
        async def evaluate(expr, wait=False): calls.append(expr)
        async def screenshot(): return b'png'
        worker.evaluate = evaluate
        worker.screenshot = screenshot
        self.assertEqual(await worker.capture(3), b'png')
        await worker.capture_ms(100.1)
        await worker.capture_ms(6 * 1000 * 1001 / 30000)
        self.assertEqual(calls, ['window.__seekFrame(3)', 'window.__seekFrame(3)', 'window.__seekFrame(5)'])

    async def test_start_uses_v2_plan_and_refuses_unimplemented_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = SimpleNamespace(identity='test', data=Path(tmp))
            exporter = studio.Exporter(runtime)
            s = scene()
            encoder = {'path': 'ffmpeg', 'version': 'fixture', 'license': 'LGPL',
                       'formats': {'webm': {'available': True}}}
            async def run(*args): pass
            with patch.object(studio, 'catalogue', return_value=({'project:test': ('project', s)}, [])), \
                    patch.object(exporter, 'run', side_effect=run):
                job = exporter.start('project:test', False, encoder=encoder)
                self.assertEqual(job['fps'], {'num': 30000, 'den': 1001})
                self.assertEqual((job['frames'], job['width'], job['height']), (6, 640, 360))
                await exporter.task
                exporter.current = None
                s['compositions'][0]['tracks'][0]['clips'][0]['layers'].append(
                    {'id': 'audio', 'type': 'audio', 'asset': 'sound'})
                with self.assertRaisesRegex(ValueError, 'Audio mixing'):
                    exporter.start('project:test', False, encoder=encoder)


if __name__ == '__main__':
    unittest.main()
