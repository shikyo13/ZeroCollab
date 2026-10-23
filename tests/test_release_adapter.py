"""room_release's adapter (task 3830a810, E4 astra 7c5e2a88): the MCP tool, the local definition and the core's
strict interface name the same actions and fields, and every native turn names the tool."""
import inspect
import typing
import unittest
import unittest.mock

import zerocollab.releases.release_interface as release_interface
import zerocollab.api.room_mcp as room_mcp
import zerocollab.runtime.room_runtime as room_runtime
from zerocollab.coordination.project_room import coordination_tools


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

    def test_stage_metadata_is_sent_only_when_given(self):
        """b1acec5d (astra 40bc6f1b): an existing stage call carries no metadata; given fields pass unchanged."""
        sent = []
        tool = getattr(room_mcp.room_release, 'fn', room_mcp.room_release)
        with unittest.mock.patch.object(room_mcp, 'call', lambda name, args, timeout: sent.append(args) or {}):
            tool(action='stage', repository='r', commit='c' * 40, reviews=['0' * 8])
            tool(action='stage', repository='r', commit='c' * 40, reviews=['0' * 8], title='T', notes='N',
                 shipped_tasks=[{'task_id': '0' * 8, 'scope': 'S'}])
        self.assertFalse({'title', 'notes', 'shipped_tasks'} & set(sent[0]))
        self.assertEqual((sent[1]['title'], sent[1]['notes'], sent[1]['shipped_tasks']),
                         ('T', 'N', [{'task_id': '0' * 8, 'scope': 'S'}]))
        local = self.local['properties']
        self.assertEqual((local['shipped_tasks']['maxItems'], local['title']['maxLength'], local['notes']['maxLength'],
                          local['shipped_tasks']['items']['required']), (32, 120, 16000, ['task_id', 'scope']))

    def test_donor_reads_are_classified_as_reads(self):
        actions, default = room_runtime.ROOM_READS['room_release']
        self.assertEqual((actions, default), ({'list', 'status', 'intake'}, 'status'))
        self.assertFalse({'check', 'handoff', 'disposition', 'archive'} & actions)


if __name__ == '__main__':
    unittest.main()
