"""Scheduler admission blockers, no model process or live room."""
import json
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from manager_wakes import ManagerWakes, TURN_CREDITS
from ready_wakes import ReadyWakes


class SchedulerChecks(unittest.TestCase):
    def setUp(self):
        self.now = 10000
        self.mono = 1
        self.saved = []
        self.queued = []
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        pr = SimpleNamespace(path=Path(folder.name) / 'coordination.json', chains={},
            limits={'agent_turns': 0, 'reported_token_budget': 0},
            state={'tasks': [self.task('manager', 'lead')], 'reviews': [], 'followups': [],
                   'responders': {'lead': 'lead', 'backup': ''}})
        def save():
            pr.path.write_text(json.dumps(pr.state), encoding='utf-8')
            self.saved.append(json.loads(pr.path.read_text('utf-8')))
        pr.save = save
        self.r = SimpleNamespace(project_room=pr, paused=False, deployment=None, identity=None,
            states={'lead': {'provider': 'codex', 'state': 'ready'},
                    'worker': {'provider': 'api', 'state': 'ready'}},
            running={}, deploy_held={}, queues={}, goals=SimpleNamespace(goal=None),
            decisions=SimpleNamespace(routes=lambda chat: chat == 'ordinary'),
            turn_admission_open=lambda: True, owns_channel=lambda chat: chat == 'ordinary')
        def enqueue(name, channel, **kwargs):
            self.assertEqual(list(self.saved[-1]['manager_audit']['claims'].values())[-1]['status'], 'claimed')
            self.queued.append(kwargs)
            return True
        self.r.enqueue = enqueue
        self.r.ready_wakes = ReadyWakes(self.r)
        self.m = ManagerWakes(self.r, clock=lambda: self.mono, wall=lambda: self.now)
        self.m.adapter_ready = True  # Test stand-in; real adapter remains fail-closed.

    @staticmethod
    def task(identifier, owner=None, **extra):
        return dict(id=identifier, title=identifier, status='working', owner=owner,
                    work_rev=1, updated=100, **extra)

    def enable(self, **extra):
        return self.m.configure('user', enabled=True, manager='lead', task='manager',
                                channel='ordinary', interval_minutes=15, **extra)

    def due(self):
        self.mono += 901
        self.now += 901
        return self.m.tick()

    def actionable(self):
        self.r.project_room.state['tasks'].append(self.task('work', 'worker', next_action_state='ready'))

    def test_off_and_no_action_never_enqueue(self):
        self.assertFalse(self.due())
        self.enable()
        self.assertFalse(self.due())
        self.assertEqual(self.queued, [])

    def test_human_only_configuration_and_verified_adapter(self):
        with self.assertRaises(ValueError):
            self.m.configure('lead', enabled=True)
        self.m.adapter_ready = False
        with self.assertRaises(ValueError):
            self.enable()
        self.assertFalse(self.m.record.get('enabled', False))

    def test_claim_is_saved_before_enqueue_once_and_dispatch_once(self):
        self.actionable()
        self.enable()
        self.assertTrue(self.due())
        self.assertFalse(self.m.tick())
        request = self.queued[-1]['routing'] | {'chain': self.queued[-1]['chain']}
        self.assertTrue(self.m.accepts(request))
        self.assertFalse(self.m.accepts(request))
        self.assertEqual(len(self.queued), 1)

    def test_restart_preserves_debit_does_not_replay(self):
        self.actionable()
        self.enable()
        self.assertTrue(self.due())
        n = ManagerWakes(self.r, clock=lambda: self.mono, wall=lambda: self.now)
        n.adapter_ready = True
        self.assertFalse(n.tick())
        self.assertEqual(n.record['claimed_starts'], 1)
        self.assertEqual(next(iter(n.record['claims'].values()))['status'], 'unknown')
        self.assertEqual(len(self.queued), 1)

    def test_waiting_deferred_inbox_like_idle_is_not_ready(self):
        self.r.project_room.state['tasks'] += [self.task('waiting', 'worker', next_action_state='waiting'),
            self.task('later', next_action_state='deferred'),
            self.task('dependency', 'worker', next_action_state='ready', depends_on=['missing'])]
        self.enable()
        self.assertEqual(self.m.findings(), [])
        self.assertFalse(self.due())

    def test_busy_interval_is_covered_without_steering(self):
        self.actionable()
        self.enable()
        self.r.running['lead'] = object()
        self.assertFalse(self.due())
        self.r.running.clear()
        self.assertFalse(self.m.tick())
        self.assertEqual(self.queued, [])

    def test_stop_and_provider_change_before_dispatch_drop(self):
        self.actionable()
        self.enable()
        self.assertTrue(self.due())
        request = self.queued[-1]['routing'] | {'chain': self.queued[-1]['chain']}
        self.r.states['lead']['provider'] = 'gemini'
        self.assertFalse(self.m.accepts(request))
        self.assertEqual(next(iter(self.m.record['claims'].values()))['status'], 'dropped')

    def test_overdue_pause_catches_up_once_on_resume(self):
        self.actionable()
        self.enable()
        self.r.paused = True
        self.assertFalse(self.due())
        self.r.paused = False
        self.assertTrue(self.m.tick())
        self.assertFalse(self.m.tick())
        self.assertEqual(len(self.queued), 1)

    def test_pause_before_deadline_preserves_scheduled_slot(self):
        self.actionable()
        self.enable()
        scheduled = self.m.deadline
        self.r.paused = True
        self.mono += 100
        self.now += 100
        self.assertFalse(self.m.tick())
        self.r.paused = False
        self.assertFalse(self.m.tick())
        self.assertEqual(self.m.deadline, scheduled)
        self.mono += 800
        self.now += 800
        self.assertTrue(self.m.tick())

    def test_deployment_gate_catches_up_once_after_many_missed_slots(self):
        self.actionable()
        self.enable()
        self.r.turn_admission_open = lambda: False
        self.mono += 2701
        self.now += 2701
        self.assertFalse(self.m.tick())
        self.r.turn_admission_open = lambda: True
        self.assertTrue(self.m.tick())
        self.assertFalse(self.m.tick())
        self.assertEqual(len(self.queued), 1)
        self.assertEqual(self.m.record['claimed_starts'], 1)

    def test_restart_keeps_overdue_slot_and_never_replays_sent_claim(self):
        self.actionable()
        self.enable()
        self.mono += 901
        self.now += 901
        restarted = ManagerWakes(self.r, clock=lambda: 1, wall=lambda: self.now)
        self.assertTrue(restarted.tick())
        self.assertFalse(restarted.tick())
        self.assertEqual(len(self.queued), 1)
        self.now += 901
        reloaded = ManagerWakes(self.r, clock=lambda: 1, wall=lambda: self.now)
        self.assertFalse(reloaded.tick())  # Prior issue is already claimed, even after a missed slot.
        self.assertEqual(next(iter(reloaded.record['claims'].values()))['status'], 'unknown')
        self.assertEqual(reloaded.record['chain']['turns'], 1)
        self.assertEqual(len(self.queued), 1)

    def test_restart_before_due_keeps_remaining_time(self):
        self.actionable()
        self.enable()
        self.now += 100
        restarted = ManagerWakes(self.r, clock=lambda: 1, wall=lambda: self.now)
        self.assertFalse(restarted.tick())
        self.assertEqual(restarted.deadline, 801)

    def test_catchup_still_obeys_credit_and_hourly_limits(self):
        for ceiling in ('credits', 'hourly'):
            with self.subTest(ceiling=ceiling):
                self.setUp()
                self.actionable()
                self.enable()
                self.r.paused = True
                self.assertFalse(self.due())
                if ceiling == 'credits':
                    self.m.record['chain']['turns'] = TURN_CREDITS
                else:
                    self.r.ready_wakes.record['fired']['lead'] = [self.now] * 3
                self.r.paused = False
                self.assertFalse(self.m.tick())
                self.assertEqual(self.queued, [])

    def test_spent_and_missing_budget_do_not_remint(self):
        self.actionable()
        self.enable()
        self.m.record['chain']['turns'] = TURN_CREDITS
        self.assertFalse(self.due())
        self.enable()
        self.assertEqual(self.m.record['chain']['turns'], TURN_CREDITS)
        self.assertFalse(self.due())
        del self.m.record['chain']
        self.m.loaded = None
        self.assertFalse(self.m.tick())
        self.assertFalse(self.m.record['enabled'])

    def test_hourly_ceiling_is_shared_with_ready_wakes(self):
        self.actionable()
        self.enable()
        self.r.ready_wakes.record['fired']['lead'] = [self.now, self.now, self.now]
        self.assertFalse(self.due())
        self.assertEqual(self.queued, [])

    def test_dedupe_and_two_targeted_followups_only(self):
        self.actionable()
        self.enable()
        self.assertTrue(self.due())
        request = self.queued[-1]['routing'] | {'chain': self.queued[-1]['chain']}
        self.assertTrue(self.m.accepts(request))
        run = SimpleNamespace(manager_audit=request['manager_audit'])
        args = {'action': 'create', 'task_id': 'work', 'assigned_to': 'worker'}
        receipt = self.m.authorize(run, 'room_followup', args)
        self.assertIs(self.m.authorize(run, 'room_followup', args), receipt)
        with self.assertRaises(ValueError):
            self.m.authorize(run, 'room_task', {'action': 'update'})
        with self.assertRaises(ValueError):
            self.m.authorize(run, 'room_followup', args | {'task_id': 'invented'})

    def test_stuck_review_and_followup_are_reminded_through_their_task(self):
        # harness e8b68f2f: these were listed in the digest but every reminder was refused.
        state = self.r.project_room.state
        state['tasks'].append(self.task('feature', 'lead', next_action_state='waiting'))
        state['reviews'].append(dict(id='rev1', task_id='feature', reviewer='worker', status='pending', updated=100))
        state['followups'].append(dict(id='fu1', task_id='feature', assigned_to='worker', status='open', updated=100))
        self.enable()
        self.assertTrue(self.due())
        request = self.queued[-1]['routing'] | {'chain': self.queued[-1]['chain']}
        self.assertTrue(self.m.accepts(request))
        run = SimpleNamespace(manager_audit=request['manager_audit'])
        self.assertIn('"task_id": "feature"', self.queued[-1]['prompt'])
        args = {'action': 'create', 'task_id': 'feature', 'assigned_to': 'worker'}
        receipt = self.m.authorize(run, 'room_followup', args)
        self.assertEqual(receipt['status'], 'claimed')
        self.assertIs(self.m.authorize(run, 'room_followup', args), receipt)  # same issue, no second handoff
        with self.assertRaises(ValueError):  # only the reviewer/assignee it names
            self.m.authorize(run, 'room_followup', args | {'assigned_to': 'lead'})
        with self.assertRaises(ValueError):  # the review id is not a task
            self.m.authorize(run, 'room_followup', args | {'task_id': 'rev1'})

    def test_ordinary_request_covers_saved_claim_without_refund_or_replay(self):
        self.actionable()
        self.enable()
        self.assertTrue(self.due())
        request = self.queued[-1]['routing']
        self.m.covered([request])
        self.assertEqual(self.m.record['claims'][request['manager_audit']]['status'], 'covered')
        self.assertEqual(self.m.record['chain']['turns'], 1)
        self.assertFalse(self.m.accepts(request))

    def test_clock_jump_does_not_catch_up(self):
        self.actionable()
        self.enable()
        self.now += 300000
        self.assertFalse(self.m.tick())  # Live deadline is monotonic, not wall time.
        self.mono += 300000
        self.assertTrue(self.m.tick())
        self.assertFalse(self.m.tick())
        self.assertEqual(len(self.queued), 1)

    def test_audit_notes_cannot_replace_or_create_decisions(self):
        self.actionable()
        self.enable()
        self.assertTrue(self.due())
        request = self.queued[-1]['routing'] | {'chain': self.queued[-1]['chain']}
        self.assertTrue(self.m.accepts(request))
        run = SimpleNamespace(manager_audit=request['manager_audit'])
        for kind in ('progress', 'blocker'):
            self.assertIsNone(self.m.authorize(run, 'room_update', {'kind': kind, 'text': 'Observation'}))
        for arguments in ({'kind': 'decision'}, {'kind': 'reference'}, {'kind': 'progress', 'supersedes': 'old'},
                          {'kind': 'blocker', 'resolves': 'human'}, {}):
            with self.assertRaises(ValueError):
                self.m.authorize(run, 'room_update', arguments)

    def test_same_issue_costs_once_and_external_revision_rearms(self):
        self.actionable()
        self.enable()
        self.assertTrue(self.due())
        self.assertFalse(self.due())
        self.assertEqual(self.m.record['chain']['turns'], 1)
        self.assertEqual(self.m.record['last']['outcome'], 'no_findings')
        self.r.project_room.state['tasks'][-1]['work_rev'] += 1
        self.assertTrue(self.due())
        self.assertEqual(self.m.record['chain']['turns'], 2)

    def test_status_uses_display_label_and_audit_chain_kind(self):
        self.r.states['lead']['label'] = 'Release manager'
        result = self.enable()
        self.assertEqual(result['options']['managers'][0]['label'], 'Release manager')
        self.assertEqual(self.m.record['chain']['kind'], 'manager_audit')


