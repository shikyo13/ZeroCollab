"""A resumed Claude session that first plays a leftover background-task notice still answers the room prompt
(harness 03660213, demo-marketing #14806: the notice's empty result ended the turn before the prompt ran)."""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import native_adapters as native


class FakeClaude:
    """In-process stand-in for `claude --print --input-format stream-json`: scripted replies per stdin line."""
    def __init__(self, echo_prompt=True, notice_first=True):
        self.stdout = asyncio.StreamReader()
        self.stdin = SimpleNamespace(write=self.write, drain=AsyncMock(), close=Mock())
        self.returncode, self.pid = None, 0
        self.echo_prompt, self.notice_first = echo_prompt, notice_first

    def emit(self, *messages):
        for msg in messages:
            self.stdout.feed_data((json.dumps(msg) + '\n').encode())

    def write(self, data):
        msg = json.loads(data)
        if msg.get('type') == 'control_request' and msg['request_id'] == 'room-init':
            self.emit({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': 'room-init'}})
        elif msg.get('type') == 'user':
            if self.notice_first:  # the injected notice turn: no model call, no usage, no text
                self.emit({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': '',
                           'usage': {'input_tokens': 0, 'output_tokens': 0, 'cache_read_input_tokens': 0}})
            if self.echo_prompt:
                self.emit({'type': 'user', 'isReplay': True, 'uuid': msg.get('uuid'), 'message': msg['message']})
            self.emit({'type': 'assistant', 'message': {'id': 'm1', 'stop_reason': 'end_turn',
                                                       'content': [{'type': 'text', 'text': 'answer'}]}},
                      {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'answer',
                       'usage': {'input_tokens': 900, 'output_tokens': 40}})
            self.stdout.feed_eof()


class ClaudeResultTests(unittest.IsolatedAsyncioTestCase):
    async def claude(self, cli):
        said = []

        async def spawn(run, *args, **kwargs):
            run.process = cli
            return cli

        async def delta(text):
            said.append(text)
            run.text += text
        run = SimpleNamespace(previous_session={'id': 'old'}, automatic_tool_approvals=True, instructions='',
                              settings={'model': '', 'effort': ''}, text='', published_messages=[],
                              completed_messages=set(), provider_session=None, message_phase=None,
                              report_settings=AsyncMock(), report_permissions=AsyncMock(), report_usage=AsyncMock(),
                              begin_message=AsyncMock(), finish_message=AsyncMock(), delta=delta)
        with patch.object(native, 'spawn', spawn), patch.object(native, 'room_server', return_value={}), \
             patch.object(native, 'relay_setup', return_value=({}, [])), patch.object(native, 'command', return_value=['claude']):
            await native.claude(run, 'Zero asked a question')
        return said

    async def test_leftover_notice_result_does_not_end_the_turn(self):
        self.assertEqual(await self.claude(FakeClaude()), ['answer'])

    async def test_cli_that_never_echoes_the_prompt_still_finishes(self):
        # Without the echo, only an empty zero-usage result is skipped; the real one ends the turn as before.
        self.assertEqual(await self.claude(FakeClaude(echo_prompt=False, notice_first=False)), ['answer'])


if __name__ == '__main__':
    unittest.main()
