"""Deployment checkpoint and unlatched Stop survival across a restart (task 89036101, backend 3de6cf8e notes 1-2)."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from coordination_queue import WakeQueue
import room_runtime
from room_runtime import RoomRuntime


def runtime(root, identity='bbbbbbbbbbbb'):
    rt = RoomRuntime.__new__(RoomRuntime)
    rt.deployment = SimpleNamespace(root=root, rehearsal=False, lock=threading.Lock())
    rt.identity = identity
    rt.queues = {'astra': WakeQueue(maxsize=4)}
    rt.deploy_held, rt.running, rt.install_interrupted = {}, {}, {}
    rt.paused = False
    rt.manual_stop = rt.manual_stop_fault = rt.manual_stop_deferred = None
    rt.manual_stop_pending, rt.manual_stop_applied = False, None
    return rt


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.path = self.root / 'pending-wakes.json'

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding='utf-8')

    def read(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def test_keeps_another_projects_queued_work(self):
        theirs = {'project_id': 'aaaaaaaaaaaa', 'queues': {'astra': [{'id': 'w1'}]}, 'interrupted': []}
        self.write(theirs)
        rt = runtime(self.root)
        rt.queues['astra'].restore({'id': 'mine'})
        rt.save_deploy_queue()
        self.assertEqual(self.read(), theirs)

    def test_keeps_another_projects_interrupted_turns(self):
        theirs = {'project_id': 'aaaaaaaaaaaa', 'queues': {}, 'interrupted': [{'agent': 'astra', 'task_id': 't'}]}
        self.write(theirs)
        runtime(self.root).save_deploy_queue()
        self.assertEqual(self.read(), theirs)

    def test_replaces_own_project_checkpoint(self):
        self.write({'project_id': 'bbbbbbbbbbbb', 'queues': {'astra': [{'id': 'old'}]}, 'interrupted': []})
        rt = runtime(self.root)
        rt.queues['astra'].restore({'id': 'new'})
        rt.save_deploy_queue()
        self.assertEqual(self.read()['queues']['astra'], [{'id': 'new'}])

    def test_replaces_empty_or_unreadable_foreign_checkpoint(self):
        for content in (json.dumps({'project_id': 'aaaaaaaaaaaa', 'queues': {'astra': []}}), '{not json'):
            self.path.write_text(content, encoding='utf-8')
            runtime(self.root).save_deploy_queue()
            self.assertEqual(self.read()['project_id'], 'bbbbbbbbbbbb')


class UnlatchedClickTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.deferred = self.root / room_runtime.MANUAL_STOP_DEFERRED_FILE

    def restart(self):
        (self.root / room_runtime.MANUAL_STOP_FAULT_FILE).write_text('latch write failed', encoding='utf-8')
        rt = runtime(self.root)
        rt.open_manual_stop(False)
        return rt

    def test_stop_without_latch_survives_restart_and_holds_room(self):
        runtime(self.root).defer_manual_stop('stop')
        rt = self.restart()
        self.assertTrue(rt.paused)
        self.assertEqual((rt.manual_stop_pending, rt.manual_stop_deferred), (True, 'stop'))
        rt.save_project = mock.Mock()
        self.assertTrue(rt.commit_manual_stop(None))
        self.assertFalse(self.deferred.exists())

    def test_last_click_wins(self):
        rt = runtime(self.root)
        rt.defer_manual_stop('stop')
        rt.defer_manual_stop('resume')
        restarted = self.restart()
        self.assertEqual(restarted.manual_stop_deferred, 'resume')
        self.assertFalse(restarted.paused)

    def test_latched_click_writes_nothing(self):
        rt = runtime(self.root)
        rt.manual_stop = object()
        rt.defer_manual_stop('stop')
        self.assertFalse(self.deferred.exists())

    def test_unreadable_record_holds_like_stop(self):
        self.deferred.write_text('{broken', encoding='utf-8')
        self.assertTrue(self.restart().paused)


if __name__ == '__main__':
    unittest.main()
