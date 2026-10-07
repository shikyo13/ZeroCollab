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

    def test_restore_preserves_accepted_work_above_admission_limits(self):
        from types import SimpleNamespace
        from pathlib import Path
        from room_runtime import RoomRuntime
        with patch('coordination_queue.MAX_BATCH_REQUESTS', 2):
            q = WakeQueue(maxsize=4)
            entries = [batch(1, 2), batch(3, 4), batch(5, 6, 7)]
            entries += [{**batch(n), 'channel': str(n)} for n in range(8, 13)]
            runtime = RoomRuntime.__new__(RoomRuntime)
            runtime.deployment = SimpleNamespace(root=Path('.'))
            runtime.identity = 'project'
            runtime.queues = {'agent': q}
            saved = {'project_id': 'project', 'queues': {'agent': entries}}
            with patch('room_deploy.read_json', return_value=saved):
                runtime.restore_deploy_queue()
            self.assertTrue(runtime.deploy_checkpoint_restored)
            self.assertEqual([q.get_nowait() for _ in entries], entries)

    def test_restore_keeps_new_arrivals_after_saved_work(self):
        with patch('coordination_queue.MAX_BATCH_REQUESTS', 2):
            q = WakeQueue(maxsize=4)
            q.restore(batch(1))
            q.restore(batch(2))
            q.put_nowait(batch(3))
            self.assertEqual(q.get_nowait(), batch(1))
            self.assertEqual(q.get_nowait()['requests'], batch(2, 3)['requests'])

    def test_waiting_merge_does_not_skip_full_older_batch(self):
        with patch('coordination_queue.MAX_BATCH_REQUESTS', 2):
            q = WakeQueue(maxsize=4)
            held = batch(1)
            q.restore(batch(2, 3))
            q.restore(batch(4))
            q.merge_waiting(held)
            self.assertEqual(held, batch(1))
            self.assertEqual(q.get_nowait(), batch(2, 3))
            self.assertEqual(q.get_nowait(), batch(4))

    def test_prompt_byte_limit_is_atomic_and_counts_utf8(self):
        with patch('coordination_queue.MAX_BATCH_PROMPT_BYTES', 4):
            q = WakeQueue(maxsize=4)
            first = batch(1)
            first['requests'][0]['prompt'] = '\u00e9\u00e9'
            q.put_nowait(first)
            incoming = batch(2)
            incoming['requests'][0]['prompt'] = 'x'
            with self.assertRaises(asyncio.QueueFull):
                q.put_nowait(incoming)
            self.assertEqual(q.get_nowait(), first)
            incoming['requests'][0]['prompt'] = '\u00e9\u00e9\u00e9'
            with self.assertRaises(asyncio.QueueFull):
                q.put_nowait(incoming)
            q.restore(incoming)
            self.assertEqual(q.get_nowait(), incoming)

    def test_replacement_reclaims_prompt_budget(self):
        with patch('coordination_queue.MAX_BATCH_PROMPT_BYTES', 4):
            q = WakeQueue(maxsize=4)
            first = batch(1)
            first['requests'][0].update(prompt='abcd', ref={'collection': 'tasks', 'id': 't', 'updated': 1})
            q.put_nowait(first)
            replacement = batch(2)
            replacement['requests'][0].update(prompt='xy', ref={'collection': 'tasks', 'id': 't', 'updated': 2})
            q.put_nowait(replacement)
            self.assertEqual(q.get_nowait()['requests'], replacement['requests'])
