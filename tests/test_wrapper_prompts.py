import json
import unittest

from zerocollab.legacy.wrapper import _queued_prompt


class WrapperPromptTests(unittest.TestCase):
    def test_preserves_channels_and_custom_prompts_in_arrival_order(self):
        prompt = _queued_prompt(map(json.dumps, [
            {'channel': 'first'}, {'prompt': 'First instruction'},
            {'channel': 'second'}, {'channel': 'first'}, {'prompt': 'Latest instruction'}]))
        self.assertEqual(prompt.count('#first'), 1)
        self.assertEqual(prompt.count('#second'), 1)
        positions = [prompt.index(s) for s in ['#first', 'First instruction', '#second', 'Latest instruction']]
        self.assertEqual(positions, sorted(positions))

    def test_malformed_entries_do_not_hide_valid_wakes(self):
        self.assertEqual(_queued_prompt(['bad', 'null', '[]', '']), '')
        self.assertIn('#general', _queued_prompt(['bad', '{"channel": null}']))
