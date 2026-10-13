"""Focused checks for video_analysis's pure parts and its bounds. QA test code."""
import unittest

import video_analysis as va


class PacingTests(unittest.TestCase):
    def test_runs(self):
        self.assertEqual(va.runs([]), [])
        self.assertEqual(va.runs([True, True, False, True, True, True]), [2, 3])

    def test_motion_profile_holds(self):
        still, moving, short_still = [0.5] * 15, [5.0] * 5, [0.1] * 8   # 1.5 s still, 0.5 s moving, 0.8 s still
        p = va.motion_profile(still + moving + short_still)
        self.assertEqual(p['holds'], 1)
        self.assertEqual(p['longest_hold_s'], 1.5)
        self.assertAlmostEqual(p['moving_share'], round(5 / 28, 3))

    def test_pacing_cuts_holds_and_transitions(self):
        # 4 s at 2 frames/s: still for 2 s, a hard cut at 2.0, a soft transition at 3.0.
        scores = [(0.0, 0.0), (0.5, 0.0), (1.0, 0.0), (1.5, 0.0), (2.0, 0.5), (2.5, 0.01), (3.0, 0.1), (3.5, 0.01)]
        p = va.pacing(scores, 4.0)
        self.assertEqual(p['cuts'], [2.0])
        self.assertEqual(p['transitions'], [3.0])
        self.assertEqual(p['shots'], 2)
        self.assertEqual(p['longest_shot_s'], 2.0)

    def test_dense_sheet_bounds(self):
        with self.assertRaises(ValueError):
            va.dense_sheet('ffmpeg', 'x.mp4', 0, 10, 10, 'out.png')   # 100 frames > DENSE_MAX_TILES
        with self.assertRaises(ValueError):
            va.dense_sheet('ffmpeg', 'x.mp4', 0, 1, 30, 'out.png')    # fps above DENSE_MAX_FPS

    def test_cache_is_bounded(self):
        va._cache.clear()
        for n in range(va.CACHE_LIMIT + 5):
            va._cache[n] = n
            while len(va._cache) > va.CACHE_LIMIT:
                va._cache.popitem(last=False)
        self.assertEqual(len(va._cache), va.CACHE_LIMIT)
        self.assertNotIn(0, va._cache)


if __name__ == '__main__':
    unittest.main()
