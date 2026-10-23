import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from zerocollab_motion.encode import PROFILES, inventory, missing, output_args, profile


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
        import zerocollab_motion.studio as studio
        caps = {'missing': ['encoder libvpx-vp9'],
                'formats': {'av1': {'available': True}, 'webm': {'available': False}}}
        with patch.object(studio, 'ffmpeg_path', return_value='encoder'), \
                patch.object(studio, 'encoder_check', return_value=caps), \
                patch.object(studio.shared_browser, 'executable', return_value='browser'):
            self.assertIs(studio.require_tools(None, 'av1'), caps)
            with self.assertRaises(studio.Unavailable):
                studio.require_tools(None, 'webm')
