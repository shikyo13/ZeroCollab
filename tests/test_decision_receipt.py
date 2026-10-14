"""room_decision submit returns a receipt, not the whole record (task 8d40ba11, Zero #17800: 54 KB per submit)."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from decision_flow import DecisionFlow

NAMES = ['opus', 'systems', 'harness', 'backend', 'frontend', 'desktop', 'qa', 'media']


class Receipt(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.flow = DecisionFlow(SimpleNamespace())
        self.flow.path = Path(tmp.name) / 'decisions.json'
        positions = [{'id': f'p{i}', 'author': name, 'stage': 'positions', 'kind': 'position', 'round': 1,
                      'revision': 1, 'epoch': 1, 'created': 1, 'text': 'x' * 8000, 'evidence': ['e' * 500] * 8}
                     for i, name in enumerate(NAMES)]
        self.d = {'id': 'd1', 'question': 'q' * 2000, 'stage': 'critique', 'revision': 1, 'epoch': 1, 'round': 1,
                  'lead': 'opus', 'participants': list(NAMES), 'channel': 'c', 'contributions': positions,
                  'returns_used': 0, 'edits_used': 0}
        self.flow.items = [self.d]

    def test_submit_returns_a_small_receipt_naming_what_was_recorded(self):
        record = len(json.dumps(self.flow.view(self.d, reader='backend')))
        out = self.flow.submit('backend', 'd1', 'critique', 1, 1, {
            'challenges': [{'responds_to': ['p0'], 'text': 'c' * 4000}],
            'concessions': [{'responds_to': ['p1'], 'text': 'k' * 4000}]})
        self.assertGreater(record, 60000)  # what submit used to return
        self.assertLess(len(json.dumps(out)), 2000)
        self.assertEqual(len(out['recorded']), 2)
        self.assertNotIn('backend', out['owed'])
        self.assertEqual((out['stage'], out['token']), ('critique', {'id': 'd1', 'stage': 'critique', 'revision': 1, 'epoch': 1}))
        self.assertIn('Waiting on: opus', out['next'])
        self.assertNotIn('contributions', out)
        self.assertEqual(len(self.flow.get('backend', 'd1')['contributions']), 10)  # get still returns the record

    def test_no_token_for_a_non_participant(self):
        self.assertNotIn('token', self.flow.receipt(self.d, 'astra'))
        self.assertIn('owe a submission', self.flow.receipt(self.d, 'qa')['next'])


if __name__ == '__main__':
    unittest.main()
