"""Role-core approval (task b8bc48b6, security 3a00bef2 A2): a drafted core.md is imported into the profile record
only by the user's approve action, bound to the exact draft reviewed; approval fields never come from the file
or a profile edit; revoke returns to the background."""
import tempfile
import unittest
from pathlib import Path

import agent_profiles
import agent_skills

CORE = """---
agent: backend
version: 2
approved_by: "someone"
approved_at: "2020-01-01"
skills: [room-review]
---

## Expertise

Python services.

## Responsibilities

Keep the room correct.

## Boundaries

No credential reads.

## Quality bars

Exact identities.
"""
SKILL = """---
name: room-review
description: Use when requesting or answering a room_review.
version: 1
---
Freeze the donor, then request.
"""


class ProfileCore(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.cores, self.skills = root / 'agent-profiles', root / 'agent-skills'
        (self.cores / 'backend').mkdir(parents=True)
        (self.skills / 'room-review').mkdir(parents=True)
        (self.cores / 'backend' / 'core.md').write_text(CORE, 'utf-8')
        (self.skills / 'room-review' / 'SKILL.md').write_text(SKILL, 'utf-8')
        self.profiles = agent_profiles.AgentProfiles.__new__(agent_profiles.AgentProfiles)
        self.profiles.items = {'backend': {'id': 'backend', 'background': 'old paragraph'}}
        self.profiles.path = root / 'agents.json'
        self.libraries = {'library': self.cores, 'skills_library': self.skills}

    def draft(self):
        return self.profiles.core_draft('backend', **self.libraries)

    def test_draft_ignores_file_approval_and_pins_skills(self):
        draft = self.draft()
        self.assertEqual((draft['core_version'], [s['name'] for s in draft['skills']]), (2, ['room-review']))
        self.assertTrue(draft['core'].startswith('## Expertise'))
        self.assertNotIn('approved_by', draft)
        self.assertFalse(self.profiles.core_state('backend')['active'])

    def test_approve_binds_to_the_reviewed_digest(self):
        digest = self.draft()['digest']
        (self.cores / 'backend' / 'core.md').write_text(CORE.replace('Python services.', 'Edited.'), 'utf-8')
        with self.assertRaisesRegex(ValueError, 'changed since you reviewed'):
            self.profiles.approve_core('backend', digest, '2026-10-13T15:00:00', **self.libraries)
        state = self.profiles.approve_core('backend', self.draft()['digest'], '2026-10-13T15:00:00', **self.libraries)
        self.assertTrue(state['active'])
        self.assertEqual((state['approved_by'], state['approved_at']), ('user', '2026-10-13T15:00:00'))
        skills, withheld = agent_skills.approved(self.profiles.items['backend'], self.skills)
        self.assertEqual(([s.name for s in skills], withheld), (['room-review'], []))

    def test_a_skill_edited_after_approval_is_withheld(self):
        self.profiles.approve_core('backend', self.draft()['digest'], 'now', **self.libraries)
        (self.skills / 'room-review' / 'SKILL.md').write_text(SKILL + 'More.\n', 'utf-8')
        skills, withheld = agent_skills.approved(self.profiles.items['backend'], self.skills)
        self.assertEqual(skills, [])
        self.assertIn('changed since approval', withheld[0])

    def test_revoke_returns_to_the_background(self):
        self.profiles.approve_core('backend', self.draft()['digest'], 'now', **self.libraries)
        state = self.profiles.revoke_core('backend')
        self.assertFalse(state['active'])
        self.assertEqual(self.profiles.items['backend'], {'id': 'backend', 'background': 'old paragraph'})

    def test_refused_drafts(self):
        for bad, why in ((CORE.replace('agent: backend', 'agent: opus'), 'names agent'),
                         (CORE.replace('version: 2', 'version: x'), 'whole number'),
                         (CORE.replace('## Boundaries', '## Limits'), 'sections'),
                         (CORE.replace('Python services.', 'word ' * 401), 'keep it to'),
                         (CORE.replace('room-review', 'missing-skill'), 'missing-skill')):
            (self.cores / 'backend' / 'core.md').write_text(bad, 'utf-8')
            with self.assertRaisesRegex(ValueError, why):
                self.draft()
        with self.assertRaisesRegex(ValueError, 'not found'):
            self.profiles.core_draft('nobody', **self.libraries)


if __name__ == '__main__':
    unittest.main()
