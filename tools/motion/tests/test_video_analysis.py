"""Focused checks for video_analysis's pure parts, its bounds and cancellation. QA test code."""
import shutil
import subprocess
import threading
import time
import unittest
from unittest import mock

import zerocollab_motion.analysis as va

FFMPEG = shutil.which('ffmpeg')
SLOW_SOURCE = 'testsrc2=size=1280x720:rate=30:duration=600'   # ten minutes of frames: runs far longer than the test
CANCEL_AFTER_S = 0.5
KILL_GRACE_S = 1.0


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


class FinishingProcess:
    """A Popen stand-in whose communicate sets the caller's stop and then finishes successfully: the race
    where a cancel lands just as the process completes."""
    def __init__(self, stop):
        self.stop, self.returncode = stop, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def communicate(self, data=None, timeout=None):
        self.stop.set()
        self.returncode = 0
        return 'output', ''

    def poll(self):
        return self.returncode

    def kill(self):
        pass


class CancelRaceTests(unittest.TestCase):
    def test_stop_while_finishing_still_cancels(self):
        stop = threading.Event()
        with mock.patch.object(va.subprocess, 'Popen', lambda *a, **k: FinishingProcess(stop)):
            with self.assertRaises(va.Cancelled):
                va._run(['ffmpeg', '-version'], stop)

    def test_stop_during_final_pass_caches_nothing(self):
        va._cache.clear()
        stop = threading.Event()
        info = {'duration': 1.0, 'audio_codec': 'aac'}
        passes = []

        def last_pass(*args, **kwargs):
            if not passes:
                stop.set()    # the first run is cancelled while its last pass finishes; the pass returns normally
            passes.append(1)
            return {}

        with mock.patch.multiple(va, probe=lambda *a, **k: info, frame_motion=lambda *a, **k: [0.0],
                                 scene_scores=lambda *a, **k: [], loudness=last_pass):
            with self.assertRaises(va.Cancelled):
                va.analyze('ffmpeg', __file__, stop=stop)
            self.assertEqual(len(va._cache), 0)
            stop.clear()
            va.analyze('ffmpeg', __file__, stop=stop)     # completes and is cached once nothing cancels it
            self.assertEqual(len(va._cache), 1)
            stop.set()
            with self.assertRaises(va.Cancelled):          # a cancelled job gets no cached result either
                va.analyze('ffmpeg', __file__, stop=stop)
        va._cache.clear()


@unittest.skipUnless(FFMPEG, 'needs ffmpeg on PATH')
class CancelTests(unittest.TestCase):
    def test_stop_kills_running_ffmpeg_and_starts_nothing_new(self):
        started, real = [], subprocess.Popen

        def spy(*args, **kwargs):
            started.append(real(*args, **kwargs))
            return started[-1]

        stop = threading.Event()
        timer = threading.Timer(CANCEL_AFTER_S, stop.set)
        with mock.patch.object(va.subprocess, 'Popen', spy):
            timer.start()
            began = time.monotonic()
            with self.assertRaises(va.Cancelled):
                va._ffmpeg(FFMPEG, '-f', 'lavfi', '-i', SLOW_SOURCE, '-f', 'null', '-', stop=stop)
            elapsed = time.monotonic() - began
            with self.assertRaises(va.Cancelled):
                va._ffmpeg(FFMPEG, '-version', stop=stop)
        self.assertLess(elapsed, CANCEL_AFTER_S + va.POLL_S + KILL_GRACE_S)
        self.assertEqual(len(started), 1)              # nothing starts once the event is set
        self.assertIsNotNone(started[0].returncode)    # killed and reaped, not left running

    def test_cancelled_analysis_is_not_cached(self):
        va._cache.clear()
        stop = threading.Event()
        stop.set()
        with self.assertRaises(va.Cancelled):
            va.analyze(FFMPEG, __file__, stop=stop)
        self.assertEqual(len(va._cache), 0)

    def test_timeout_still_kills_with_or_without_stop(self):
        for stop in (None, threading.Event()):
            with mock.patch.object(va, 'TIMEOUT', CANCEL_AFTER_S), self.assertRaises(subprocess.TimeoutExpired):
                va._ffmpeg(FFMPEG, '-f', 'lavfi', '-i', SLOW_SOURCE, '-f', 'null', '-', stop=stop)

    def test_without_stop_behaves_as_before(self):
        self.assertEqual(va._ffmpeg(FFMPEG, '-version').returncode, 0)
        with self.assertRaises(RuntimeError):
            va._ffmpeg(FFMPEG, '-loglevel', 'error', '-i', 'no-such-input.mp4', '-f', 'null', '-')


if __name__ == '__main__':
    unittest.main()
