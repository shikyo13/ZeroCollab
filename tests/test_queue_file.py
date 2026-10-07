import json
import multiprocessing
import tempfile
import time
import unittest
from pathlib import Path

import queue_file


def produce(path, prefix):
    for i in range(40):
        queue_file.append(path, {'id': f'{prefix}-{i}'})


class QueueFileTests(unittest.TestCase):
    def test_concurrent_processes_and_drain_do_not_lose_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'agent_queue.jsonl'
            context = multiprocessing.get_context('spawn')
            producers = [context.Process(target=produce, args=(path, name)) for name in ('a', 'b')]
            received = []
            try:
                for process in producers:
                    process.start()
                deadline = time.monotonic() + 15
                while any(process.is_alive() for process in producers):
                    self.assertLess(time.monotonic(), deadline)
                    received.extend(queue_file.drain(path))
                    time.sleep(.005)
                for process in producers:
                    process.join()
                    self.assertEqual(process.exitcode, 0)
                received.extend(queue_file.drain(path))
                ids = [json.loads(line)['id'] for line in received]
                self.assertEqual(len(ids), 80)
                self.assertEqual(set(ids), {f'{name}-{i}' for name in ('a', 'b') for i in range(40)})
            finally:
                for process in producers:
                    if process.is_alive():
                        process.terminate()
                    if process.pid:
                        process.join()

    def test_clear_then_append_and_empty_drain(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'agent_queue.jsonl'
            self.assertEqual(queue_file.drain(path), [])
            queue_file.append(path, {'id': 'old'})
            queue_file.clear(path)
            queue_file.append(path, {'id': 'new'})
            self.assertEqual([json.loads(line) for line in queue_file.drain(path)], [{'id': 'new'}])
            self.assertEqual(queue_file.drain(path), [])
