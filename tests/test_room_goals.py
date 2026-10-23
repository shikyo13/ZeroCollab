import asyncio
import time
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from zerocollab.coordination.room_goals import RoomGoals


class GoalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = SimpleNamespace(path=Path(self.tmp.name) / 'coordination.json', chains={}, limits={'hops': 4})
        def chain(channel):
            p.chains['chain'] = {'handoffs': 0}
            return 'chain'
        p.new_chain = chain
        self.r = SimpleNamespace(project_room=p, paused=False, running={},
            states={'lead': {'state': 'ready'}}, queues={'lead': asyncio.Queue()},
            host=SimpleNamespace(room_settings={'channels': ['general']}, store=SimpleNamespace(add=lambda *a, **k: {'id': 1})),
            default_channel=lambda: 'general', owns_channel=lambda c: c == 'general')
        self.calls = []
        def enqueue(*args, **kwargs):
            self.calls.append((args, kwargs))
            self.r.queues['lead'].put_nowait(kwargs)
            return True
        self.r.enqueue = enqueue
        self.g = RoomGoals(self.r)
        self.g.load()

    def start(self, **kwargs):
        return self.g.change('user', 'start', text='Implement X and verify behavior', owner='lead', **kwargs)

    def test_no_duplicate_or_polling_continuations(self):
        self.start()
        self.g.schedule(); self.g.schedule()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.g.goal['rounds'], 1)
        self.r.queues['lead'].get_nowait()
        self.r.running['lead'] = object()
        self.g.schedule()
        self.assertEqual(len(self.calls), 1)

    def test_stop_invalidates_queued_goal(self):
        self.start(); self.g.schedule()
        request = self.calls[0][1]['routing']
        self.g.pause('User stop')
        self.assertFalse(self.g.accepts(request))
        self.g.schedule()
        self.assertEqual(len(self.calls), 1)

    def test_human_queue_precedes_goal(self):
        self.start(); self.r.queues['lead'].put_nowait('human steering')
        self.g.schedule()
        self.assertEqual(self.calls, [])

    def test_limits_pause_without_resetting_chain(self):
        self.start(max_rounds=1); self.g.schedule()
        self.r.queues['lead'].get_nowait(); self.g.schedule()
        self.assertEqual(self.g.goal['status'], 'paused')
        with self.assertRaises(ValueError):
            self.g.change('user', 'resume', goal_id=self.g.goal['id'])

    def test_turn_budget(self):
        self.start(); self.r.project_room.limits['agent_turns'] = 4; self.r.project_room.chains['chain']['turns'] = 4
        self.g.schedule()
        self.assertEqual(self.g.goal['status'], 'paused')
        self.assertEqual(self.calls, [])

    def test_error_or_paused_lead_does_not_spin(self):
        self.start(); self.r.states['lead']['state'] = 'blocked'; self.g.schedule()
        self.assertEqual(self.g.goal['status'], 'paused')
        self.assertEqual(self.calls, [])

    def test_lead_completion_requires_evidence_and_current_id(self):
        goal = self.start()
        for actor, identifier, evidence in [('peer', goal['id'], 'passed'), ('lead', 'stale', 'passed'), ('lead', goal['id'], '')]:
            with self.assertRaises(ValueError):
                self.g.change(actor, 'complete', goal_id=identifier, evidence=evidence)
        self.g.change('lead', 'complete', goal_id=goal['id'], evidence='Implemented and checks passed')
        self.g.schedule()
        self.assertEqual(self.calls, [])

    def test_agent_cannot_start_or_resume(self):
        with self.assertRaises(ValueError): self.g.change('lead', 'start', text='X', owner='lead')
        self.start(); self.g.pause('blocked')
        with self.assertRaises(ValueError): self.g.change('lead', 'resume', goal_id=self.g.goal['id'])

    def test_restart_pauses_and_restores_budget(self):
        self.start()
        self.r.project_room.chains['chain'].update(turns=3, reported_tokens=1234, handoffs=2)
        self.g.save()
        other = RoomGoals(self.r); other.load()
        self.assertEqual(other.goal['status'], 'paused')
        self.r.project_room.chains.clear()
        other.change('user', 'resume', goal_id=other.goal['id'])
        self.assertEqual(self.r.project_room.chains['chain']['turns'], 3)
        self.assertEqual(self.r.project_room.chains['chain']['reported_tokens'], 1234)

    def test_worker_dequeue_gap_counts_as_busy(self):
        self.start(); self.r.states['lead']['task'] = object()
        self.g.schedule()
        self.assertEqual(self.calls, [])

    def test_round_resets_only_peer_hop_allowance(self):
        self.start(); chain = self.r.project_room.chains['chain']
        chain.update(handoffs=4, turns=3, reported_tokens=123)
        self.g.schedule()
        self.assertEqual(chain['handoffs'], 0)
        self.assertEqual(chain['turns'], 3)
        self.assertEqual(chain['reported_tokens'], 123)

    def test_context_only_on_change_or_new_session(self):
        self.start()
        run = SimpleNamespace(name='lead', previous_session=None)
        self.assertIn('Implement X', self.g.context(run))
        run.previous_session = 'session'
        self.assertEqual(self.g.context(run), '')
        self.g.pause('User stop')
        self.assertIn('paused', self.g.context(run))

    def test_validation_and_single_active_goal(self):
        with self.assertRaises(ValueError): self.start(max_rounds=0)
        self.start()
        with self.assertRaises(ValueError): self.start()


class RuntimeGoalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from unittest.mock import AsyncMock, patch
        import zerocollab.runtime.room_runtime as room_runtime
        from zerocollab.coordination.project_room import ProjectRoom
        from zerocollab.coordination.coordination_queue import WakeQueue
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = room_runtime.RoomRuntime(SimpleNamespace(config={'server': {'data_dir': self.tmp.name}, 'agents': {}, 'runtime': {}}))
        self.r.data = Path(self.tmp.name)
        self.r.paused = False
        self.r.running = {}
        self.r.states = {'lead': {'state': 'ready', 'provider': 'api'}}
        self.r.queues = {'lead': WakeQueue(maxsize=4)}
        self.r.host = SimpleNamespace(room_settings={'channels': ['general']}, store=SimpleNamespace(add=lambda *a, **k: {'id': 1}))
        self.r.approvals = {}
        self.r.project_room = ProjectRoom(self.r)
        self.r.project_room.use_project('test')
        self.r.default_channel = lambda: 'general'
        self.r.owns_channel = lambda c: c == 'general'
        self.r.project_room.complete = lambda run: None
        self.r.goals = RoomGoals(self.r); self.r.goals.load()
        self.r.goals.change('user', 'start', text='Done criteria', owner='lead')
        self.r.publish_status = AsyncMock()
        self.r.emit = AsyncMock()
        self.r.context = lambda run, changes: 'goal context'
        self.r.collect_diffs = AsyncMock(return_value={})
        self.r.activity_note = AsyncMock()
        self.r.check_room_tools = lambda run: None
        self.r.save_project = lambda: None
        self.r.decisions = SimpleNamespace(accepts=lambda r: True, routes=lambda c: True, turn_finished=lambda *a: None)
        self.r.team_builder = SimpleNamespace(accepts=lambda r: True, turn_finished=lambda *a: None)
        self.r.local_turn = AsyncMock()
        class FakeRun:
            def __init__(inner, manager, name, entry):
                inner.id = 'run'; inner.name = name; inner.channel = entry['channel']; inner.entry = entry
                inner.manager_audit = None; inner.focus_history = []; inner.focus_seed = None; inner.task_id = None; inner.text = ''
                inner.progress = {}; inner.file_changes = 0; inner.first_output_at = None; inner.created_at = 0; inner.compacted = False; inner.receipts = set()
                inner.steering = SimpleNamespace(close=AsyncMock()); inner.changing_calls = 0; inner.coordination_calls = 1
                inner.native_tool_calls = 0; inner.input_chars = 0; inner.input_blocks = {}; inner.input_clipped = False
                inner.input_unchanged = []; inner.reported_tokens = 0; inner.relay_problem = ''; inner.stop_note = ''
                inner.settings = {}; inner.message_phase = ''; inner.published_messages = ['m']
                inner.stop_reason = ''; inner.stderr = ''; inner.reported_settings = {}
            def begin(inner, deadline):
                inner.started_at = time.monotonic()
            async def finish_message(inner, **kwargs): pass
            async def publish_last(inner, **kwargs): pass
        for target, value in [('Run', FakeRun), ('native.terminate', AsyncMock()), ('native.restore_room_server', lambda run: None)]:
            patcher = patch('zerocollab.runtime.room_runtime.' + target, value)
            patcher.start(); self.addCleanup(patcher.stop)

    async def test_goal_round_uses_turn_budget_not_peer_hop(self):
        self.r.goals.schedule()
        entry = self.r.queues['lead'].get_nowait()
        await self.r.run_turn('lead', entry)
        chain = self.r.project_room.chains[self.r.goals.goal['chain']]
        self.assertEqual(chain['turns'], 1)
        self.assertEqual(chain['handoffs'], 0)
        self.r.local_turn.assert_awaited_once()
        self.r.goals.schedule()
        self.assertEqual(self.r.queues['lead'].qsize(), 1)

    async def test_paused_queued_goal_never_calls_provider(self):
        self.r.goals.schedule(); entry = self.r.queues['lead'].get_nowait()
        self.r.goals.pause('stop')
        await self.r.run_turn('lead', entry)
        self.r.local_turn.assert_not_awaited()

    async def test_runtime_budget_rejection_pauses_goal(self):
        self.r.goals.schedule(); entry = self.r.queues['lead'].get_nowait()
        chain = self.r.project_room.chains[self.r.goals.goal['chain']]
        chain['paused'] = True
        await self.r.run_turn('lead', entry)
        self.r.local_turn.assert_not_awaited()
        self.assertEqual(self.r.goals.goal['status'], 'paused')

    async def test_provider_failure_never_retries(self):
        self.r.goals.schedule(); entry = self.r.queues['lead'].get_nowait()
        self.r.local_turn.side_effect = RuntimeError('provider unavailable')
        await self.r.run_turn('lead', entry)
        self.r.goals.schedule()
        self.assertEqual(self.r.goals.goal['status'], 'paused')
        self.assertTrue(self.r.queues['lead'].empty())

    async def test_goal_http_controls_share_runtime_state(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from unittest.mock import patch
        import zerocollab.runtime.room_runtime as room_runtime
        host = SimpleNamespace(app=FastAPI(), agents=SimpleNamespace())
        with patch('zerocollab.runtime.room_runtime.RoomRuntime', return_value=self.r):
            room_runtime.install(host)
        async with AsyncClient(transport=ASGITransport(app=host.app), base_url='http://test') as client:
            goal = (await client.get('/api/runtime/goal')).json()
            response = await client.post('/api/runtime/goal', json={'action': 'pause', 'goal_id': goal['id']})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['status'], 'paused')
            bad = await client.post('/api/runtime/goal', json={'action': 'complete', 'goal_id': goal['id']})
            self.assertEqual(bad.status_code, 400)
            response = await client.post('/api/runtime/goal', json={'action': 'resume', 'goal_id': goal['id']})
            self.assertEqual(response.json()['status'], 'active')
            self.assertEqual(self.r.queues['lead'].qsize(), 1)

    async def test_worker_survives_scheduler_failure(self):
        from unittest.mock import patch
        self.r.goals.schedule()
        with patch.object(self.r.goals, 'schedule', side_effect=OSError('locked')):
            worker = asyncio.create_task(self.r.worker('lead'))
            for _ in range(12): await asyncio.sleep(0)
            self.assertFalse(worker.done())
            self.assertEqual(self.r.goals.goal['status'], 'paused')
            worker.cancel()
            with self.assertRaises(asyncio.CancelledError): await worker


if __name__ == '__main__':
    unittest.main()
