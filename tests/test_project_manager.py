"""Reported setup/Claude protocol regressions, disposable room and transport only."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from manager_wakes import ManagerWakes, install
from project_room import ProjectRoom
from ready_wakes import ReadyWakes
import native_adapters as native


class SetupChecks(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.r = SimpleNamespace(data=root / 'room-data', project=root / 'project', identity='fixture',
            states={'lead': {'provider': 'claude', 'state': 'ready', 'label': 'Opus'},
                    'other': {'provider': 'codex', 'state': 'ready'},
                    'old': {'provider': 'claude', 'archived': True}},
            paused=False, deployment=None, running={}, queues={}, deploy_held={},
            goals=SimpleNamespace(goal=None), owns_channel=lambda c: c == 'chat',
            default_channel=lambda: 'chat', decisions=SimpleNamespace(routes=lambda c: c == 'chat'),
            turn_admission_open=lambda: True)
        self.r.project.mkdir()
        self.r.data.mkdir()
        self.pr = self.r.project_room = ProjectRoom(self.r)
        self.pr.use_project('fixture')
        self.pr.state['responders'] = {'lead': 'lead', 'backup': 'other'}
        self.m = self.r.manager_wakes = ManagerWakes(self.r)
        self.r.ready_wakes = ReadyWakes(self.r)
        self.m.channels = lambda: [{'id': 'chat', 'title': 'General'}]

    def enable(self, **kw):
        return self.m.configure_project_manager('user', enabled=True, manager='lead', **kw)

    def test_default_setup_creates_one_real_coordination_task_and_claude_is_eligible(self):
        self.assertFalse(self.m.status()['enabled'])
        status = self.enable()
        self.assertEqual(status['selected_manager']['id'], 'lead')
        self.assertTrue(status['selected_manager']['eligible'])
        self.assertEqual(status['scope']['mode'], 'project')
        task = next(t for t in self.pr.state['tasks'] if t['id'] == status['task']['id'])
        self.assertTrue(task['manager_coordination'])
        self.assertEqual(task['owner'], 'lead')
        self.enable()
        self.assertEqual(len(self.pr.state['tasks']), 1)
        self.assertNotIn('old', [m['id'] for m in status['options']['managers']])

    def test_real_routes_need_no_task_manager_or_path_fields_and_reject_stale_client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        install(self.r, SimpleNamespace(app=app))
        with TestClient(app) as client:
            body = {'action': 'enable', 'project_id': 'fixture', 'interval_minutes': 30}
            self.assertEqual(client.post('/api/runtime/manager-audit', json=body).status_code, 200)
            before = copy.deepcopy(self.pr.state)
            self.assertEqual(client.post('/api/runtime/manager-audit', json=body | {'manager': 'other'}).status_code, 400)
            self.assertEqual(self.pr.state, before)
            generation = self.m.record['generation']
            self.assertEqual(client.post('/api/runtime/manager-audit', json={'action': 'disable', 'project_id': 'fixture'}).status_code, 200)
            self.assertEqual(client.post('/api/runtime/manager-audit', json={'action': 'renew', 'project_id': 'fixture', 'generation': generation}).status_code, 409)
            generation = self.m.record['generation']
            reply = client.post('/api/runtime/manager-audit', json={'action': 'renew', 'project_id': 'fixture', 'generation': generation})
            self.assertEqual(reply.status_code, 200)
            self.assertFalse(reply.json()['enabled'])

    def test_bad_save_does_not_create_task_or_lose_saved_configuration(self):
        for values in ({'interval_minutes': 1}, {'channel': 'unrelated'}):
            before = copy.deepcopy(self.pr.state)
            with self.assertRaises(ValueError):
                self.enable(**values)
            self.assertEqual(self.pr.state, before)

    def test_role_change_preserves_credits_transfers_marked_task_and_cancels_old_claim(self):
        self.enable()
        task_id = self.m.record['task']
        self.m.record['chain']['turns'] = 7
        self.m.record['claims']['old'] = {'status': 'sent', 'generation': self.m.record['generation']}
        self.pr.state['responders']['lead'] = 'other'
        self.m.manager_changed('other')
        self.assertEqual(self.m.record['chain']['turns'], 7)
        self.assertEqual(self.m.record['claims']['old']['status'], 'cancelled')
        self.assertEqual(self.m.record['task'], task_id)
        self.assertEqual(self.pr.state['tasks'][0]['owner'], 'other')
        self.assertTrue(self.m.record['enabled'])

    def test_load_mismatch_turns_old_manager_off_without_replenishing_credits(self):
        self.enable()
        self.m.record['chain']['turns'] = 4
        self.pr.state['responders']['lead'] = 'other'
        restored = ManagerWakes(self.r)
        restored.restore()
        self.assertFalse(restored.record['enabled'])
        self.assertEqual(restored.record['chain']['turns'], 4)
        self.assertEqual(restored.record['manager'], 'other')

    def test_old_narrow_scope_is_preserved_and_unrelated_project_data_is_not_nerfed(self):
        self.enable()
        self.assertTrue(self.m.path_allowed('data/game-save.py'))
        self.assertTrue(self.m.path_allowed('run.py'))
        self.m.record['planning_paths'] = ['frontend']
        status = self.enable()
        self.assertEqual(status['scope']['mode'], 'folders')
        self.assertTrue(self.m.path_allowed('frontend/src/a.tsx'))
        self.assertFalse(self.m.path_allowed('backend/a.py'))

    def test_room_app_scope_exclusions_do_not_apply_to_other_projects(self):
        for name in ('room_runtime.py', 'project_room.py', 'room_mcp.py'):
            (self.r.project / name).touch()
        self.enable()
        self.assertFalse(self.m.path_allowed('supervisor.py'))
        self.assertFalse(self.m.path_allowed('data/room.json'))
        self.assertTrue(self.m.path_allowed('frontend/src/Team.tsx'))


class ClaudeProtocolChecks(unittest.IsolatedAsyncioTestCase):
    async def test_control_reply_before_init_sends_prompt_then_room_tool_without_human_prompt(self):
        output = asyncio.StreamReader()
        writes, args_seen = [], []
        class Input:
            def write(self, wire):
                obj = json.loads(wire)
                writes.append(obj)
                if obj.get('request_id') == 'room-init':
                    output.feed_data((json.dumps({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': 'room-init'}}) + '\n').encode())
                elif obj['type'] == 'user':
                    messages = [
                        {'type': 'system', 'subtype': 'init', 'permissionMode': 'manual', 'tools': ['mcp__zerocollab_coordination__room_task']},
                        {'type': 'control_request', 'request_id': 'tool', 'request': {'subtype': 'can_use_tool', 'tool_name': 'mcp__zerocollab_coordination__room_task', 'input': {'action': 'get'}}},
                        {'type': 'result', 'result': ''}]
                    for msg in messages:
                        output.feed_data((json.dumps(msg) + '\n').encode())
            async def drain(self):
                pass
        async def spawn(run, args, env):
            args_seen.extend(args)
            return SimpleNamespace(stdin=Input(), stdout=output)
        run = SimpleNamespace(manager_audit='claim', automatic_tool_approvals=True, previous_session={'id': 'old'},
            instructions='', settings={'model': '', 'effort': ''}, provider_session=None, text='', published_messages=[],
            report_settings=AsyncMock(), report_permissions=AsyncMock(), approval=AsyncMock())
        with patch.object(native, 'spawn', spawn), patch.object(native, 'command', return_value=['claude']), \
             patch.object(native, 'room_server', return_value={}), patch.object(native, 'relay_setup', return_value=({}, [])):
            await native.claude(run, 'Read the board')
        self.assertEqual(args_seen[args_seen.index('--tools') + 1], 'Read,Grep,Glob')
        self.assertIn('--no-session-persistence', args_seen)
        self.assertNotIn('--resume', args_seen)
        self.assertEqual(sum(x['type'] == 'user' for x in writes), 1)
        self.assertEqual(writes[-1]['response']['response']['behavior'], 'allow')
        run.approval.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
