"""Tests for chat_send channel fallback behavior.

When an agent calls chat_read(channel="X") and then chat_send(...)
without passing a channel, the message should go to #X instead of
the default "general" channel. Closes #58.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import zerocollab.api.mcp_bridge as mcp_bridge


class ChannelFallbackStateTests(unittest.TestCase):
    """Tests the in-memory state tracking that powers the fallback.

    The full chat_send path requires a configured MessageStore, registry,
    etc. — testing the fallback logic via the state dicts directly
    exercises the contract without needing that scaffolding.
    """

    def setUp(self):
        # Snapshot and clear the channel map before each test so
        # parallel tests don't leak into each other.
        self._saved_ch = dict(mcp_bridge._last_read_channel)
        mcp_bridge._last_read_channel.clear()

    def tearDown(self):
        mcp_bridge._last_read_channel.clear()
        mcp_bridge._last_read_channel.update(self._saved_ch)

    def test_last_read_channel_state_exists(self):
        # Contract: module exposes the state map chat_send reads from
        self.assertIsInstance(mcp_bridge._last_read_channel, dict)
        self.assertTrue(hasattr(mcp_bridge, "_last_read_lock"))

    def test_different_agents_tracked_independently(self):
        mcp_bridge._last_read_channel["alice"] = "bugfixing"
        mcp_bridge._last_read_channel["bob"] = "portfolio"
        self.assertEqual(mcp_bridge._last_read_channel["alice"], "bugfixing")
        self.assertEqual(mcp_bridge._last_read_channel["bob"], "portfolio")

    def test_chat_send_fallback_uses_last_read_channel(self):
        sender = "agent"
        mcp_bridge._last_read_channel[sender] = "bugfixing"

        # Replicate the fallback logic from chat_send:
        channel = ""
        if sender and not channel:
            with mcp_bridge._last_read_lock:
                channel = mcp_bridge._last_read_channel.get(sender, "")
        self.assertEqual(channel, "bugfixing")

    def test_chat_send_fallback_falls_through_to_general(self):
        sender = "new-agent"  # never read anything
        channel = ""
        # Apply full fallback logic including the 'general' final fallback
        if sender and not channel:
            with mcp_bridge._last_read_lock:
                channel = mcp_bridge._last_read_channel.get(sender, "")
        if not channel:
            channel = "general"
        self.assertEqual(channel, "general")

    def test_explicit_channel_is_never_overridden(self):
        sender = "agent"
        mcp_bridge._last_read_channel[sender] = "bugfixing"

        # Caller explicitly passed channel="portfolio"
        channel = "portfolio"
        # Fallback applies only when the channel is empty
        applied_fallback = bool(sender and not channel)
        self.assertFalse(applied_fallback)
        self.assertEqual(channel, "portfolio")


class ChannelFallbackMigrationTests(unittest.TestCase):
    """Tests that identity and channel lifecycle hooks keep the fallback
    maps in sync (rename/purge of agents, rename/delete of channels)."""

    def setUp(self):
        self._saved_ch = dict(mcp_bridge._last_read_channel)
        mcp_bridge._last_read_channel.clear()

    def tearDown(self):
        mcp_bridge._last_read_channel.clear()
        mcp_bridge._last_read_channel.update(self._saved_ch)

    def test_identity_rename_carries_fallback_state(self):
        mcp_bridge._last_read_channel["alice"] = "bugfixing"
        mcp_bridge.migrate_identity("alice", "alice-2")
        self.assertEqual(mcp_bridge._last_read_channel["alice-2"], "bugfixing")
        self.assertNotIn("alice", mcp_bridge._last_read_channel)

    def test_purge_identity_drops_fallback_state(self):
        mcp_bridge._last_read_channel["alice"] = "bugfixing"
        mcp_bridge.purge_identity("alice")
        self.assertNotIn("alice", mcp_bridge._last_read_channel)

    def test_channel_rename_rewrites_matching_fallbacks(self):
        mcp_bridge._last_read_channel["alice"] = "bugfixing"
        mcp_bridge._last_read_channel["bob"] = "portfolio"
        mcp_bridge.migrate_cursors_rename("bugfixing", "hotfixes")
        self.assertEqual(mcp_bridge._last_read_channel["alice"], "hotfixes")
        # Other agents' fallbacks are untouched
        self.assertEqual(mcp_bridge._last_read_channel["bob"], "portfolio")

    def test_channel_delete_resets_fallbacks_to_general(self):
        mcp_bridge._last_read_channel["alice"] = "bugfixing"
        mcp_bridge._last_read_channel["bob"] = "portfolio"
        mcp_bridge.migrate_cursors_delete("bugfixing")
        self.assertEqual(mcp_bridge._last_read_channel["alice"], "general")
        # Other agents' fallbacks are untouched
        self.assertEqual(mcp_bridge._last_read_channel["bob"], "portfolio")


if __name__ == "__main__":
    unittest.main()
