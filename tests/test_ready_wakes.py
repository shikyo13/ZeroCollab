"""Stand-in checks for the approved ready-task wake contract (r2.1 ea12acc9 + r2.2 9e1bad8b), one per case.

Real ProjectRoom task writes and the real ReadyWakes module; the runtime around them is a minimal stand-in,
as in test_room_goals. No model, provider, network or live room.
"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import roomdb
from project_room import ProjectRoom
from ready_wakes import ReadyWakes


class ReadyWakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls = []
        r = SimpleNamespace(data=Path(self.tmp.name), project=None, paused=False, deployment=None,
                            running={}, deploy_held={}, repository_transition=None,
                            states={n: {'state': 'ready'} for n in ('worker', 'lead', 'peer')},
                            queues={n: asyncio.Queue() for n in ('worker', 'lead', 'peer')},
                            goals=SimpleNamespace(goal=None))
        r.turn_admission_open = lambda: True
        r.project_room = ProjectRoom(r)
        r.project_room.use_project('p')
        self.queued_ok = True

        def enqueue(name, channel, prompt='', chain=None, routing=None, **_):
            # The claim must already be durable when the wake is queued (r2.2 atomicity).
            saved = roomdb.read_state(r.project_room.path.parent)
            self.calls.append({'name': name, 'chain': chain, 'routing': routing, 'prompt': prompt,
                               'durable': routing['ready_wake'] in saved['ready_wakes']['claims']})
            if self.queued_ok:
                r.queues[name].put_nowait({'channel': channel, 'requests': [{'chain': chain, **routing}]})
            return self.queued_ok
        r.enqueue = enqueue
        self.r, self.room = r, r.project_room
        self.wakes = r.ready_wakes = ReadyWakes(r)
        self.room.change_limits({'ready_task_wakes': 1}, save=False)
        self.chain = self.room.new_chain('general')
        task = self.room.task('lead', 'create', title='Implement parser', owner='worker', next_action='write parse()')
        self.task_id = task['id']
        self.wakes.record_chain(self.task_id, self.chain)

    def task(self):
        return next(t for t in self.room.state['tasks'] if t['id'] == self.task_id)

    def ready(self, actor='worker'):
        return self.room.task(actor, 'update', task_id=self.task_id, next_action_state='ready')

    def drain(self, name='worker'):
        queue = self.r.queues[name]
        return queue.get_nowait() if not queue.empty() else None

    def test_off_by_default_and_never_inferred(self):
        self.room.change_limits({'ready_task_wakes': 0}, save=False)
        self.ready()
        self.assertEqual(self.wakes.schedule(), [])
        self.room.change_limits({'ready_task_wakes': 1}, save=False)
        self.room.task('worker', 'update', task_id=self.task_id, next_action_state='')
        self.assertEqual(self.wakes.schedule(), [], 'open status and a next_action alone are not readiness')

    def test_initial_readiness_wakes_once_and_owner_edits_cannot_rearm(self):
        self.ready()
        self.assertEqual(self.wakes.schedule(), ['worker'])
        self.assertTrue(self.calls[0]['durable'])
        self.assertIn('initial readiness', self.calls[0]['prompt'])
        self.drain()
        self.assertEqual(self.wakes.schedule(), [])
        # The owner's own edits, including toggling its readiness, never re-arm the consumed revision.
        self.room.task('worker', 'update', task_id=self.task_id, next_action='retry parse()', update='progress')
        self.room.task('worker', 'update', task_id=self.task_id, next_action_state='waiting')
        self.ready()
        self.assertEqual(self.wakes.schedule(), [])
        self.assertEqual(len(self.calls), 1)

    def test_external_change_arms_one_new_wake(self):
        self.ready(); self.wakes.schedule(); self.drain()
        self.room.task('user', 'update', task_id=self.task_id, next_action='also handle comments')
        self.assertEqual(self.task()['work_rev'], 1)
        self.assertEqual(self.wakes.schedule(), ['worker'])
        self.assertIn('external change by user', self.calls[-1]['prompt'])
        self.drain()
        self.assertEqual(self.wakes.schedule(), [])

    def test_wake_history_can_build_owner_turn_context(self):
        self.ready()
        self.assertEqual(self.wakes.schedule(), ['worker'])
        request = self.drain()['requests'][0]
        self.assertTrue(self.wakes.accepts(request))
        latest = self.task()['updates'][-1]
        self.r.host = SimpleNamespace(store=SimpleNamespace(get_recent=lambda *a, **k: []))
        run = SimpleNamespace(name='worker', channel='general', previous_session={},
                              entry={'chain': self.chain}, worktree=self.tmp.name, receipts=set())
        context = self.room.context(run)
        self.assertIn('last change', context)
        self.assertIn(latest['text'], context)

    def test_release_then_claim_cannot_self_rearm(self):
        self.ready(); self.assertEqual(self.wakes.schedule(), ['worker']); self.drain()
        self.room.task('worker', 'release', task_id=self.task_id)
        self.room.task('worker', 'claim', task_id=self.task_id)
        self.ready()
        self.assertEqual(self.task()['work_rev'], 0, 'release and claim by the owner are not external changes')
        self.assertEqual(self.wakes.schedule(), [])
        self.assertEqual(len(self.calls), 1)

    def test_only_owner_or_user_sets_readiness(self):
        with self.assertRaises(ValueError):
            self.room.task('peer', 'update', task_id=self.task_id, next_action_state='ready')
        self.ready('user')
        self.assertEqual(self.task()['next_action_state'], 'ready')

    def test_owner_change_resets_readiness(self):
        self.ready()
        self.room.task('worker', 'delegate', task_id=self.task_id, owner='peer')
        self.room.task('peer', 'accept', task_id=self.task_id)
        self.assertNotIn('next_action_state', self.task())
        self.assertEqual(self.wakes.schedule(), [])

    def test_busy_agent_coalesces_without_claiming(self):
        self.ready()
        self.r.queues['worker'].put_nowait({'channel': 'general', 'requests': [{'prompt': 'review'}]})
        self.assertEqual(self.wakes.schedule(), [])
        self.assertEqual(self.wakes.record['claims'], {}, 'nothing is claimed while existing work covers the agent')
        self.drain()
        self.assertEqual(self.wakes.schedule(), ['worker'])

    def test_missing_or_spent_chain_is_refused_never_replaced(self):
        self.ready()
        self.room.chains.pop(self.chain)
        self.assertEqual(self.wakes.schedule(), [])
        claim = next(iter(self.wakes.record['claims'].values()))
        self.assertEqual((claim['status'], claim['reason']), ('refused', 'its conversation is no longer active'))
        self.assertEqual(self.calls, [])
        # A spent budget on a live chain is also refused, after a new external change.
        chain = self.room.new_chain('general'); self.wakes.record_chain(self.task_id, chain)
        self.room.limits['agent_turns'] = 2; self.room.chains[chain]['turns'] = 2
        self.room.task('user', 'update', task_id=self.task_id, next_action='retry')
        self.wakes.schedule()
        self.assertEqual(sorted(c['status'] for c in self.wakes.record['claims'].values()), ['refused', 'refused'])
        self.assertEqual(self.calls, [])

    def test_hourly_ceiling_refusal_consumes_the_revision(self):
        self.ready()
        for n in range(3):
            self.assertEqual(self.wakes.schedule(), ['worker']); self.drain()
            self.room.task('user', 'update', task_id=self.task_id, next_action=f'step {n}')
        self.assertEqual(self.wakes.schedule(), [])
        key = self.wakes.key(self.task_id, 'worker', 3)
        self.assertEqual(self.wakes.record['claims'][key]['status'], 'refused')
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.wakes.schedule(), [], 'the refused revision is not retried')

    def test_dispatch_rechecks_switch_readiness_owner_and_excludes_own_entry(self):
        def queued_request():
            self.ready(); self.wakes.schedule()
            return self.drain()['requests'][0]
        # Own entry (W2): once dequeued, run_turn marks the worker waiting while it holds this very batch;
        # that never counts as other work, so the wake is accepted.
        request = queued_request()
        self.r.states['worker']['waiting'] = True
        self.assertTrue(self.wakes.accepts(request))
        self.r.states['worker'].pop('waiting')
        # W1: switch turned off before dispatch.
        self.room.task('user', 'update', task_id=self.task_id, next_action='a')
        self.wakes.schedule(); request = self.drain()['requests'][0]
        self.room.change_limits({'ready_task_wakes': 0}, save=False)
        self.assertFalse(self.wakes.accepts(request))
        self.room.change_limits({'ready_task_wakes': 1}, save=False)
        # W1: readiness withdrawn by the owner before dispatch.
        self.room.task('user', 'update', task_id=self.task_id, next_action='b')
        self.wakes.schedule(); request = self.drain()['requests'][0]
        self.room.task('worker', 'update', task_id=self.task_id, next_action_state='deferred')
        self.assertFalse(self.wakes.accepts(request))
        statuses = [c['status'] for c in self.wakes.record['claims'].values()]
        self.assertEqual(statuses.count('dropped'), 2)
        self.assertTrue(any('disabled before dispatch' in u['text'] for u in self.task()['updates']))

    def test_stopped_room_or_agent_claims_nothing(self):
        self.ready()
        self.r.paused = True
        self.assertEqual(self.wakes.schedule(), [])
        self.r.paused = False
        self.r.states['worker']['paused'] = True
        self.assertEqual(self.wakes.schedule(), [])
        self.assertEqual(self.wakes.record['claims'], {})

    def test_refused_enqueue_consumes_without_retry(self):
        self.ready(); self.queued_ok = False
        self.assertEqual(self.wakes.schedule(), [])
        self.queued_ok = True
        self.assertEqual(self.wakes.schedule(), [])
        self.assertEqual(len(self.calls), 1)

    def test_dependency_finish_and_review_verdict_are_external_changes(self):
        upstream = self.room.task('peer', 'create', title='Grammar', owner='peer')
        self.room.task('worker', 'update', task_id=self.task_id, depends_on=[upstream['id']], next_action_state='ready')
        self.assertEqual(self.wakes.schedule(), [], 'waiting on an unfinished dependency')
        self.room.task('peer', 'update', task_id=upstream['id'], status='done')
        self.assertEqual(self.task()['work_rev'], 1)
        self.assertEqual(self.wakes.schedule(), ['worker']); self.drain()
        self.assertTrue(self.room.bump_work(self.task_id, 'peer', 'review verdict by peer'))
        self.assertFalse(self.room.bump_work(self.task_id, 'worker', 'own'), "the owner's own records never count")
        self.assertEqual(self.wakes.schedule(), ['worker'])

    def test_existing_wake_covers_the_revision(self):
        self.ready()
        self.wakes.cover('worker', [{'task_id': self.task_id, 'prompt': 'Task start'}])
        self.assertEqual(self.wakes.schedule(), [])
        self.room.task('user', 'update', task_id=self.task_id, next_action='new')
        self.assertEqual(self.wakes.schedule(), ['worker'])

    def test_claims_survive_reload(self):
        self.ready(); self.wakes.schedule(); self.drain()
        self.room.use_project('p')  # reload from disk, as after a restart or project switch
        self.assertEqual(self.wakes.schedule(), [])
        self.assertEqual(len(self.calls), 1)


if __name__ == '__main__':
    unittest.main()
