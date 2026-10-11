"""Audit authority blockers with runner/RPC stand-ins; no CLI/model calls."""
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import native_adapters as native
from room_runtime import Run


class CodexBoundaryChecks(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.thread = {'thread': {'id': 'audit'}, 'approvalPolicy': 'on-request',
                       'approvalsReviewer': 'user', 'sandbox': {'type': 'readOnly'}}
        self.room = {'name': 'zerocollab_coordination', 'tools': {'room_read': {}}}
        self.run = SimpleNamespace(report_permissions=AsyncMock())

    async def test_actual_permission_report_precedes_input(self):
        rpc = SimpleNamespace(request=AsyncMock(return_value={'data': [self.room], 'nextCursor': None}))
        await native.verify_audit_codex(rpc, self.run, self.thread)
        self.run.report_permissions.assert_awaited_once()
        report = self.run.report_permissions.call_args.args[0]
        self.assertEqual(report['loaded_tools'], ['mcp__zerocollab_coordination__room_read'])
        self.assertEqual(report['sandbox'], {'type': 'readOnly'})

    async def test_unsafe_or_unknown_permissions_never_report_success(self):
        for replacement in ({'sandbox': {'type': 'workspaceWrite'}}, {'approvalsReviewer': 'auto_review'},
                            {'approvalPolicy': 'never'}, {'sandbox': {'type': 'readOnly', 'networkAccess': True}}):
            rpc = SimpleNamespace(request=AsyncMock())
            with self.assertRaises(ValueError):
                await native.verify_audit_codex(rpc, self.run, self.thread | replacement)
            rpc.request.assert_not_awaited()
        self.run.report_permissions.assert_not_awaited()

    async def test_extra_failed_missing_and_paginated_mcp_fail_closed(self):
        for pages in ([{'data': [self.room, {'name': 'github', 'tools': {'write': {}}}]}],
                      [{'data': [self.room | {'toolsError': 'failed'}]}],
                      [{'data': []}], [{'data': None}],
                      [{'data': [self.room], 'nextCursor': 'next'},
                       {'data': [{'name': 'filesystem', 'tools': {'write': {}}}]}],
                      [{'data': [self.room], 'nextCursor': 'same'},
                       {'data': [self.room], 'nextCursor': 'same'}]):
            rpc = SimpleNamespace(request=AsyncMock(side_effect=pages))
            with self.assertRaises(ValueError):
                await native.verify_audit_codex(rpc, self.run, self.thread)
        self.run.report_permissions.assert_not_awaited()

    async def test_only_requested_verified_disabled_server_is_ignored(self):
        disabled = {'name': 'github', 'runtimeStatus': 'disabled', 'tools': {}}
        rpc = SimpleNamespace(request=AsyncMock(return_value={'data': [self.room, disabled]}))
        await native.verify_audit_codex(rpc, self.run, self.thread, audit_disabled=('github',))
        self.run.report_permissions.assert_awaited_once()
        self.run.report_permissions.reset_mock()
        for disabled_names, server in (
                ((), disabled),
                (('other',), disabled),
                (('github',), disabled | {'runtimeStatus': 'connected'}),
                (('github',), disabled | {'runtimeStatus': None}),
                (('github',), disabled | {'tools': {'write': {}}}),
                (('github',), disabled | {'tools': None}),
                (('github',), disabled | {'toolsError': 'unavailable'})):
            with self.subTest(disabled_names=disabled_names, server=server):
                rpc = SimpleNamespace(request=AsyncMock(return_value={'data': [self.room, server]}))
                with self.assertRaises(native.AuditMCPIsolation):
                    await native.verify_audit_codex(rpc, self.run, self.thread, audit_disabled=disabled_names)
        self.run.report_permissions.assert_not_awaited()

    async def test_adapter_refuses_before_turn_and_cleans_rpc(self):
        run = SimpleNamespace(manager_audit='claim', automatic_tool_approvals=True,
                              previous_session={'id': 'ordinary'}, worktree=Path.cwd(),
                              settings={'model': '', 'effort': ''}, instructions='audit',
                              report_permissions=AsyncMock(), report_settings=AsyncMock(),
                              install_steer=lambda fn: None)
        calls, rpc_instances, argv = [], [], []
        outer = self
        class Rpc:
            def __init__(self, run, proc, notify, incoming):
                self.notify = notify
                self.reader = asyncio.get_running_loop().create_future()
                self.read_error = None
                self.closed = False
                rpc_instances.append(self)
            async def send(self, obj):
                pass
            async def request(self, method, params):
                calls.append((method, params))
                if method == 'initialize':
                    return {}
                if method == 'thread/start':
                    return outer.thread
                if method == 'mcpServerStatus/list':
                    return {'data': [outer.room, {'name': 'github', 'tools': {'write': {}}}]}
                raise AssertionError('A prompt reached the runner before inventory refusal')
            async def close(self):
                self.closed = True
                self.reader.cancel()
        async def spawn(run, args, env, **kwargs):
            argv.extend(args)
            return object()
        with patch.object(native, 'Rpc', Rpc), patch.object(native, 'spawn', spawn), \
             patch.object(native, 'command', return_value=['codex']), \
             patch.object(native, 'room_server', return_value={'command': 'python', 'args': []}), \
             patch.object(native, 'room_environment', return_value={}), \
             patch.object(native, 'terminate', AsyncMock()), \
             patch.object(native, 'relay_setup', return_value=({}, [])):
            with self.assertRaises(ValueError):
                await native.codex(run, 'must not be sent')
        self.assertTrue(rpc_instances[0].closed)
        self.assertEqual(len(rpc_instances), 2)
        self.assertTrue(rpc_instances[1].closed)
        self.assertNotIn('turn/start', [m for m, p in calls])
        self.assertEqual(run.previous_session, {})
        self.assertFalse(run.automatic_tool_approvals)
        self.assertIn('features.apps=false', argv)
        self.assertIn('features.multi_agent=false', argv)
        self.assertIn('web_search="disabled"', argv)
        self.assertIn('shell_environment_policy.ignore_default_excludes=false', argv)
        self.assertIn('approvals_reviewer="user"', argv)
        self.assertIn('mcp_servers.github.enabled=false', argv)
        params = dict(calls)['thread/start']
        self.assertEqual(params['sandbox'], 'read-only')
        self.assertEqual(params['approvalsReviewer'], 'user')
        self.assertTrue(params['ephemeral'])

    async def test_bounded_relaunch_rechecks_before_input(self):
        class BeforeInput(Exception):
            pass
        run = SimpleNamespace(manager_audit='claim', automatic_tool_approvals=True,
                              previous_session={'id': 'ordinary'}, worktree=Path.cwd(),
                              settings={'model': '', 'effort': ''}, instructions='audit',
                              report_permissions=AsyncMock(), report_settings=AsyncMock(side_effect=BeforeInput),
                              install_steer=lambda fn: None)
        instances, launches, events = [], [], []
        outer = self
        class Rpc:
            def __init__(self, run, proc, notify, incoming):
                self.index, self.closed = len(instances), False
                instances.append(self)
            async def send(self, obj):
                pass
            async def request(self, method, params):
                events.append((self.index, method))
                if method == 'initialize':
                    return {}
                if method == 'thread/start':
                    return outer.thread
                if method == 'mcpServerStatus/list':
                    return {'data': [outer.room, {'name': 'github',
                        'runtimeStatus': 'connected' if self.index == 0 else 'disabled',
                        'tools': {'write': {}} if self.index == 0 else {}}]}
                raise AssertionError('Model input must not be sent by this pre-input check')
            async def close(self):
                self.closed = True
        async def spawn(run, args, env, **kwargs):
            if launches:
                self.assertTrue(instances[0].closed)
                self.assertIn(('terminated', 0), events)
            launches.append(args)
            return object()
        async def terminate(run, grace):
            self.assertEqual(grace, native.EXIT_GRACE)
            events.append(('terminated', 0))
        with patch.object(native, 'Rpc', Rpc), patch.object(native, 'spawn', spawn), \
             patch.object(native, 'terminate', terminate), \
             patch.object(native, 'command', return_value=['codex']), \
             patch.object(native, 'room_server', return_value={'command': 'python', 'args': []}), \
             patch.object(native, 'room_environment', return_value={}), \
             patch.object(native, 'relay_setup', return_value=({}, [])):
            with self.assertRaises(BeforeInput):
                await native.codex(run, 'must not be sent')
        self.assertEqual(len(launches), 2)
        self.assertNotIn('mcp_servers.github.enabled=false', launches[0])
        self.assertIn('mcp_servers.github.enabled=false', launches[1])
        self.assertTrue(all(r.closed for r in instances))
        self.assertEqual([m for i, m in events if isinstance(i, int)].count('mcpServerStatus/list'), 2)
        run.report_permissions.assert_awaited_once()
        self.assertEqual(run.report_permissions.call_args.args[0]['loaded_tools'], ['mcp__zerocollab_coordination__room_read'])


class RunAuthorityChecks(unittest.IsolatedAsyncioTestCase):
    def make_run(self, audit=True):
        store = SimpleNamespace(add=lambda *a, **kw: self.messages.append(kw) or {'id': 'report'})
        self.messages = []
        manager = SimpleNamespace(states={'lead': {'worktree': str(Path.cwd())}},
            controls=SimpleNamespace(values={'lead': {'model': '', 'effort': ''}}),
            project_room=SimpleNamespace(limits={}, previous_session=lambda run: {'id': 'old'}),
            cfg={'runtime': {'automatic_tool_approvals': True}}, profiles=SimpleNamespace(items={}),
            emit=AsyncMock(), ask=AsyncMock(return_value=False), host=SimpleNamespace(store=store))
        requests = [{'manager_audit': 'claim'}] if audit else [{}]
        return Run(manager, 'lead', {'channel': 'ordinary', 'chain': 'chain', 'requests': requests})

    async def test_audit_caps_session_no_route_and_human_approval_override(self):
        run = self.make_run()
        self.assertEqual(run.limits, {'turn_max_seconds': 120, 'turn_seconds': 60,
                                     'native_tool_calls': 16, 'local_tool_rounds': 3})
        self.assertEqual(run.previous_session, {})
        # Even a later room-approval flag mutation cannot make an audit auto-approve.
        run.automatic_tool_approvals = True
        self.assertFalse(await run.approval('shell', {}, 'risk'))
        run.manager.ask.assert_awaited_once()
        run.message_id, run.text, run.message_phase = 'output', '@worker do work', 'final_answer'
        await run.finish_message()
        self.assertTrue(self.messages[-1]['metadata']['no_route'])

    async def test_ordinary_permissions_and_session_unchanged(self):
        run = self.make_run(audit=False)
        self.assertTrue(run.automatic_tool_approvals)
        self.assertEqual(run.previous_session, {'id': 'old'})
        self.assertEqual(run.limits, {})


if __name__ == '__main__':
    unittest.main()
