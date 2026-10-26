"""Native/local room-tool parity and truthful direct-API capability prompts."""
import asyncio
import copy
import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from mcp.server.fastmcp import FastMCP

from zerocollab.api.room_mcp import mcp
from zerocollab.coordination.project_room import ProjectRoom, coordination_tools
from zerocollab.coordination.response_claims import ResponseClaims
from zerocollab.providers import provider_store
from zerocollab.runtime import room_runtime


class ToolContractParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Bypass RoomMCP's load notification; metadata inspection makes no HTTP request.
        cls.native = {tool.name: tool for tool in asyncio.run(FastMCP.list_tools(mcp))}
        cls.local = {tool['function']['name']: tool['function'] for tool in coordination_tools()}

    def test_local_catalog_is_an_explicit_native_subset(self):
        self.assertEqual(set(self.native) - set(self.local),
                         {'room_browser', 'room_decision', 'room_image', 'room_team_builder'})
        self.assertFalse(set(self.local) - set(self.native))

    def test_shared_parameters_and_required_fields_match(self):
        for name, local in self.local.items():
            with self.subTest(tool=name):
                native = self.native[name].inputSchema
                schema = local['parameters']
                self.assertEqual(set(schema['properties']), set(native['properties']))
                native_required = set(native.get('required', []))
                local_required = set(schema.get('required', []))
                self.assertFalse(native_required - local_required)
                # Local calls deliberately require an explicit release action; MCP defaults to status.
                self.assertEqual(local_required - native_required,
                                 {'action'} if name == 'room_release' else set())

    def test_declared_native_action_enums_match_local(self):
        for name, local in self.local.items():
            native_action = self.native[name].inputSchema['properties'].get('action', {})
            if 'enum' in native_action:
                with self.subTest(tool=name):
                    self.assertEqual(set(local['parameters']['properties']['action']['enum']),
                                     set(native_action['enum']))


class ToolAvailability(unittest.TestCase):
    def prompt(self, provider='api', *, enabled=True, audit=False, skills=()):
        room = ProjectRoom.__new__(ProjectRoom)
        room.path = None
        room.runtime = SimpleNamespace(project=None, identity='fixture',
            states={'worker': {'provider': provider}},
            cfg={'agents': {'worker': {'tools': enabled}}})
        run = SimpleNamespace(name='worker', worktree=Path('fixture'),
                              manager_audit='fixture-audit' if audit else None, skills=skills)
        return room.execution_context(run)

    def advertised(self, text):
        match = re.search(r'- Room tools \(([^)]+)\) are provided for this turn\.', text)
        return set(match[1].split(', ')) if match else set()

    def test_direct_api_advertises_exactly_its_catalog(self):
        expected = {tool['function']['name'] for tool in coordination_tools()}
        self.assertEqual(self.advertised(self.prompt()), expected)
        self.assertNotIn('room_image', self.advertised(self.prompt()))

    def test_disabled_tools_are_not_advertised(self):
        text = self.prompt(enabled=False)
        self.assertEqual(self.advertised(text), set())
        self.assertIn('Room tools are disabled for this turn.', text)
        self.assertEqual(coordination_tools(enabled=False), [])

    def test_audit_advertises_only_its_allowed_tools(self):
        expected = {'room_read', 'room_task', 'room_update', 'room_followup'}
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                self.assertEqual(self.advertised(self.prompt(enabled=enabled, audit=True)), expected)
                selected = coordination_tools(enabled=enabled, audit=True)
                self.assertEqual({tool['function']['name'] for tool in selected}, expected)

    def test_native_capability_text_is_unchanged(self):
        expected = ('room_read, room_task, room_update, room_review, room_followup, room_goal, '
                    'room_repository, room_image, room_markup, room_browser, room_release')
        for provider in ('codex', 'claude', 'antigravity'):
            with self.subTest(provider=provider):
                text = self.prompt(provider)
                self.assertIn(f'- Room tools ({expected}) are provided for this turn.', text)
                self.assertIn("room_browser is the user's Browser panel", text)
                self.assertIn('room_skill', self.advertised(self.prompt(provider, skills=['room-task'])))


