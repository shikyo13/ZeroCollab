"""Native/local room-tool parity and truthful direct-API capability prompts."""
import asyncio
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from mcp.server.fastmcp import FastMCP

from zerocollab.api.room_mcp import mcp
from zerocollab.coordination.project_room import ProjectRoom, coordination_tools


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


if __name__ == '__main__':
    unittest.main()
