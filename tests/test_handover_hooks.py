"""Backend's host handover hooks (desktop f0ef18fa, bridge r1): the runtime seams the bridge relies on, with a
stand-in bridge exposing the pinned API (startup_hold, tick, held, exit_ready, release_for_user)."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from room_runtime import RoomRuntime


class Stop(Exception):
    pass


class Halt(BaseException):
    """Ends the beat loop in a test: the beat itself catches Exception."""


class HandoverHooks(unittest.TestCase):
    def test_admission_and_enqueue_honour_the_bridge(self):
        runtime = SimpleNamespace(handover=SimpleNamespace(held=True, exit_ready=False), deployment=None)
        self.assertFalse(RoomRuntime.turn_admission_open(runtime))
        runtime.handover = SimpleNamespace(held=False, exit_ready=True)
        self.assertFalse(RoomRuntime.enqueue(runtime, 'worker', 'general', prompt='late'))  # after the checkpoint

    def test_startup_hold_runs_before_any_project_setup(self):
        runtime = mock.MagicMock()
        runtime.handover.startup_hold.side_effect = Halt  # not an Exception, so the N2 catch lets it through
        with self.assertRaises(Halt):
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

    def test_an_unreadable_record_starts_the_room_held_until_resume(self):
        # security N2: hold, don't fail to start; the user's Resume releases it and it is recorded.
        runtime = mock.MagicMock(spec_set=None)
        runtime.handover.startup_hold.side_effect = ValueError('ack_schema')
        with mock.patch('room_runtime.load_workspace', side_effect=Stop):
            with self.assertRaises(Stop):  # start() carries on past the hold to the workspace load
                asyncio.run(RoomRuntime.start(runtime))
        self.assertIn('ack_schema', runtime.handover_fault)
        held = SimpleNamespace(handover=SimpleNamespace(held=False), handover_fault=runtime.handover_fault,
                               deployment=None)
        self.assertFalse(RoomRuntime.turn_admission_open(held))
        resumed = mock.MagicMock()
        resumed.handover_fault = 'ValueError: ack_schema'
        resumed.handover.held = False
        resumed.manual_stop_call.side_effect = Stop
        with self.assertRaises(Stop):
            asyncio.run(RoomRuntime.user_resume(resumed, True))
        self.assertIsNone(resumed.handover_fault)
        self.assertIn('host_handover_fault_released', resumed.append_event.call_args[0][0])

    def test_a_repeated_tick_error_is_logged_once(self):
        async def beat():
            runtime = mock.MagicMock()
            runtime.handover.tick = mock.AsyncMock(side_effect=ValueError('request_schema'))
            runtime.resume_deploy_checks = mock.AsyncMock(side_effect=[None, Halt])
            runtime.consume_release_completions = mock.Mock()
            runtime.deployment.reload.return_value = {'writes': 'blocked'}
            with mock.patch('room_runtime.LOGGER') as logger, mock.patch('asyncio.sleep', mock.AsyncMock()):
                with self.assertRaises(Halt):
                    await RoomRuntime.beat(runtime)
            return logger.exception.call_count
        self.assertEqual(asyncio.run(beat()), 1)  # two beats, one log line (security N1)

    def test_resume_note_says_until_when_the_bridge_holds(self):
        import sys
        sys.modules['host_handover'] = SimpleNamespace(SECONDS=600)
        self.addCleanup(sys.modules.pop, 'host_handover', None)
        runtime = SimpleNamespace(handover=SimpleNamespace(held=True, hold_since=None, request=None,
                                                           ack={'nonce': 'ab' * 16, 'created': 0}))
        self.assertIn('Held for host handover abababab until', RoomRuntime.handover_hold_note(runtime))
        runtime.handover.held = False
        self.assertEqual(RoomRuntime.handover_hold_note(runtime), '')

    def test_user_resume_releases_only_the_held_bridge(self):
        runtime = mock.MagicMock()
        runtime.handover_fault = None
        runtime.handover.held = True
        runtime.handover.release_for_user = mock.AsyncMock(side_effect=Stop)
        with self.assertRaises(Stop):
            asyncio.run(RoomRuntime.user_resume(runtime, False))
        runtime.handover.release_for_user.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
