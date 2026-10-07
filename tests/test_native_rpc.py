import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from native_adapters import CODEX_MESSAGE_LIMIT, Rpc


class RpcReaderTests(unittest.IsolatedAsyncioTestCase):
    def connection(self, limit):
        stream = asyncio.StreamReader(limit=limit)
        proc = SimpleNamespace(stdout=stream, stdin=SimpleNamespace(write=Mock(), drain=AsyncMock()))
        return Rpc(None, proc, AsyncMock(), AsyncMock()), stream

    async def test_image_sized_reply_exceeds_old_limit_without_losing_next_reply(self):
        rpc, stream = self.connection(CODEX_MESSAGE_LIMIT)
        try:
            first = asyncio.create_task(rpc.request('thread/read', {}))
            second = asyncio.create_task(rpc.request('second/request', {}))
            await asyncio.sleep(0)
            image = 'x' * (4 * 1024 * 1024 + 18000)
            wire = (json.dumps({'id': 1, 'result': {'image': image}}) + '\n'
                    + json.dumps({'id': 2, 'result': {'ok': True}}) + '\n').encode()
            for offset in range(0, len(wire), 65536):
                stream.feed_data(wire[offset:offset + 65536])
                await asyncio.sleep(0)
            stream.feed_eof()
            self.assertEqual((await first)['image'], image)
            self.assertEqual(await second, {'ok': True})
        finally:
            await rpc.close()

    async def test_reader_failure_preserves_cause_and_rejects_later_requests(self):
        rpc, stream = self.connection(1024)
        try:
            pending = asyncio.create_task(rpc.request('thread/resume', {}))
            await asyncio.sleep(0)
            stream.feed_data(b'x' * 2048 + b'\n')
            with self.assertRaisesRegex(RuntimeError, 'protocol reader failed.*longer than limit') as caught:
                await pending
            self.assertIsInstance(caught.exception.__cause__, ValueError)
            with self.assertRaisesRegex(RuntimeError, 'protocol reader failed'):
                await rpc.request('later/request', {})
        finally:
            await rpc.close()
