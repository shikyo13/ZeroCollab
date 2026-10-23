"""Native regression: opt in with the host's exact ZEROCOLLAB_TEST_FFMPEG path."""
import os
from pathlib import Path
import tempfile
import unittest

from zerocollab_motion.audio_media import _fixture, _probes, decode, digest, probe


ENCODER = os.environ.get('ZEROCOLLAB_TEST_FFMPEG')


@unittest.skipUnless(ENCODER, 'set ZEROCOLLAB_TEST_FFMPEG to the configured encoder')
class NativeAudioProbeChecks(unittest.IsolatedAsyncioTestCase):
    async def test_opus_webm_keeps_all_9600_samples(self):
        _probes.clear()
        with tempfile.TemporaryDirectory() as folder:
            result = await probe(ENCODER, 'webm', Path(folder))
        self.assertEqual(result['samples'], 9600)
        self.assertTrue(result['audio_probed'])

    async def test_pcm_mov_keeps_all_9600_samples(self):
        _probes.clear()
        with tempfile.TemporaryDirectory() as folder:
            result = await probe(ENCODER, 'prores4444', Path(folder))
        self.assertEqual(result['samples'], 9600)

    async def test_one_sample_over_budget_is_still_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, output = root / 'source.wav', root / 'decoded.pcm'
            _fixture(source)
            with self.assertRaisesRegex(ValueError, 'budget|oversized'):
                await decode(ENCODER, source, digest(source), output, max_bytes=9599 * 8)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
