"""Release completions are delivered only while the release core allows them (Astra 3a7da7ef)."""
import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import tests.test_pending_recovery as pending  # the module, so its own tests are not collected here

KEY = 'dep-1:a'


class FilterReached(BaseException):
    """Not an Exception, so run_turn's own error handling lets it through."""


class ReleaseCompletionFilterTests(unittest.IsolatedAsyncioTestCase):
    provider = pending.PendingRecoveryTests.provider  # the same fixture, without inheriting its tests

    def runtime(self):
        r = pending.PendingRecoveryTests.runtime(self)
        r.decisions, r.ready_wakes, r.manager_wakes = Mock(), Mock(), Mock()
        return r

    def mixed_entry(self):
        return {'channel': 'general', 'chain': 'c', 'requests': [
            {'human': True, 'chain': 'c', 'prompt': 'from Zero'},
            {'human': False, 'chain': 'c', 'prompt': 'release finished', 'release_key': KEY}]}

    async def delivered(self, allowed):
        r = self.runtime()
        r.release_manager = SimpleNamespace(completion_allowed=lambda key, recipient: allowed)
        self.provider(r)
        seen = []

        def cover(name, requests):
            # Called with the requests that will start the turn, right after the filter; stop there.
            seen.extend(q['prompt'] for q in requests)
            raise FilterReached
        r.ready_wakes.cover = cover
        with self.assertRaises(FilterReached):
            await r.run_turn('a', self.mixed_entry())
        return seen

    async def test_revoked_after_queue_drops_only_the_completion(self):
        self.assertEqual(await self.delivered(False), ['from Zero'])

    async def test_allowed_completion_is_delivered_with_the_rest(self):
        self.assertEqual(await self.delivered(True), ['from Zero', 'release finished'])

    async def test_no_core_fails_closed(self):
        r = self.runtime()
        self.assertFalse(r.release_completion_allowed(KEY, 'a'))
        r.release_manager = SimpleNamespace(completion_allowed=Mock(side_effect=RuntimeError('broken')))
        self.assertFalse(r.release_completion_allowed(KEY, 'a'))

    def test_restore_keeps_denied_records_pending(self):
        r = self.runtime()
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        r.deployment = SimpleNamespace(root=root, lock=threading.RLock(), rehearsal=False)
        r.identity, r.paused, r.release_wakes_lock = 'p1', False, threading.Lock()
        (root / 'release-wakes.json').write_text(
            '{"version": 1, "wakes": {"%s": {"project": "p1", "recipient": "a", "prompt": "done", '
            '"state": "pending", "created": 1}}}' % KEY, 'utf-8')
        r.enqueue = Mock(return_value=True)
        r.release_manager = SimpleNamespace(completion_allowed=lambda key, recipient: False)
        self.assertEqual(r.restore_release_wakes(), 0)
        r.enqueue.assert_not_called()
        r.release_manager = SimpleNamespace(completion_allowed=lambda key, recipient: True)
        self.assertEqual(r.restore_release_wakes(), 1)
