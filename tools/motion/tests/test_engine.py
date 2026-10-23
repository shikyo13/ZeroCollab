"""Extraction boundaries: queue ownership, cancellation and file-only previews."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from zerocollab_motion.engine import Engine
from zerocollab_motion.jobs import Cancelled


class EngineChecks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = Engine(self.folder.name)

    async def asyncTearDown(self):
        await self.engine.close()
        self.folder.cleanup()

    async def test_cancel_joins_owner_before_admitting_next_job(self):
        entered, cancelled, cleanup, second_entered = (asyncio.Event() for _ in range(4))
        async def first(job, check):
            entered.set()
            try:
                while True:
                    check()
                    await asyncio.sleep(.01)
            finally:
                cancelled.set()
                await cleanup.wait()
        async def second(job, check):
            second_entered.set()
            return {'ok': True}
        one = self.engine.start('frame', first)
        await asyncio.wait_for(entered.wait(), 2)
        two = self.engine.start('frame', second)
        with self.assertRaisesRegex(ValueError, 'generation'):
            self.engine.cancel(one['id'], one['generation'] + 1)
        self.engine.cancel(one['id'], one['generation'])
        await asyncio.wait_for(cancelled.wait(), 2)
        self.assertFalse(second_entered.is_set())
        cleanup.set()
        with self.assertRaises(Cancelled):
            await self.engine.wait(one)
        self.assertTrue((await self.engine.wait(two))['result']['ok'])
        self.assertEqual(self.engine.status(one['id'])['phase'], 'cancelled')

    async def test_close_cancels_queued_work_without_running_it(self):
        entered = asyncio.Event()
        async def first(job, check):
            entered.set()
            while True:
                check()
                await asyncio.sleep(.01)
        async def forbidden(job, check):
            self.fail('Queued job ran during shutdown')
        one = self.engine.start('frame', first)
        await asyncio.wait_for(entered.wait(), 2)
        two = self.engine.start('frame', forbidden)
        await self.engine.close()
        self.assertIsNone(self.engine.jobs.active)
        self.assertEqual(self.engine.status(one['id'])['phase'], 'cancelled')
        self.assertEqual(self.engine.status(two['id'])['phase'], 'cancelled')

    async def test_preview_is_a_self_contained_file(self):
        result = await self.engine.wait(self.engine.preview('bundled:check-moves'))
        path = Path(result['result']['path'])
        self.assertTrue(path.is_relative_to(Path(self.folder.name)))
        html = path.read_text('utf-8')
        self.assertIn('Content-Security-Policy', html)
        self.assertIn('zm-scene', html)
        self.assertNotIn('127.0.0.1', html)

    async def test_parallel_asset_imports_preserve_both_registry_entries(self):
        root = Path(self.folder.name)
        inputs = [root / 'first.png', root / 'second.png']
        for index, path in enumerate(inputs):
            path.write_bytes(bytes([index]))
        await asyncio.gather(*(asyncio.to_thread(self.engine.import_asset, path, 'asset-' + str(index))
                               for index, path in enumerate(inputs)))
        rows = json.loads((root / 'assets.json').read_text('utf-8'))
        self.assertEqual({Path(row['path']) for row in rows.values()}, set(inputs))
