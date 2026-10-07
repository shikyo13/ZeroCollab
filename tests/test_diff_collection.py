import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from room_runtime import RoomRuntime


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

        with patch('room_runtime._worktree_diff', side_effect=collect):
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