class RealProjectAdmissionChecks(SchedulerChecks):
    def setUp(self):
        super().setUp()
        from project_room import ProjectRoom
        folder = self.r.project_room.path.parent
        self.r.data, self.r.project, self.r.identity = folder, None, 'test'
        room = ProjectRoom(self.r)
        self.r.project_room = room
        room.use_project('test')
        room.state['tasks'] = [self.task('manager', 'lead')]
        self.saved.clear()
        original_save = room.save
        def save():
            original_save()
            self.saved.append(json.loads(room.path.read_text('utf-8')))
        room.save = save
        self.r.manager_wakes = self.m

    def test_reserved_turn_and_downstream_are_one_finite_persisted_budget(self):
        self.actionable()
        self.enable()
        self.assertTrue(self.due())
        request = self.queued[-1]['routing'] | {'chain': self.queued[-1]['chain']}
        self.assertTrue(self.m.accepts(request))
        room, chain = self.r.project_room, request['chain']
        self.assertEqual(room.chains[chain]['turns'], 1)
        self.assertEqual(room.chains[chain]['kind'], 'manager_audit')
        self.assertTrue(room.permit(chain, audit_reserved=request['manager_audit']))
        self.assertEqual(room.chains[chain]['turns'], 1)
        self.assertFalse(room.permit(chain, audit_reserved=request['manager_audit']))
        self.assertTrue(room.permit(chain))
        self.assertEqual(room.chains[chain]['turns'], 2)
        self.assertEqual(json.loads(room.path.read_text('utf-8'))['manager_audit']['chain']['turns'], 2)
        room.use_project('test')
        self.m.loaded = None
        self.m.restore()
        self.assertEqual(room.chains[chain]['turns'], 2)
        room.chains[chain]['turns'] = TURN_CREDITS
        self.assertFalse(room.permit(chain))
        ordinary = room.new_chain('ordinary')
        self.assertTrue(room.permit(ordinary))
        self.assertEqual(room.chains[ordinary]['turns'], 1)
        self.assertFalse(room.permit(ordinary, audit_reserved=request['manager_audit']))
        self.assertEqual(room.chains[ordinary]['turns'], 1)

    def test_real_http_schema_and_stale_project_generation(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from manager_wakes import install
        app = FastAPI()
        install(self.r, SimpleNamespace(app=app))
        with TestClient(app) as client:
            status = client.get('/api/runtime/manager-audit').json()
            self.assertFalse(status['enabled'])
            self.assertEqual(status['project_id'], 'test')
            self.assertEqual(status['options']['interval'], {'min': 15, 'max': 240, 'default': 30})
            body = dict(action='enable', project_id='test', manager='lead', task_id='manager',
                        channel='ordinary', interval_minutes=15)
            self.assertEqual(client.post('/api/runtime/manager-audit', json=body | {'project_id': 'other'}).status_code, 409)
            response = client.post('/api/runtime/manager-audit', json=body)
            self.assertEqual(response.status_code, 200)
            status = response.json()
            self.assertIsInstance(status['generation'], str)
            self.assertEqual(status['config']['task_id'], 'manager')
            self.assertEqual(status['budget']['total'], 12)
            self.assertEqual(client.post('/api/runtime/manager-audit', json=dict(action='renew', project_id='test', generation='stale')).status_code, 409)
            self.assertEqual(client.post('/api/runtime/manager-audit', json=body | {'budget': 999}).status_code, 400)
            client.post('/api/runtime/manager-audit', json=dict(action='disable', project_id='test'))
            status = client.get('/api/runtime/manager-audit').json()
            renewed = client.post('/api/runtime/manager-audit', json=dict(action='renew', project_id='test', generation=status['generation'])).json()
            self.assertFalse(renewed['enabled'])

    def test_real_host_middleware_refuses_anonymous_and_run_tokens(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        import app as host
        from manager_wakes import install
        application = FastAPI()
        self.r.running['lead'] = SimpleNamespace(room_token='disposable-run-token', id='run')
        with patch.object(host, 'app', application), patch.object(host, 'runtime', self.r), \
             patch.object(host, 'session_token', 'old-disposable-token'):
            host._install_security_middleware('disposable-ui-token', {'server': {'port': 63500}})
            install(self.r, SimpleNamespace(app=application))
            with TestClient(application, base_url='http://127.0.0.1:63500') as client:
                body = dict(action='enable', project_id='test', manager='lead', task_id='manager',
                            channel='ordinary', interval_minutes=15)
                for headers in ({}, {'x-session-token': 'disposable-run-token'}):
                    self.assertEqual(client.get('/api/runtime/manager-audit', headers=headers).status_code, 403)
                    self.assertEqual(client.post('/api/runtime/manager-audit', json=body, headers=headers).status_code, 403)
                headers = {'x-session-token': 'disposable-ui-token'}
                self.assertEqual(client.get('/api/runtime/manager-audit', headers=headers).status_code, 200)
                self.assertEqual(client.post('/api/runtime/manager-audit', json=body, headers=headers).status_code, 200)
        self.assertEqual(self.queued, [])


if __name__ == '__main__':
    unittest.main()
