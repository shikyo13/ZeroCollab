import asyncio
import threading
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from zerocollab.runtime.room_runtime import RoomRuntime


class DiffCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_uses_snapshot_without_blocking_loop(self):
        runtime = RoomRuntime.__new__(RoomRuntime)
        runtime.project = 'project'
        runtime.states = {'a': {'worktree': 'original', 'provider': 'api'},
                          'b': {'worktree': 'second', 'provider': 'api'}}
        started, release = threading.Event(), threading.Event()
        calls = []
        loop_thread = threading.get_ident()

        def collect(path, provider):
            self.assertNotEqual(threading.get_ident(), loop_thread)
            calls.append((path, provider))
            if path == 'original':
                started.set()
                if not release.wait(3):
                    raise AssertionError('event loop did not release worker')
            return path

        with patch('zerocollab.runtime.room_runtime._worktree_diff', side_effect=collect):
            task = asyncio.create_task(runtime.collect_diffs())
            try:
                async with asyncio.timeout(3):
                    while not started.is_set():
                        await asyncio.sleep(.01)
                runtime.states['b']['worktree'] = 'changed'
                runtime.states.clear()
            finally:
                release.set()
            self.assertEqual(await task, {'a': 'original', 'b': 'second'})
        self.assertEqual(calls, [('original', 'api'), ('second', 'api')])

    async def test_read_does_not_accept_injected_patches(self):
        runtime = RoomRuntime.__new__(RoomRuntime)
        seen = {}
        def read(run, **options):
            seen.update(options)
            return options['changes']
        runtime.project_room = SimpleNamespace(read=read)
        async def collect():
            return {'a': 'actual'}
        runtime.collect_diffs = collect
        run = SimpleNamespace(touch=lambda: None, coordination_calls=0)
        result = await runtime.coordination(run, 'room_read',
                                            {'section': 'patches', 'changes': {'a': 'fake'}})
        self.assertEqual(result, {'a': 'actual'})
        self.assertEqual(seen['section'], 'patches')

    async def test_failed_worktree_does_not_hide_later_diffs(self):
        for failure in (OSError('missing'), RuntimeError('private output'),
                        subprocess.TimeoutExpired('git', 15)):
            with self.subTest(failure=type(failure).__name__):
                runtime = RoomRuntime.__new__(RoomRuntime)
                runtime.project = 'project'
                runtime.states = {name: {'worktree': name, 'provider': 'api'}
                                  for name in ('bad', 'good')}
                with patch('zerocollab.runtime.room_runtime._worktree_diff', side_effect=[failure, 'actual diff']), \
                     self.assertLogs('room_runtime', level='WARNING'):
                    result = await runtime.collect_diffs()
                self.assertIn('unavailable', result['bad'])
                self.assertNotIn('private output', result['bad'])
                self.assertEqual(result['good'], 'actual diff')

    async def test_cancellation_is_not_a_worktree_failure(self):
        runtime = RoomRuntime.__new__(RoomRuntime)
        runtime.project = 'project'
        runtime.states = {'a': {'worktree': 'a', 'provider': 'api'}}
        with patch('zerocollab.runtime.room_runtime.asyncio.to_thread', side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await runtime.collect_diffs()
