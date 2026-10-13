"""room_release's adapter (task 3830a810, E4 astra 7c5e2a88): the MCP tool, the local definition and the core's
strict interface name the same actions and fields, and every native turn names the tool."""
import inspect
import typing
import unittest

import release_interface
import room_mcp
import room_runtime
from project_room import coordination_tools


class Parity(unittest.TestCase):
    def setUp(self):
        self.local = next(t['function']['parameters'] for t in coordination_tools()
                          if t['function']['name'] == 'room_release')
        self.mcp = inspect.signature(getattr(room_mcp.room_release, 'fn', room_mcp.room_release)).parameters

    def test_actions_match_the_core(self):
        core = set(release_interface.FIELDS)
        self.assertEqual(set(typing.get_args(self.mcp['action'].annotation)), core)
        self.assertEqual(set(self.local['properties']['action']['enum']), core)

    def test_fields_match_the_core_in_both_definitions(self):
        fields = set().union(*release_interface.FIELDS.values())
        self.assertEqual(set(self.mcp) - {'action'}, fields)
        self.assertEqual(set(self.local['properties']) - {'action'}, fields)
        self.assertEqual(self.local['properties']['disposition']['enum'], ['open', 'deferred', 'dropped'])

    def test_donor_reads_are_classified_as_reads(self):
        actions, default = room_runtime.ROOM_READS['room_release']
        self.assertEqual((actions, default), ({'list', 'status', 'intake'}, 'status'))
        self.assertFalse({'check', 'handoff', 'disposition', 'archive'} & actions)


if __name__ == '__main__':
    unittest.main()
