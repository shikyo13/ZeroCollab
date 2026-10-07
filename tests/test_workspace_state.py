import asyncio
import json
import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from room_runtime import RoomRuntime, Run, execute_file_tool, file_tool_target, git, load_workspace, save_workspace
from coordination_queue import WakeQueue
from project_room import ProjectRoom
import project_registry

class WorkspacePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'workspace.json'
        self.old = {'project': 'first', 'project_id': 'one', 'paused': False}
        self.new = {'project': 'second', 'project_id': 'two', 'paused': False}

    def test_roundtrip_and_backup(self):
        self.assertEqual(load_workspace(self.path), {})
        save_workspace(self.path, self.old)
        save_workspace(self.path, self.new)
        self.assertEqual(load_workspace(self.path), self.new)
        self.assertEqual(json.loads(self.path.with_suffix('.json.bak').read_text()), self.old)

    def test_failed_replace_keeps_selection(self):
        save_workspace(self.path, self.old)
        with patch('room_runtime.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                save_workspace(self.path, self.new)
        self.assertEqual(load_workspace(self.path), self.old)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_corrupt_primary_recovers_paused_and_keeps_backup(self):
        save_workspace(self.path, self.old)
        save_workspace(self.path, self.new)
        self.path.write_text('{torn')
        with self.assertLogs('room_runtime', level='WARNING'):
            recovered = load_workspace(self.path)
        self.assertEqual(recovered, {**self.old, 'paused': True})
        save_workspace(self.path, recovered)
        self.assertEqual(json.loads(self.path.with_suffix('.json.bak').read_text()), self.old)

    def test_invalid_both_fails_explicitly(self):
        self.path.write_text('null')
        with self.assertRaisesRegex(RuntimeError, 'Cannot recover workspace'):
            load_workspace(self.path)

    def test_invalid_project_shape_recovers(self):
        save_workspace(self.path, self.old)
        save_workspace(self.path, self.new)
        self.path.write_text('{"project": 123}')
        with self.assertLogs('room_runtime', level='WARNING'):
            self.assertTrue(load_workspace(self.path)['paused'])


class RepositoryChangeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        r = RoomRuntime.__new__(RoomRuntime)
        r.data, r.identity, r.project = self.root / 'data', 'aaaaaaaa', self.root / 'old'
        folder = r.data / 'projects' / r.identity
        folder.mkdir(parents=True)
        (folder / 'coordination.json').write_text('{"tasks": [], "notes": []}')
        project_registry._save_record(folder, {'path': str(r.project), 'sessions': [{'id': 'chat1', 'title': 'Keep'}], 'name': 'Keep'})
        r.project_room = ProjectRoom(r)
        r.project_room.use_project(r.identity)
        r.goals = SimpleNamespace(goal=None)
        r.states = {n: {'worktree': str(self.root / 'old' / n), 'branch': 'old', 'workspace_kind': 'repository'} for n in ('a', 'b')}
        r.queues = {n: WakeQueue(maxsize=4) for n in r.states}
        self.queued = {'channel': 'chat1', 'requests': [{'prompt': 'preserve'}]}
        r.queues['a'].put_nowait(self.queued)
        self.held = {'channel': 'chat1', 'requests': [{'prompt': 'held'}]}
        r.running, r.deploy_held = {}, {'b': self.held}
        r.paused, r.deployment = False, None
        r.repository_transition, r.repository_job = None, None
        r.profile_lock = asyncio.Lock()
        r.save_project, r.publish_soon = Mock(), Mock()
        r.publish_status = AsyncMock()
        r.repository_workspace = Mock(side_effect=lambda p, n: {'worktree': str(p / n), 'branch': 'new', 'workspace_kind': 'repository'})
        self.r = r
        self.target = self.root / 'new'
        self.enterContext(patch('project_registry.repository_root', side_effect=lambda p: Path(p)))

    async def test_drain_keeps_queued_and_held_work_and_honors_new_pause(self):
        r = self.r
        r.running['a'] = object()
        arrived = asyncio.Event()
        r.publish_status.side_effect = lambda: arrived.set()
        job = r.start_repository_change(r.identity, self.target)
        await arrived.wait()
        self.assertFalse(r.turn_admission_open())
        self.assertEqual(r.project, self.root / 'old')
        self.assertEqual(r.repository_progress()['waiting_on'], ['a'])
        r.paused = True  # A user Stop during the wait must not be undone.
        r.running.clear()
        result = await job
        self.assertEqual(result['path'], str(self.target))
        self.assertIs(r.queues['a']._queue[0], self.queued)
        self.assertIs(r.deploy_held['b'], self.held)
        self.assertTrue(r.paused)
        self.assertEqual(result['sessions'][0]['id'], 'chat1')
        self.assertEqual(result['repository_history'][0]['path'], str(self.root / 'old'))
        self.assertIsNotNone(result['repository_history'][0]['detached'])

    async def test_preparation_failure_does_not_publish_any_workspaces(self):
        old = {n: dict(s) for n, s in self.r.states.items()}
        self.r.repository_workspace.side_effect = [dict(worktree='first prepared', branch='new', workspace_kind='repository'), OSError('second failed')]
        with self.assertRaisesRegex(OSError, 'second failed'):
            await self.r.start_repository_change(self.r.identity, self.target)
        self.assertEqual(self.r.states, old)
        self.assertEqual(self.r.project, self.root / 'old')
        self.assertIsNone(self.r.repository_transition)
        self.assertTrue(self.r.turn_admission_open())

    async def test_failed_project_record_write_keeps_old_selection(self):
        with patch('project_registry._save_record', side_effect=OSError('disk failure')):
            with self.assertRaisesRegex(OSError, 'disk failure'):
                await self.r.start_repository_change(self.r.identity, self.target)
        self.assertEqual(self.r.project, self.root / 'old')
        self.assertEqual(self.r.states['a']['branch'], 'old')

    async def test_cancel_during_prepare_keeps_admission_until_worker_finishes(self):
        entered, release = threading.Event(), threading.Event()
        def prepare(path, name):
            entered.set()
            release.wait(3)
            return {'worktree': str(path / name), 'branch': 'new', 'workspace_kind': 'repository'}
        self.r.repository_workspace.side_effect = prepare
        job = self.r.start_repository_change(self.r.identity, self.target)
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            self.r.repository_control(self.r.identity, 'cancel')
            self.assertFalse(self.r.turn_admission_open())
            self.assertFalse(job.done())
        finally:
            release.set()
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            await job
        self.assertEqual(self.r.project, self.root / 'old')
        self.assertIs(self.r.queues['a']._queue[0], self.queued)

    async def test_disconnected_caller_does_not_cancel_owned_job_or_deploy_count(self):
        r = self.r
        r.deployment = Mock()
        r.running['a'] = object()
        arrived = asyncio.Event()
        r.publish_status.side_effect = lambda: arrived.set()
        job = r.start_repository_change(r.identity, self.target)
        async def request():
            return await asyncio.shield(job)
        caller = asyncio.create_task(request())
        await arrived.wait()
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        self.assertFalse(job.done())
        r.deployment.track_continuation.assert_called_once()
        r.deployment.leave_mutation.assert_not_called()
        r.running.clear()
        await job
        r.deployment.leave_mutation.assert_called_once()

    async def test_first_attach_and_failed_workspace_summary_use_committed_record(self):
        self.r.project = None
        self.r.save_project.side_effect = OSError('summary failure')
        with self.assertLogs('room_runtime', level='ERROR'):
            await self.r.start_repository_change(self.r.identity, self.target)
        saved = project_registry.record(project_registry.active_folder(self.r))
        self.assertEqual(saved['path'], str(self.target))
        self.assertEqual(len(saved['repository_history']), 1)
        self.assertEqual(self.r.project, self.target)

    async def test_interrupt_cancels_only_active_turns_not_held_entries(self):
        self.r.running['a'] = object()
        async def cancel(name):
            self.r.running.pop(name)
        self.r.cancel_turn = AsyncMock(side_effect=cancel)
        await self.r.start_repository_change(self.r.identity, self.target, interrupt=True)
        self.r.cancel_turn.assert_awaited_once_with('a')
        self.assertIs(self.r.deploy_held['b'], self.held)
        self.assertIs(self.r.queues['a']._queue[0], self.queued)

    async def test_agent_can_request_replacement_and_duplicate_switch_is_refused(self):
        request = project_registry.request_repository(self.r, 'a', str(self.target), reason='Move source')
        self.assertTrue(request['replace'])
        self.assertEqual(request['current'], str(self.r.project))
        self.assertEqual(self.r.project, self.root / 'old')
        job = self.r.start_repository_change(self.r.identity, self.target)
        with self.assertRaisesRegex(ValueError, 'already in progress'):
            self.r.start_repository_change(self.r.identity, self.target)
        await job

    async def test_add_select_primary_remove_preserves_other_roots_and_queue(self):
        old = self.r.project
        old_key, new_key = map(project_registry.repository_key, (old, self.target))
        result = await self.r.start_repository_change(self.r.identity, self.target, action='add')
        self.assertEqual(self.r.project, old)
        self.assertEqual(len(result['repositories']), 2)
        self.assertEqual(set(self.r.states['a']['repositories']), {old_key, new_key})
        await self.r.start_repository_change(self.r.identity, None, action='primary', key=new_key)
        self.assertEqual(self.r.project, self.target)
        self.assertEqual(len(project_registry.repositories(self.r)), 2)
        result = await self.r.start_repository_change(self.r.identity, None, action='remove', key=old_key)
        self.assertEqual([r['key'] for r in result['repositories']], [new_key])
        self.assertIs(self.r.queues['a']._queue[0], self.queued)
        self.assertIsNotNone(next(h for h in result['repository_history'] if h['key'] == old_key)['detached'])

    async def test_reservations_pin_roots_and_block_alias_bypass_and_removal(self):
        room = self.r.project_room
        old_key = project_registry.repository_key(self.r.project)
        original = room.task('a', 'create', title='Original', paths=['src'])
        await self.r.start_repository_change(self.r.identity, self.target, action='add')
        with self.assertRaisesRegex(ValueError, 'Scope already reserved'):
            room.task('b', 'create', title='Alias', paths=[old_key + ':src/file.py'])
        new_key = project_registry.repository_key(self.target)
        room.task('b', 'create', title='Other repository', paths=[new_key + ':src'])
        await self.r.start_repository_change(self.r.identity, None, action='primary', key=new_key)
        self.assertEqual(original['repository_key'], old_key)
        with self.assertRaises(project_registry.RepositoryInUse):
            await self.r.start_repository_change(self.r.identity, None, action='remove', key=old_key)
        room.task('a', 'release', task_id=original['id'])
        await self.r.start_repository_change(self.r.identity, None, action='remove', key=old_key)

    async def test_review_files_pin_each_root_after_primary_change_and_reject_escape(self):
        await self.r.start_repository_change(self.r.identity, self.target, action='add')
        old_key = project_registry.repository_key(self.r.project)
        new_key = project_registry.repository_key(self.target)
        for key, repo in self.r.states['a']['repositories'].items():
            root = Path(repo['worktree'])
            root.mkdir(parents=True)
            (root / 'file.py').write_text(key)
        task = {'owner': 'a', 'paths': ['file.py', new_key + ':file.py'], 'repository_key': old_key}
        snapshot, blobs = self.r.project_room.records.capture(task)
        self.assertEqual(len(blobs), 2)
        review = {'snapshot': snapshot}
        await self.r.start_repository_change(self.r.identity, None, action='primary', key=new_key)
        self.assertEqual(self.r.project_room.records.changed_files(review), [])
        Path(snapshot['files'][0]['root'], 'file.py').write_text('changed')
        self.assertEqual(self.r.project_room.records.changed_files(review), ['file.py'])
        with self.assertRaisesRegex(ValueError, 'inside the owner workspace'):
            self.r.project_room.records.capture(task, [new_key + ':../escape.py'])
        await self.r.start_repository_change(self.r.identity, None, action='remove', key=old_key)
        old_snapshot, _ = self.r.project_room.records.capture(task, ['file.py'])
        self.assertEqual(old_snapshot['files'][0]['root'], snapshot['files'][0]['root'])

    async def test_add_request_and_labeled_diff_payload(self):
        request = project_registry.request_repository(self.r, 'a', str(self.target), reason='Extra source', action='add')
        self.assertEqual(request['action'], 'add')
        self.assertFalse(request['replace'])
        await self.r.start_repository_change(self.r.identity, self.target, action='add')
        for state in self.r.states.values():
            state['provider'] = 'api'
        with patch('room_runtime._worktree_diff', return_value='diff'):
            diffs = await self.r.collect_diffs()
        self.assertIsInstance(diffs['a'], str)
        for key in self.r.states['a']['repositories']:
            self.assertIn('[Repository ' + key + ']', diffs['a'])
        with patch('room_runtime._worktree_diff', return_value=''):
            self.assertEqual(await self.r.collect_diffs(), {'a': '', 'b': ''})

    async def test_extra_instructions_change_session_hash_and_local_file_target_is_confined(self):
        r = self.r
        r.controls = SimpleNamespace(values={'a': {}})
        r.cfg, r.profiles = {}, SimpleNamespace(items={})
        before = Run(r, 'a', self.queued)
        await r.start_repository_change(r.identity, self.target, action='add')
        after = Run(r, 'a', self.queued)
        key = project_registry.repository_key(self.target)
        root = Path(r.states['a']['repositories'][key]['worktree'])
        self.assertIn(f'{key} -> {root}', after.instructions)
        self.assertNotEqual(before.instruction_hash, after.instruction_hash)
        selected, params = file_tool_target(after.worktree, r.states['a']['repositories'],
                                          {'path': key + ':src/file.py', 'content': 'extra'})
        execute_file_tool(selected, 'write_file', params)
        self.assertEqual((root / 'src/file.py').read_text(), 'extra')
        self.assertFalse((after.worktree / 'src/file.py').exists())
        for path in (key + ':../escape.py', key + ':.git/config', 'unknown:file.py'):
            with self.assertRaises(ValueError):
                file_tool_target(after.worktree, r.states['a']['repositories'], {'path': path})


class RepositoryWorktreeTests(unittest.TestCase):
    def test_repository_identity_preserves_legacy_and_uncommitted_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repos = [root / 'one' / 'app', root / 'two' / 'app']
            for repo in repos:
                repo.mkdir(parents=True)
                git(repo, 'init')
                git(repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-m', 'initial')
            r = RoomRuntime.__new__(RoomRuntime)
            r.data, r.identity = root / 'data', 'aaaaaaaa'
            legacy = r.data / 'worktrees' / r.identity / 'a'
            legacy.parent.mkdir(parents=True)
            git(repos[0], 'worktree', 'add', '-b', 'legacy-a', str(legacy), 'HEAD')
            (legacy / 'uncommitted.txt').write_text('keep me')
            old = r.repository_workspace(repos[0], 'a')
            new = r.repository_workspace(repos[1], 'a')
            self.assertEqual(old['worktree'], str(legacy))
            self.assertNotEqual(old['worktree'], new['worktree'])
            self.assertIn(project_registry.repository_key(repos[1]), new['worktree'])
            self.assertNotEqual(project_registry.repository_key(repos[0]), project_registry.repository_key(repos[1]))
            self.assertEqual((legacy / 'uncommitted.txt').read_text(), 'keep me')
            self.assertEqual(r.repository_workspace(repos[0], 'a'), old)
            self.assertEqual(r.repository_workspace(repos[1], 'a'), new)
