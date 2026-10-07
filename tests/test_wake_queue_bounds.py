import asyncio
import unittest
from unittest.mock import patch
from coordination_queue import WakeQueue


def batch(*ids):
    return {'channel': 'a', 'chain': str(ids),
            'requests': [{'message_id': n, 'human': True} for n in ids]}


class WakeQueueBoundsTests(unittest.TestCase):
    def test_overflow_is_atomic_and_duplicate_still_coalesces(self):
        with patch('coordination_queue.MAX_BATCH_REQUESTS', 2):
            q = WakeQueue(maxsize=4)
            q.put_nowait(batch(1, 2))
            q.put_nowait(batch(2))
            with self.assertRaises(asyncio.QueueFull):
                q.put_nowait(batch(3))
            self.assertEqual(q.get_nowait()['requests'], batch(1, 2)['requests'])
            with self.assertRaises(asyncio.QueueFull):
                q.put_nowait(batch(1, 2, 3))
            self.assertTrue(q.empty())

    def test_held_merge_retains_overflow_for_next_turn(self):
        with patch('coordination_queue.MAX_BATCH_REQUESTS', 2):
            q = WakeQueue(maxsize=4)
            held = batch(1, 2)
            q.put_nowait(batch(3))
            q.merge_waiting(held)
            self.assertEqual(held, batch(1, 2))
            self.assertEqual(q.get_nowait(), batch(3))
