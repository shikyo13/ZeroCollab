import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from motion_encode import PROFILES, inventory, missing, output_args, profile


class FormatChecks(unittest.TestCase):
    def test_unknown_format_and_audio_refused(self):
        for value in ('libx264', '../../movie', {}, None):
            with self.assertRaises(ValueError):
                profile(value)
        for value in ('png', 'gif', 'png-sequence'):
            with self.assertRaises(ValueError):
                output_args(value, audio=True)

    def test_missing_build_dependencies_are_explicit(self):
        for name in PROFILES:
            self.assertTrue(missing(name, {}))
        caps = {'encoders': {'gif'}, 'decoders': {'png'}, 'demuxers': {'image2pipe'},
                'muxers': {'gif'}, 'filters': {'split', 'palettegen'}}
        self.assertEqual(missing('gif', caps), ['filters: paletteuse'])

    def test_sequence_and_alpha_profiles_are_distinct(self):
        self.assertEqual(profile('png-sequence').extension, 'zip')
        self.assertTrue(profile('png-sequence').sequence)
        self.assertFalse(profile('webm').alpha)
        self.assertTrue(profile('vp9-alpha').alpha)
        self.assertIn('yuva420p', output_args('vp9-alpha'))
        self.assertIn('yuva444p10le', output_args('prores4444'))
        self.assertNotIn('-an', output_args('av1', audio=True))
        self.assertIn('libopus', output_args('av1', audio=True))

    def test_inventory_reads_flags_without_substrings(self):
        names = inventory(' V....D libvpx-vp9 description\n V....D libaom-av1 description\n')
        self.assertIn('libvpx-vp9', names)
        self.assertNotIn('vp9', names)

    def test_format_specific_authority_does_not_require_vp9_for_av1(self):
        import motion_studio as studio
        caps = {'missing': ['encoder libvpx-vp9'],
                'formats': {'av1': {'available': True}, 'webm': {'available': False}}}
        with patch.object(studio, 'ffmpeg_path', return_value='encoder'), \
                patch.object(studio, 'encoder_check', return_value=caps), \
                patch.object(studio.shared_browser, 'executable', return_value='browser'):
            self.assertIs(studio.require_tools(None, 'av1'), caps)
            with self.assertRaises(studio.Unavailable):
                studio.require_tools(None, 'webm')


class AdmissionChecks(unittest.IsolatedAsyncioTestCase):
    async def prepared(self, during, *, mock_start=False):
        import motion_studio as studio
        exporter = studio.Exporter(SimpleNamespace(identity='original'))
        entered, release = threading.Event(), threading.Event()
        loop_thread = threading.get_ident()
        capability = {'formats': {'webm': {'available': True}}}

        def probe(*args):
            self.assertNotEqual(threading.get_ident(), loop_thread)
            entered.set()
            if not release.wait(.5):
                raise AssertionError('room loop did not release the blocked probe')
            return capability

        with patch.object(studio, 'require_tools', side_effect=probe):
            if mock_start:
                with patch.object(exporter, 'start', return_value={'id': 'new'}) as start:
                    task = asyncio.create_task(exporter.prepare_start('bundled:m01', False))
                    self.assertTrue(await asyncio.to_thread(entered.wait, .3))
                    # A different room-loop operation runs while the cold probe waits.
                    await asyncio.sleep(0)
                    during(exporter)
                    release.set()
                    result = await task
                    start.assert_called_once_with('bundled:m01', False, 'webm', encoder=capability)
                    return result
            task = asyncio.create_task(exporter.prepare_start('bundled:m01', False))
            self.assertTrue(await asyncio.to_thread(entered.wait, .3))
            during(exporter)
            release.set()
            return await task

    async def test_cold_probe_leaves_room_loop_free_and_returns_to_atomic_start(self):
        result = await self.prepared(lambda exporter: None, mock_start=True)
        self.assertEqual(result, {'id': 'new'})

    async def test_changed_project_during_probe_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'Project changed'):
            await self.prepared(lambda e: setattr(e.runtime, 'identity', 'another'))

    async def test_new_export_or_preview_during_probe_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'already running'):
            await self.prepared(lambda e: setattr(e, 'current', {'phase': 'rendering'}))
        with self.assertRaisesRegex(RuntimeError, 'preview frame'):
            await self.prepared(lambda e: setattr(e, 'framing', 'frontend'))


if __name__ == '__main__':
    unittest.main()
