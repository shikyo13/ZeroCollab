"""Turn telemetry (task 844f5663): runtime_end's prompt-size blocks, Claude's real tool arguments, and bounded tool details."""
import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import zerocollab.runtime.room_runtime as room_runtime
from zerocollab.coordination.project_room import ProjectRoom
from zerocollab.storage.store import MessageStore


class PromptBlockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        data = Path(self.tmp.name)
        self.store = MessageStore(str(data / 'messages.jsonl'))
        runtime = SimpleNamespace(data=data, cfg={'server': {'port': 1}}, host=SimpleNamespace(store=self.store),
                                  project=None, identity='p', states={'opus': {'label': 'Opus', 'provider': 'claude'}},
                                  diffs=lambda: {}, channel_title=lambda c: '#' + c)
        self.room = ProjectRoom(runtime)
        self.room.use_project('p')

    def turn(self, text):
        m = self.store.add('user', text, channel='general')
        run = SimpleNamespace(name='opus', channel='general', receipts=set(), worktree='W:/scratch/opus',
                              entry={'chain': 'c', 'requests': [{'prompt': f'user: {text}', 'message_id': m['id'], 'human': True}]},
                              session_key='opus:general', settings={}, instruction_hash='h', provider_session='sess')
        run.previous_session = self.room.previous_session(run)
        prompt = self.room.context(run)
        self.room.complete(run)
        return run, prompt

    def test_blocks_add_up_to_the_prompt_and_name_unchanged_ones(self):
        first, prompt = self.turn('@opus look at the board')
        self.assertFalse(first.input_clipped)
        self.assertEqual(sum(first.input_blocks.values()), len(prompt))
        self.assertEqual(first.input_blocks['request'], len('\nCurrent message or task:\n') + len(prompt.split('\nCurrent message or task:\n', 1)[1]))
        self.assertEqual(first.input_unchanged, [])
        second, _ = self.turn('@opus again')
        self.assertIn('workspace', second.input_unchanged)  # a resumed session gets the pointer, and the event says so


class ToolEventTests(unittest.TestCase):
    def run_like(self, provider='claude'):
        events = []
        async def emit(kind, data, persist=False):
            events.append((kind, data))
        manager = SimpleNamespace(states={'opus': {'provider': provider}}, emit=emit,
                                  decisions=SimpleNamespace(sealing=lambda run: None))
        run = room_runtime.Run.__new__(room_runtime.Run)
        run.manager, run.name, run.channel, run.id = manager, 'opus', 'general', 'r1'
        run.text, run.message_phase, run.focus, run.deadline = '', None, None, None
        run.limits = {'native_tool_calls': 0}
        run.native_tool_ids, run.native_tool_calls, run.changing_calls, run.file_changes = set(), 0, 0, 0
        run.created_at, run.first_output_at = time.monotonic(), None
        return run, events

    def test_claude_input_event_carries_a_digest_and_moves_no_counter(self):
        run, events = self.run_like()
        start = {'type': 'tool_use', 'id': 'toolu_1', 'name': 'Bash', 'input': {}}
        asyncio.run(run.tool_event('Bash', start, 'started'))
        self.assertEqual((run.native_tool_calls, run.changing_calls), (1, 1))
        self.assertNotIn('args_sha256', events[-1][1])  # input {} at stream start says nothing about the call
        full = {**start, 'input': {'command': 'git status'}}
        asyncio.run(run.tool_event('Bash', full, 'input'))
        self.assertEqual((run.native_tool_calls, run.changing_calls), (1, 1))
        self.assertEqual(events[-1][1]['args_sha256'], room_runtime.args_digest({**full, 'id': 'toolu_2'}))  # ids excluded
        self.assertIsNotNone(run.first_output_at)
        profile = room_runtime.turn_profile(SimpleNamespace(created_at=run.created_at, first_output_at=run.first_output_at,
            native_tool_calls=1, coordination_calls=0, changing_calls=1, input_chars=10, input_blocks={'request': 10},
            input_clipped=False, input_unchanged=[], reported_tokens=0), 'claude')
        self.assertEqual((profile['tool_calls'], profile['input_chars']), (1, 10))

    def test_details_are_cut_and_redacted_but_the_digest_sees_the_whole_call(self):
        run, events = self.run_like(provider='codex')
        item = {'type': 'commandExecution', 'id': 'c1', 'command': 'curl -H "token=abc123" ' + 'x' * 20000}
        asyncio.run(run.tool_event('commandExecution', item, 'started'))
        details = events[-1][1]['details']
        self.assertIn('[redacted]', details['command'])
        self.assertNotIn('abc123', details['command'])
        self.assertTrue(details['command'].endswith('chars]'))
        self.assertLessEqual(len(details['command']), room_runtime.TOOL_STRING_CHARS + 20)
        self.assertEqual(events[-1][1]['args_sha256'], room_runtime.args_digest(item))
        huge = room_runtime.bounded_details({f'k{n}': 'y' * 1500 for n in range(20)})
        self.assertEqual(set(huge), {'excerpt', 'cut_chars'})
        self.assertEqual(len(huge['excerpt']), room_runtime.TOOL_DETAIL_CHARS)


if __name__ == '__main__':
    unittest.main()
