"""Backend's host handover hooks (desktop f0ef18fa, bridge r1): the runtime seams the bridge relies on, with a
stand-in bridge exposing the pinned API (startup_hold, tick, held, exit_ready, release_for_user)."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from room_runtime import RoomRuntime


class Stop(Exception):
    pass


class HandoverHooks(unittest.TestCase):
    def test_admission_and_enqueue_honour_the_bridge(self):
        runtime = SimpleNamespace(handover=SimpleNamespace(held=True, exit_ready=False), deployment=None)
        self.assertFalse(RoomRuntime.turn_admission_open(runtime))
        runtime.handover = SimpleNamespace(held=False, exit_ready=True)
        self.assertFalse(RoomRuntime.enqueue(runtime, 'worker', 'general', prompt='late'))  # after the checkpoint

    def test_startup_hold_runs_before_any_project_setup(self):
        runtime = mock.MagicMock()
        runtime.handover.startup_hold.side_effect = Stop
        with self.assertRaises(Stop):
            asyncio.run(RoomRuntime.start(runtime))
        runtime.setup_project.assert_not_called()
        runtime.restore_deploy_queue.assert_not_called()

    def test_close_after_a_ready_handover_keeps_the_checkpoint(self):
        async def close():
            runtime = SimpleNamespace(handover=SimpleNamespace(exit_ready=True), release_manager=None,
                                      deployment=SimpleNamespace(reload=lambda: {'writes': 'blocked'}),
                                      running={}, workers={}, host=SimpleNamespace(),
                                      heartbeat=asyncio.get_running_loop().create_future(),
                                      save_deploy_queue=mock.Mock(), stop=mock.AsyncMock())
            runtime.heartbeat.set_result(None)
            await RoomRuntime.close(runtime)
            return runtime
        runtime = asyncio.run(close())
        runtime.save_deploy_queue.assert_not_called()  # the hashed checkpoint is final
        runtime.stop.assert_not_called()  # never the user's Stop path, which clears queues

    def test_user_resume_releases_only_the_held_bridge(self):
        runtime = mock.MagicMock()
        runtime.handover.held = True
        runtime.handover.release_for_user = mock.AsyncMock(side_effect=Stop)
        with self.assertRaises(Stop):
            asyncio.run(RoomRuntime.user_resume(runtime, False))
        runtime.handover.release_for_user.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
