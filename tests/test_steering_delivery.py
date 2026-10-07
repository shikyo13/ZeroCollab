import asyncio
import unittest
from types import SimpleNamespace

from steering_delivery import SteeringDelivery


class SteeringTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events = []
        async def emit(kind, data, persist=False):
            self.events.append(data)
        manager = SimpleNamespace(paused=False, states={'a': {}},
                                  project_room=SimpleNamespace(state={}), emit=emit)
        self.run = SimpleNamespace(manager=manager, name='a', id='run', channel='one', receipts=set())
        self.delivery = SteeringDelivery(self.run)

    def request(self, identifier=1):
        return {'human': True, 'message_id': identifier, 'prompt': 'correction'}

    async def test_write_is_not_acceptance_and_replay_is(self):
        request = self.request()
        async def send(text, original):
            self.assertIs(original, request)
            return True
        self.delivery.offer(request)
        self.delivery.install(send)
        await self.delivery.task
        self.assertEqual(request['_steering'], 'sent')
        self.assertFalse(request.get('_steer_delivered'))
        await self.delivery.delivered(request)
        self.assertTrue(request['_steer_delivered'])
        self.assertEqual(self.run.receipts, {'message:1'})
        await self.delivery.close('completed')
        self.assertEqual(request['_steering'], 'accepted')

    async def test_stop_cancels_pending_send_and_ignores_late_ack(self):
        started = asyncio.Event()
        async def send(text, request):
            started.set()
            await asyncio.Future()
        request = self.request()
        self.delivery.install(send)
        self.delivery.offer(request)
        await started.wait()
        await self.delivery.close('cancelled')
        await self.delivery.delivered(request)
        self.assertEqual(request['_steering'], 'cancelled')
        self.assertFalse(request.get('_steer_delivered'))
        self.assertTrue(self.delivery.task.done())

    async def test_transport_failure_keeps_request_without_retry(self):
        calls = []
        async def send(text, request):
            calls.append(text)
            raise RuntimeError('turn ended')
        request = self.request()
        self.delivery.offer(request)
        self.delivery.install(send)
        await self.delivery.task
        self.delivery.kick()
        await self.delivery.task
        self.assertEqual(len(calls), 1)
        self.assertEqual(request['_steering'], 'queued_for_next_turn')
        self.assertFalse(request.get('_steer_delivered'))

    async def test_no_sender_and_peer_chatter_do_not_inject(self):
        request = self.request()
        self.delivery.offer(request)
        self.delivery.offer({'human': False, 'prompt': 'peer chatter'})
        self.assertEqual(self.delivery.pending, [request])
        self.assertIsNone(self.delivery.task)
        await self.delivery.close('completed')
        self.assertEqual(request['_steering'], 'queued_for_next_turn')

    async def test_queue_order_preserved_and_duplicate_id_sent_once(self):
        calls = []
        async def send(text, request):
            calls.append(request['message_id'])
            await self.delivery.delivered(request)
            return True
        for identifier in [1, 2, 2, 3]:
            self.delivery.offer(self.request(identifier))
        self.delivery.install(send)
        await self.delivery.task
        self.assertEqual(calls, [1, 2, 3])

    async def test_runtime_routes_only_same_channel_and_stop_refuses(self):
        from room_runtime import RoomRuntime
        from coordination_queue import WakeQueue
        runtime = object.__new__(RoomRuntime)
        runtime.paused = False
        runtime.states = {'a': {'state': 'working'}}
        runtime.queues = {'a': WakeQueue(maxsize=4)}
        runtime.project_room = SimpleNamespace(chain_for=lambda channel: 'chain')
        runtime.host = SimpleNamespace(store=SimpleNamespace(add=lambda *a, **k: None))
        self.run.steering = self.delivery
        runtime.running = {'a': self.run}
        runtime.enqueue('a', channel='other', message='elsewhere')
        self.assertEqual(self.delivery.pending, [])
        runtime.enqueue('a', channel='one', message='here')
        self.assertEqual(len(self.delivery.pending), 1)
        runtime.paused = True
        runtime.enqueue('a', channel='one', message='must not restart')
        self.assertEqual(len(self.delivery.pending), 1)


if __name__ == '__main__':
    unittest.main()
