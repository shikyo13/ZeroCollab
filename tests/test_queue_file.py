import json
import multiprocessing
import tempfile
import time
import unittest
from pathlib import Path
import threading
from unittest.mock import AsyncMock, Mock, patch

import queue_file
from agents import AgentTrigger


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


class AsyncQueueCallerTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_target_does_not_block_event_loop_or_later_target(self):
        loop_thread = threading.get_ident()
        calls = []
        def append(path, entry):
            self.assertNotEqual(threading.get_ident(), loop_thread)
            calls.append(path.name)
            if path.name.startswith('first_'):
                raise TimeoutError('queue busy')
        trigger = AgentTrigger(Mock())
        with patch('agents.file_queue.append', side_effect=append), self.assertLogs('agents', level='ERROR'):
            results = [await trigger.trigger(name) for name in ('first', 'second')]
        self.assertEqual(results, [False, True])
        self.assertEqual(calls, ['first_queue.jsonl', 'second_queue.jsonl'])

    async def test_stop_cancels_turns_before_off_thread_clear_failure(self):
        from room_runtime import RoomRuntime
        with tempfile.TemporaryDirectory() as directory:
            runtime = object.__new__(RoomRuntime)
            runtime.data = Path(directory)
            (runtime.data / 'a_queue.jsonl').touch()
            runtime.team_builder, runtime.goals, runtime.host = Mock(), Mock(), Mock()
            runtime.save_project = Mock()
            runtime.queues, runtime.states = {}, {'a': {}}
            runtime.cancel_turn, runtime.publish_status = AsyncMock(), AsyncMock()
            runtime.default_channel = lambda: 'general'
            loop_thread = threading.get_ident()
            def clear(path):
                self.assertNotEqual(threading.get_ident(), loop_thread)
                runtime.cancel_turn.assert_awaited_once_with('a')
                raise TimeoutError('queue busy')
            with patch('room_runtime.file_queue.clear', side_effect=clear), self.assertLogs('room_runtime', level='ERROR'):
                await runtime.stop()
            self.assertTrue(runtime.paused)
            self.assertIn('could not be cleared', runtime.host.store.add.call_args.args[1])
