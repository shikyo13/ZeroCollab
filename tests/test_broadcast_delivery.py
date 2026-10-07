import asyncio
import unittest
import weakref
from unittest.mock import AsyncMock, Mock, patch

import app


class BroadcastDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.enterContext(patch.object(app, 'ws_clients', set()))
        self.enterContext(patch.object(app, '_ws_send_locks', weakref.WeakKeyDictionary()))
        self.enterContext(patch.object(app, 'WS_SEND_TIMEOUT_SECONDS', .1))
        self.enterContext(patch.object(app, 'WS_CLOSE_TIMEOUT_SECONDS', .1))

    async def test_fast_client_receives_while_slow_client_is_still_waiting(self):
        waiting = asyncio.Event()
        fast_received = asyncio.Event()
        async def stalled(message):
            waiting.set()
            await asyncio.Event().wait()
        async def fast_send(message):
            fast_received.set()
        slow = Mock(send_text=stalled, close=AsyncMock())
        fast = Mock(send_text=fast_send, close=AsyncMock())
        app.ws_clients.update([slow, fast])
        task = asyncio.create_task(app._broadcast('message'))
        await asyncio.wait_for(waiting.wait(), 1)
        await asyncio.wait_for(fast_received.wait(), 1)
        self.assertFalse(task.done())
        await asyncio.wait_for(task, 1)
        self.assertEqual(app.ws_clients, {fast})
        slow.close.assert_awaited_once_with(code=1013)

    async def test_overlapping_broadcasts_preserve_socket_order(self):
        entered, release = asyncio.Event(), asyncio.Event()
        received = []
        async def send(message):
            if message == 'first':
                entered.set()
                await release.wait()
            received.append(message)
        client = Mock(send_text=send, close=AsyncMock())
        app.ws_clients.add(client)
        first = asyncio.create_task(app._broadcast('first'))
        await entered.wait()
        second = asyncio.create_task(app._broadcast('second'))
        await asyncio.sleep(0)
        self.assertEqual(received, [])
        release.set()
        await asyncio.gather(first, second)
        self.assertEqual(received, ['first', 'second'])

    async def test_failed_client_is_skipped_by_queued_broadcast(self):
        client = Mock(send_text=AsyncMock(side_effect=OSError('disconnected')), close=AsyncMock())
        app.ws_clients.add(client)
        await asyncio.gather(app._broadcast('first'), app._broadcast('second'))
        self.assertEqual(client.send_text.await_count, 1)
        self.assertEqual(client.close.await_count, 1)
        self.assertFalse(app.ws_clients)
