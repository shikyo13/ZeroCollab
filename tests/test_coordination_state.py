import unittest

from zerocollab.coordination.coordination_state import execution_state, update_execution


class ExecutionStateTests(unittest.TestCase):
    def setUp(self):
        self.task = {'id': 't1', 'owner': 'astra', 'status': 'working', 'updated': 1}
        self.followup = {'id': 'f1', 'task_id': 't1', 'assigned_to': 'user', 'status': 'open'}
        self.state = {'tasks': [self.task], 'followups': [self.followup]}

    def waiting(self):
        return update_execution(self.task, self.state, next_action='Check the answer before installing',
                                wait={'kind': 'followup', 'record_id': 'f1'})

    def test_human_wait_does_not_expire_or_imply_approval(self):
        task = self.waiting()
        self.assertEqual(execution_state(task, self.state), 'waiting_on_human')
        for answer in ('resolved', 'cancelled'):
            self.followup['status'] = answer
            self.assertEqual(execution_state(task, self.state), 'needs_attention')
            self.assertEqual(task['status'], 'working')

    def test_invalid_link_is_atomic(self):
        for field, value in [('assigned_to', 'opus'), ('task_id', 'other'), ('status', 'resolved')]:
            original = self.followup[field]
            self.followup[field] = value
            with self.assertRaises(ValueError):
                self.waiting()
            self.assertNotIn('wait', self.task)
            self.followup[field] = original

    def test_execution_is_not_inferred_from_working_label(self):
        self.assertEqual(execution_state(self.task, self.state), 'idle')
        self.assertEqual(execution_state(self.task, self.state, queued=True), 'queued')
        self.assertEqual(execution_state(self.task, self.state, running=True), 'executing')

    def test_dependencies_and_cleared_wait(self):
        task = self.waiting()
        task = update_execution(task, self.state, wait={})
        task['depends_on'] = ['upstream']
        self.assertEqual(execution_state(task, self.state), 'waiting_on_dependency')
        self.state['tasks'].append({'id': 'upstream', 'status': 'done'})
        self.assertEqual(execution_state(task, self.state), 'idle')


class RecordIntegrationTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from zerocollab.coordination.project_room import ProjectRoom
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = SimpleNamespace(data=self.root / 'data', host=SimpleNamespace(router=None),
            states={name: {'worktree': str(self.root)} for name in ('astra', 'opus')},
            activity=lambda name: {'running': True, 'queued': 2})
        self.room = ProjectRoom(self.runtime)
        self.room.use_project('test')
        self.task = self.room.task('astra', 'create', title='Deliver feature', paths=['source.py'])
        self.ident = self.task['id']
        (self.root / 'source.py').write_text('original source', encoding='utf-8')

    def test_transfer_retains_owner_until_recipient_accepts(self):
        transfer = self.room.task('astra', 'delegate', self.ident, owner='opus')
        self.assertEqual(transfer['owner'], 'astra')
        self.assertEqual(transfer['transfer']['to'], 'opus')
        with self.assertRaises(ValueError):
            self.room.task('astra', 'accept', self.ident)
        accepted = self.room.task('opus', 'accept', self.ident)
        self.assertEqual(accepted['owner'], 'opus')
        self.assertNotIn('transfer', accepted)
        with self.assertRaises(ValueError):
            self.room.task('astra', 'update', self.ident, status='done')

    def test_release_cancels_transfer(self):
        self.room.task('astra', 'delegate', self.ident, owner='opus')
        self.room.task('astra', 'release', self.ident)
        with self.assertRaises(ValueError):
            self.room.task('opus', 'accept', self.ident)

    def test_human_wait_and_evidence_are_separate_from_activity(self):
        request = self.room.records.followup('astra', 'create', task_id=self.ident,
            assigned_to='user', text='May this be installed?')['item']
        self.room.task('astra', 'update', self.ident, status='working', next_action='Check answer',
            wait={'kind':'followup', 'record_id':request['id']}, evidence={'implementation':'built'})
        self.room.state['tasks'][0]['updated'] = 1
        task = self.room.snapshot()['tasks'][0]
        self.assertEqual(task['execution_state'], 'waiting_on_human')
        self.assertFalse(task['stale'])
        self.assertEqual(task['evidence'], {'implementation':'built'})
        self.room.records.followup('user', 'resolve', followup_id=request['id'], result='No')
        self.assertEqual(self.room.task_view(self.room.state['tasks'][0])['execution_state'], 'needs_attention')
        with self.assertRaises(ValueError):
            self.room.task('astra', 'update', self.ident, status='done')
        task = self.room.task('astra', 'update', self.ident, wait={})
        self.assertEqual(task['execution_state'], 'idle')  # owner busy is not task execution

    def test_review_snapshot_is_immutable_and_changed_source_cannot_pass(self):
        from types import SimpleNamespace
        item = self.room.records.review('astra', 'request', task_id=self.ident,
            reviewer='opus', revision='v1', text='Check source')['item']
        (self.root / 'source.py').write_text('changed source', encoding='utf-8')
        artifact = self.room.read(SimpleNamespace(), section='review_artifact', query=item['id']+':source.py')
        self.assertEqual(artifact['text'], 'original source')
        with self.assertRaisesRegex(ValueError, 'files changed'):
            self.room.records.review('opus', 'respond', review_id=item['id'], revision='v1',
                verdict='approved', text='checked')
        (self.root / 'source.py').write_text('original source', encoding='utf-8')
        self.room.records.review('opus', 'respond', review_id=item['id'], revision='v1',
            verdict='approved', text='checked')
        (self.root / 'source.py').write_text('changed again', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.room.task('astra', 'update', self.ident, status='done')

    def test_requester_can_close_verified_followup_and_notify_assignee(self):
        item = self.room.records.followup('astra', 'create', task_id=self.ident,
            assigned_to='opus', text='Check output')['item']
        result = self.room.records.followup('astra', 'resolve', followup_id=item['id'],
            result='Verified output at the recorded revision')
        self.assertEqual(result['item']['status'], 'resolved')
        self.assertEqual(result['notify'], ['opus'])

    def test_decision_replacement_keeps_history(self):
        old = self.room.note('astra', 'decision', 'Old direction')
        new = self.room.note('astra', 'decision', 'New direction', supersedes=old['id'])
        self.assertEqual(old['status'], 'superseded')
        self.assertEqual(old['replaced_by'], new['id'])
        self.assertEqual(new['status'], 'active')
        human = self.room.note('user', 'decision', 'Keep live data')
        with self.assertRaises(ValueError):
            self.room.note('astra', 'decision', 'Retire', resolves=human['id'])

    def test_conflict_returns_existing_identity_without_mutation(self):
        import json
        before = json.dumps(self.room.state)
        for title, paths in [('deliver FEATURE', []), ('Different title', ['source.py'])]:
            with self.assertRaises(ValueError) as raised:
                self.room.task('opus', 'create', title=title, paths=paths)
            self.assertIn(self.ident, str(raised.exception))
            self.assertIn('"owner": "astra"', str(raised.exception))
            self.assertIn('"status": "open"', str(raised.exception))
            self.assertEqual(json.dumps(self.room.state), before)

    def test_references_survive_progress_and_preserve_replacement_history(self):
        old = self.room.note('astra', 'reference', 'Cache: research/cache.json')
        for i in range(105):
            self.room.note('astra', 'progress', str(i))
        new = self.room.note('opus', 'reference', 'Cache moved: research/new.json', supersedes=old['id'])
        self.assertIn(old, self.room.state['notes'])
        self.assertEqual(old['status'], 'superseded')
        self.assertEqual(new['status'], 'active')
        with self.assertRaises(ValueError):
            self.room.note('opus', 'decision', 'Wrong kind', resolves=new['id'])
        self.room.note('opus', 'reference', 'Cache retired', resolves=new['id'])
        self.assertEqual(new['status'], 'resolved')

    def test_ready_tasks_respect_dependencies_and_priority(self):
        second = self.room.task('astra', 'create', title='Downstream', depends_on=[self.ident], priority=1)
        self.assertEqual([t['id'] for t in self.room.records.inbox('astra')['ready_tasks']], [self.ident])
        self.room.task('astra', 'update', self.ident, status='done')
        self.assertEqual([t['id'] for t in self.room.records.inbox('astra')['ready_tasks']], [second['id']])


if __name__ == '__main__':
    unittest.main()

