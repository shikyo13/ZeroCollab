"""The app owns capture; a separately invoked CLI owns recording encoding."""
import asyncio
import io
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image
from zerocollab.browser.browser_recording_coordinator import RecordingCoordinator
from zerocollab.integrations import motion_client


class CaptureBrowser:
    def __init__(self, directory, command):
        self.project = 'fixture'
        self.agent, self.id = 'fixture-agent', 'fixture-browser'
        self.runtime = SimpleNamespace(identity=self.project, data=directory,
            cfg={'tools': {'motion': {'command': command}}})
        self.page = SimpleNamespace(id='fixture-page', width=320, height=180, recording=None)
        self.detached = 0

    def status(self):
        return {'running': True}

    async def screenshot(self, page):
        stream = io.BytesIO()
        Image.new('RGB', (320, 180), '#225588').save(stream, format='JPEG')
        return stream.getvalue()

    async def start_stream(self, page):
        page.recording.push(await self.screenshot(page))

    async def recording_detached(self, recording):
        self.page.recording = None
        self.detached += 1


class BoundaryChecks(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(importlib.util.find_spec('zerocollab_motion'), 'optional motion tool not installed')
    def test_installed_tool_fallback_bootstraps_its_own_dependencies(self):
        with patch.object(motion_client.shutil, 'which', return_value=None):
            argv = motion_client.command({})
        result = subprocess.run([sys._base_executable, *argv[1:], '--help'],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('record-encode', result.stdout)

    async def test_cancel_before_task_starts_still_finishes(self):
        with tempfile.TemporaryDirectory() as folder:
            browser = CaptureBrowser(Path(folder), ['unused'])
            recording = RecordingCoordinator(browser, browser.page, .2, 30)
            await asyncio.wait_for(recording.cancel('test'), 2)
            self.assertTrue(recording.finished)
            self.assertEqual(recording.phase, 'failed')

    async def test_missing_optional_tool_has_actionable_error(self):
        with patch.object(motion_client.shutil, 'which', return_value=None), \
             patch.object(motion_client.importlib.util, 'find_spec', return_value=None):
            with self.assertRaisesRegex(ValueError, 'Install tools/motion separately'):
                motion_client.command({})

    async def test_cancel_during_probe_joins_it_and_creates_no_capture_files(self):
        with tempfile.TemporaryDirectory() as folder:
            browser = CaptureBrowser(Path(folder), ['unused'])
            entered, joined = asyncio.Event(), asyncio.Event()
            async def probe(config):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    joined.set()
            with patch.object(motion_client, 'probe', side_effect=probe):
                recording = RecordingCoordinator(browser, browser.page, .2, 30)
                await asyncio.wait_for(entered.wait(), 2)
                await asyncio.wait_for(recording.cancel('test'), 2)
            self.assertTrue(joined.is_set())
            self.assertTrue(recording.finished)
            self.assertEqual(recording.phase, 'failed')
            self.assertFalse(list(Path(folder).iterdir()))

    @unittest.skipUnless(os.environ.get('ZEROCOLLAB_TEST_FFMPEG'), 'set explicit encoder for native recording boundary')
    async def test_capture_delegates_encoding_and_commits_playable_artifact(self):
        with tempfile.TemporaryDirectory() as folder:
            argv = [sys.executable, '-m', 'zerocollab_motion', '--ffmpeg', os.environ['ZEROCOLLAB_TEST_FFMPEG']]
            browser = CaptureBrowser(Path(folder), argv)
            recording = RecordingCoordinator(browser, browser.page, .2, 30)
            await asyncio.wait_for(asyncio.shield(recording.task), 45)
            status = recording.status(paths=True)
            self.assertEqual(status['phase'], 'done', status)
            self.assertGreater(status['frames'], 0)
            self.assertGreater(Path(status['path']).stat().st_size, 32)
            sidecar = json.loads(Path(status['sidecar_path']).read_text('utf-8'))
            self.assertEqual(sidecar['expected_frames'], status['frames'])
            self.assertEqual(sidecar['phase'], 'done')
            self.assertIsNone(browser.page.recording)
            self.assertGreater(browser.detached, 0)
            self.assertEqual({p.name for p in Path(status['path']).parent.iterdir()}, {'clip.webm', 'sidecar.json'})