class LocalTurnContracts(unittest.IsolatedAsyncioTestCase):
    def make_run(self, *, provider='api', enabled=True, audit=False, options=None, background=''):
        runtime = room_runtime.RoomRuntime.__new__(room_runtime.RoomRuntime)
        runtime.cfg = {'runtime': {'automatic_tool_approvals': True}, 'agents': {'worker': {
            'base_url': 'http://127.0.0.1:1/v1', 'tools': enabled, 'max_tokens': 1024,
            'context_tokens': 16384, 'request_options': options or {}}}}
        runtime.states = {'worker': {'provider': provider, 'worktree': 'fixture'}}
        runtime.controls = SimpleNamespace(values={'worker': {'model': 'fixture', 'effort': ''}})
        runtime.profiles = SimpleNamespace(items={'worker': {'background': background}})
        runtime.host = SimpleNamespace(store=SimpleNamespace())
        runtime.response_claims = ResponseClaims(runtime)
        runtime.decisions = SimpleNamespace(sealing=lambda run: None)
        runtime.project, runtime.identity = None, 'fixture'
        room = ProjectRoom.__new__(ProjectRoom)
        room.path, room.runtime, room.state = None, runtime, {}
        room.limits = {'local_tool_rounds': 2}
        room.previous_session = lambda run: {}
        runtime.project_room = room
        run = room_runtime.Run(runtime, 'worker', {'channel': 'fixture',
            'requests': [{'manager_audit': 'audit'}] if audit else [{}]})
        for name in ('report_permissions', 'report_settings', 'report_usage', 'delta', 'finish_message', 'tool_event'):
            setattr(run, name, AsyncMock())
        runtime.coordination = AsyncMock(return_value={'ok': True})
        return runtime, run

    async def capture(self, runtime, run, requested_tool=None):
        requests = []

        async def stream(client, base, payload, **callbacks):
            requests.append(copy.deepcopy(payload))
            if requested_tool and len(requests) == 1:
                call = {'id': 'fixture-call', 'type': 'function',
                        'function': {'name': requested_tool, 'arguments': '{}'}}
                return '', {0: call}, 'tool_calls', {}
            return '', {}, 'stop', {}

        client = MagicMock()
        client.headers = {}
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        with patch.object(room_runtime.httpx, 'AsyncClient', return_value=client), \
             patch.object(room_runtime, 'stream_chat', side_effect=stream), \
             patch.object(room_runtime.provider_keys, 'headers', return_value={}), \
             patch.object(room_runtime.provider_keys, 'record'):
            await runtime.local_turn(run, runtime.project_room.execution_context(run))
        return requests

    def system_room_names(self, text):
        match = re.search(r'Available room tools for this turn: ([^\n]+)', text)
        self.assertIsNotNone(match, 'The real system instructions must state the selected capabilities')
        return set() if match[1] == 'none' else set(match[1].split(', '))

    async def test_full_system_instructions_scope_shared_guidance_to_the_actual_catalog(self):
        for enabled, audit in ((True, False), (False, False), (True, True), (False, True)):
            with self.subTest(enabled=enabled, audit=audit):
                runtime, run = self.make_run(enabled=enabled, audit=audit)
                request = (await self.capture(runtime, run))[0]
                actual = {t['function']['name'] for t in request.get('tools', [])
                          if t['function']['name'].startswith('room_')}
                system = request['messages'][0]['content']
                self.assertEqual(self.system_room_names(system), actual)
                self.assertNotIn('room_image views an uploaded image', system)
                self.assertIn('instructions naming other tools apply to teammates that have them', system)
                self.assertIn('Intentional tool absence is not a harness fault', system)
                self.assertIn('This is the complete callable room-tool list for this turn.', system)
                self.assertGreater(system.index('Available room tools for this turn:'),
                                   system.index((room_runtime.ROOT / 'COLLABORATION.md').read_text('utf-8').strip()))
                if not enabled or audit:
                    self.assertIn('No local file tools are provided for this turn.', system)

    async def test_request_options_cannot_replace_or_restore_runner_tools(self):
        for enabled, audit in ((True, False), (False, False), (True, True), (False, True)):
            for override in ([], [{'type': 'function', 'function': {'name': 'injected_tool'}}]):
                with self.subTest(enabled=enabled, audit=audit, override=override):
                    options = {'tools': override, 'temperature': 0.2, 'top_p': 0.9}
                    runtime, run = self.make_run(enabled=enabled, audit=audit, options=options)
                    before = copy.deepcopy(runtime.cfg)
                    request = (await self.capture(runtime, run))[0]
                    expected = coordination_tools(enabled=enabled, audit=audit)
                    if enabled and not audit:
                        expected = room_runtime.local_tool_definitions() + expected
                    self.assertEqual(request.get('tools', []), expected)
                    self.assertEqual(self.system_room_names(request['messages'][0]['content']),
                                     {t['function']['name'] for t in expected if t['function']['name'].startswith('room_')})
                    self.assertEqual((request['temperature'], request['top_p']), (0.2, 0.9))
                    self.assertEqual(runtime.cfg, before)
                    if not expected:
                        self.assertNotIn('tools', request)
                        self.assertNotIn('tool_choice', request)

    async def test_prompt_budget_reserves_the_tools_actually_sent(self):
        for enabled, audit in ((True, False), (False, False), (True, True), (False, True)):
            with self.subTest(enabled=enabled, audit=audit):
                runtime, run = self.make_run(enabled=enabled, audit=audit)
                request = (await self.capture(runtime, run))[0]
                expected = provider_store.input_budget_chars(16384, 1024,
                    len(run.instructions) + len(json.dumps(request.get('tools', []))) + 300)
                self.assertEqual(runtime.local_input_budget(run), expected)

    async def test_audit_with_valid_background_is_not_refused_for_unused_tools(self):
        runtime, run = self.make_run(audit=True, background='x' * 13000)
        self.assertGreaterEqual(provider_store.input_budget_chars(16384, 1024,
                                provider_store.fixed_chars_estimate()), provider_store.MIN_INPUT_CHARS)
        request = (await self.capture(runtime, run))[0]
        self.assertEqual(len(request['tools']), 4)
        self.assertGreaterEqual(runtime.local_input_budget(run), provider_store.MIN_INPUT_CHARS)

    async def test_real_dispatch_accepts_available_tools_and_refuses_unavailable_tools(self):
        for enabled, name, accepted in ((True, 'room_read', True), (True, 'room_image', False),
                                        (False, 'room_read', False)):
            with self.subTest(enabled=enabled, tool=name):
                runtime, run = self.make_run(enabled=enabled)
                requests = await self.capture(runtime, run, requested_tool=name)
                self.assertEqual(runtime.coordination.await_count, int(accepted))
                if accepted:
                    runtime.coordination.assert_awaited_once_with(run, name, {})
                else:
                    self.assertIn('unavailable tool: ' + name, requests[-1]['messages'][-1]['content'])

    def test_native_run_instructions_keep_their_existing_tool_guidance(self):
        collaboration = (room_runtime.ROOT / 'COLLABORATION.md').read_text('utf-8').strip()
        for provider in ('codex', 'claude', 'antigravity'):
            with self.subTest(provider=provider):
                _, run = self.make_run(provider=provider)
                self.assertIn('room_image views an uploaded image', run.instructions)
                self.assertIn(collaboration, run.instructions)
                self.assertNotIn('Available room tools for this turn:', run.instructions)


if __name__ == '__main__':
    unittest.main()
