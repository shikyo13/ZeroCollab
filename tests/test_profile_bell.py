"""Role profile drafts reach the user's bell (task b0eaf89e, Zero f14bfb67)."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import profile_bell
from agent_profiles import AgentProfiles
from project_room import ProjectRoom

CORE = """---
agent: {name}
version: {version}
skills: [{skills}]
---
## Expertise

{text}

## Responsibilities

Keep it right.

## Boundaries

Stay in scope.

## Quality bars

Prove it.
"""


class Profiles(AgentProfiles):
    """The real draft, approve and revoke code on a scratch library, without provider setup."""

    def __init__(self, path, library):
        self.path, self.library = path, library
        self.items = {name: {'id': name, 'name': name.title(), 'archived': False} for name in ('scratch', 'other')}

    def core_draft(self, name, **_):
        return super().core_draft(name, library=self.library, skills_library=self.library)


class ProfileBell(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.library = root / 'agent-profiles'
        self.chat = []
        self.runtime = SimpleNamespace(
            data=root / 'data', identity='test', host=SimpleNamespace(router=None, store=SimpleNamespace(
                add=lambda sender, text, **kw: self.chat.append(text))),
            states={'scratch': {'worktree': str(root)}, 'other': {'worktree': str(root)}},
            activity=lambda name: {'running': False, 'queued': 0},
            ready_wakes=SimpleNamespace(room_gate=lambda: ''), project_state_soon=lambda: None,
            owns_channel=lambda channel: True, default_channel=lambda: 'main',
            decisions=SimpleNamespace(routes=lambda channel: True), manager_wakes=SimpleNamespace(record={}))
        self.runtime.profiles = Profiles(root / 'agents.json', self.library)
        self.room = self.runtime.project_room = ProjectRoom(self.runtime)
        self.room.use_project('test')

    def draft(self, name, version, text='Backend work.', skills=''):
        folder = self.library / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'core.md').write_text(CORE.format(name=name, version=version, text=text, skills=skills), 'utf-8')

    def skill(self, version):
        folder = self.library / 'room-review'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'SKILL.md').write_text(f'---\nname: room-review\ndescription: Use when reviewing.\n'
                                         f'version: {version}\n---\nFreeze, then request.\n', 'utf-8')

    def sync(self):
        return profile_bell.sync(self.runtime, profile_bell.drafts(self.runtime))

    def items(self, status='open'):
        return [f for f in self.room.state.get('followups', []) if f['created_by'] == 'system' and f['status'] == status]

    def test_one_item_per_draft_and_a_restart_adopts_it(self):
        self.assertEqual(self.sync(), [])  # no drafts, no items
        self.draft('scratch', 1)
        self.assertEqual(self.sync(), ['opened'])
        item, = self.items()
        self.assertEqual(item['assigned_to'], 'user')
        self.assertIn('scratch (@scratch)', item['text'])
        self.assertIn("version 1, 19 words, 0 skills. Open Team, then Scratch's edit card", item['text'])
        reloaded = ProjectRoom(self.runtime)  # a restart reads the saved board; nothing points at the item
        reloaded.use_project('test')
        self.runtime.project_room = self.room = reloaded
        self.assertEqual(self.sync(), [])
        self.assertEqual(len(self.items()), 1)
        self.draft('other', 1)
        self.assertEqual(self.sync(), ['opened'])  # two agents, two items
        self.assertEqual(len(self.items()), 2)

    def test_a_newer_draft_rewrites_the_open_item(self):
        self.draft('scratch', 1)
        self.sync()
        self.draft('scratch', 2, 'Backend work, revised.')
        self.assertEqual(self.sync(), ['updated'])
        item, = self.items()
        self.assertIn('version 2, 20 words', item['text'])

    def test_approve_resolves_and_revoke_stays_closed_until_a_new_draft(self):
        profiles = self.runtime.profiles
        self.draft('scratch', 1)
        self.sync()
        profiles.approve_core('scratch', profiles.core_draft('scratch')['digest'], '2026-10-13T16:00:00-05:00')
        self.assertEqual(self.sync(), ['closed'])
        self.assertIn('Approved: version 1', self.items('resolved')[0]['result'])
        self.draft('scratch', 2)
        self.sync()
        profiles.revoke_core('scratch')
        self.assertEqual(self.sync(), ['closed'])
        self.assertIn('Revoked. Version 2', self.items('resolved')[-1]['result'])
        self.assertEqual(self.sync(), [])  # revoked: not reopened
        self.draft('scratch', 3)
        self.assertEqual(self.sync(), ['opened'])  # a new draft asks again

    def test_a_skill_changed_after_approval_asks_again(self):
        # harness 1de4eb74 B1: the core version is unchanged, but the approved skill pin no longer matches.
        profiles = self.runtime.profiles
        self.skill(1)
        self.draft('scratch', 1, skills='room-review')
        self.sync()
        profiles.approve_core('scratch', profiles.core_draft('scratch')['digest'], '2026-10-13T16:00:00-05:00')
        self.assertEqual(self.sync(), ['closed'])
        self.assertEqual(self.sync(), [])  # approved and unchanged: no item
        self.skill(2)
        self.assertEqual(self.sync(), ['opened'])
        item, = self.items()
        self.assertIn('version 1, 19 words, 1 skill. Since your approval: room-review changed (now v2).', item['text'])

    def test_held_room_does_nothing(self):
        self.draft('scratch', 1)
        self.runtime.ready_wakes = SimpleNamespace(room_gate=lambda: 'maintenance')
        self.assertIsNone(self.sync())
        self.assertEqual(self.items(), [])


if __name__ == '__main__':
    unittest.main()
