"""E3 release train as a room action (Docs/process-enforcement.md r3, task a7da2f10)."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import process_enforcement as pe
from project_room import ProjectRoom, RELEASE_GRANTEE

HOUR = 3600


class ReleaseTrain(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.gate, self.staged, self.chat, self.woken = '', None, [], []
        self.runtime = SimpleNamespace(
            data=root / 'data', host=SimpleNamespace(router=None, store=SimpleNamespace(
                add=lambda sender, text, **kw: self.chat.append((sender, text)))),
            states={name: {'worktree': str(root)} for name in (RELEASE_GRANTEE, 'opus')},
            activity=lambda name: {'running': False, 'queued': 0}, identity='test',
            ready_wakes=SimpleNamespace(room_gate=lambda: self.gate),
            deployment=SimpleNamespace(enter_mutation=lambda: True, leave_mutation=lambda: None),
            release_manager=SimpleNamespace(successful_stage_since=lambda project, since: self.staged),
            deliver_record=lambda recipient, items, channel, chain, text, ref: self.woken.append(recipient),
            owns_channel=lambda channel: True, default_channel=lambda: 'main',
            decisions=SimpleNamespace(routes=lambda channel: True), manager_wakes=SimpleNamespace(record={}))
        self.room = ProjectRoom(self.runtime)
        self.room.use_project('test')
        self.runtime.project_room = self.room
        self.task = self.room.task(RELEASE_GRANTEE, 'create', title='Approved fix')
        self.signals = {'visible': [self.task], 'installed_at': 1000.0, 'due': True}
        self.room.delivery_signals = lambda tasks: self.signals
        self.now = 1000.0 + 3 * HOUR

    def tick(self, after=0):
        self.now += after
        return pe.tick(self.runtime, now=self.now)

    def items(self, recipient):
        return [f for f in self.room.state.get('followups', []) if f['assigned_to'] == recipient and f['created_by'] == 'system']

    def test_opens_one_item_and_wakes_the_release_manager_once(self):
        self.assertEqual(self.tick(), ['opened'])
        self.assertEqual(self.tick(pe.TICK_SECONDS), [])
        self.assertEqual(len(self.items(RELEASE_GRANTEE)), 1)
        self.assertEqual(self.woken, [RELEASE_GRANTEE])
        self.assertIn('E3 release train: 1 approved user-visible tasks have waited 3.0 h', self.items(RELEASE_GRANTEE)[0]['text'])

    def test_escalates_to_the_user_once_unless_staged(self):
        self.tick()
        self.staged = {'release_id': 'abc'}
        self.assertEqual(self.tick(pe.TRAIN_ESCALATE_SECONDS), [])
        self.staged = None
        self.assertEqual(self.tick(pe.TICK_SECONDS), ['escalated'])
        self.assertEqual(self.tick(pe.TRAIN_ESCALATE_SECONDS), [])
        self.assertEqual(len(self.items('user')), 1)

    def test_an_install_closes_both_items(self):
        self.tick()
        self.tick(pe.TRAIN_ESCALATE_SECONDS)
        self.signals = {'visible': [self.task], 'installed_at': self.now + 10, 'due': False}
        self.assertEqual(self.tick(pe.TICK_SECONDS), ['closed', 'closed'])
        self.assertEqual({f['status'] for f in self.items(RELEASE_GRANTEE) + self.items('user')}, {'resolved'})
        self.assertEqual(self.room.state['enforcement']['train'], {})

    def test_closing_without_staging_waits_out_the_cooldown(self):
        self.tick()
        item = self.items(RELEASE_GRANTEE)[0]
        self.room.records.followup(RELEASE_GRANTEE, 'resolve', followup_id=item['id'], result='not now')
        self.assertEqual(self.tick(pe.TICK_SECONDS), [])
        self.assertEqual(self.tick(pe.TRAIN_COOLDOWN_SECONDS / 2), [])
        # Records stamp real time; move the recorded close a full cooldown before this test's clock.
        self.room.state['enforcement']['train']['closed_at'] = self.now - pe.TRAIN_COOLDOWN_SECONDS
        self.assertEqual(self.tick(pe.TICK_SECONDS), ['opened'])
        self.assertEqual(len(self.items(RELEASE_GRANTEE)), 2)

    def test_adopts_an_untracked_item_and_recovers_from_a_lost_pointer(self):
        self.tick()
        self.room.state['enforcement']['train'] = {}  # as if the board save was lost after the item was created
        self.assertEqual(self.tick(pe.TICK_SECONDS), [])
        self.assertEqual(len(self.items(RELEASE_GRANTEE)), 1)
        self.room.state['enforcement']['train'] = {'item': 'gone0000', 'opened': self.now}
        self.room.state['followups'].clear()
        self.assertEqual(self.tick(pe.TICK_SECONDS), ['opened'])

    def test_adopts_an_untracked_escalation_item(self):
        self.tick()
        self.tick(pe.TRAIN_ESCALATE_SECONDS)
        self.room.state['enforcement']['train'].pop('escalation')  # as if that board save was lost
        self.assertEqual(self.tick(pe.TRAIN_ESCALATE_SECONDS), [])
        self.assertEqual(len(self.items('user')), 1)

    def test_quiet_without_a_registered_release_manager(self):
        del self.runtime.states[RELEASE_GRANTEE]
        self.assertEqual(self.tick(), [])  # E3 is quiet; the tick still runs for L1, which needs no release manager
        self.assertEqual(self.items(RELEASE_GRANTEE), [])

    def test_nothing_while_held_or_when_nothing_waits(self):
        self.gate = 'room stopped'
        self.assertIsNone(self.tick())
        self.gate = ''
        self.tick(pe.TICK_SECONDS)
        self.signals = {'visible': [], 'installed_at': 1000.0, 'due': False}
        self.assertEqual(self.tick(pe.TICK_SECONDS), ['closed'])
        self.assertEqual(self.items(RELEASE_GRANTEE)[0]['status'], 'cancelled')


if __name__ == '__main__':
    unittest.main()
