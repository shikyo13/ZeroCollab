"""A transient provider failure re-queues the work with backoff instead of blocking the agent (task f5dbe565)."""
import time
import unittest
from types import SimpleNamespace

from room_runtime import RoomRuntime, TRANSIENT_FAILURE, TRANSIENT_RETRY_SECONDS

# The exact detail Astra's two turns failed with on 2026-10-14 (runtime_events, runs 3a6238af2ed8 and f94c3b744e22).
CODEX_429 = ("{'message': 'exceeded retry limit, last status: 429 Too Many Requests', 'codexErrorInfo': "
             "{'responseTooManyFailedAttempts': {'httpStatusCode': 429}}, 'additionalDetails': None, 'misalignment': None}")


class Queue:
    def __init__(self):
        self.items = []

    def retry(self, item):
        self.items.insert(0, item)


class TransientFailure(unittest.TestCase):
    def setUp(self):
        self.rt = object.__new__(RoomRuntime)
        self.rt.states = {'astra': {'state': 'ready', 'detail': ''}}
        self.rt.queues = {'astra': Queue()}
        self.entry = {'channel': 'c', 'requests': []}

    def run_for(self, **produced):
        return SimpleNamespace(name='astra', entry=self.entry, manager_audit=False, stop_reason='',
                               text=produced.get('text', ''), published_messages=0, native_tool_calls=0,
                               coordination_calls=0)

    def test_a_429_before_any_output_is_requeued_with_backoff_then_blocks(self):
        for count, delay in enumerate(TRANSIENT_RETRY_SECONDS, 1):
            before = time.time()
            self.assertEqual(self.rt.transient_failure(self.run_for(), RuntimeError(CODEX_429), CODEX_429), 'retry')
            self.assertEqual(self.entry['transient_retries'], count)
            self.assertGreaterEqual(self.entry['retry_after'], before + delay)
            self.assertEqual(self.rt.states['astra']['state'], 'ready')  # never blocked while retries remain
            self.assertIn(f'({count} of {len(TRANSIENT_RETRY_SECONDS)})', self.rt.states['astra']['detail'])
        self.assertEqual(len(self.rt.queues['astra'].items), len(TRANSIENT_RETRY_SECONDS))
        self.assertEqual(self.rt.transient_failure(self.run_for(), RuntimeError(CODEX_429), CODEX_429), 'exhausted')

    def test_output_already_produced_is_never_rerun(self):
        self.assertEqual(self.rt.transient_failure(self.run_for(text='partial'), RuntimeError(CODEX_429), CODEX_429),
                         'resume')
        self.assertEqual(self.rt.queues['astra'].items, [])

    def test_configuration_timeouts_and_other_errors_are_not_transient(self):
        for exc, detail in ((ValueError('key'), CODEX_429), (TimeoutError(), CODEX_429),
                            (RuntimeError('x'), 'MCP handshake closed'), (RuntimeError('x'), 'changed 500 lines')):
            self.assertEqual(self.rt.transient_failure(self.run_for(), exc, detail), '')

    def test_only_a_real_block_asks_for_refresh_connections(self):
        # harness ae809bdf: 'resume' leaves the agent ready, so its chat line must not ask anyone to act.
        resumed = RoomRuntime.failure_notice('astra', CODEX_429, 'resume')
        self.assertNotIn('Refresh', resumed)
        self.assertIn('carries on automatically', resumed)
        for transient in ('exhausted', ''):
            self.assertIn('Refresh connections', RoomRuntime.failure_notice('astra', CODEX_429, transient))

    def test_pattern_matches_provider_wordings(self):
        for detail in (CODEX_429, 'API Error: 529 Overloaded', 'rate_limit_error', 'API Error: 500 Internal server error',
                       '503 Service Unavailable', 'status: 502 Bad Gateway'):
            self.assertTrue(TRANSIENT_FAILURE.search(detail), detail)
        for detail in ('Invalid API key', 'not logged in', 'line 429 of the file'):
            self.assertFalse(TRANSIENT_FAILURE.search(detail), detail)


if __name__ == '__main__':
    unittest.main()
