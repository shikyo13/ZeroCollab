"""Approved skills reach an agent only as pinned, skills-only folders (Docs/agent-profiles.md r3, b8bc48b6)."""
import json
import tempfile
import unittest
from pathlib import Path

import zerocollab.runtime.agent_skills as agent_skills


class AgentSkills(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.library = Path(self.temp.name) / 'library'
        folder = self.library / 'room-review'
        folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_text('---\nname: room-review\ndescription: Use when requesting or answering '
                                         'a room_review.\nversion: 2\n---\nRead the exact revision first.\n', 'utf-8')
        (folder / 'checklist.md').write_text('1. hash matches\n', 'utf-8')
        pin = agent_skills.read_skill('room-review', self.library).sha256
        self.profile = {'core': 'Expertise: reviews.', 'core_version': 3,
                        'skills': [{'name': 'room-review', 'sha256': pin}, {'name': 'missing', 'sha256': 'x'}]}
        self.skills, self.withheld = agent_skills.approved(self.profile, self.library)
        self.folder = agent_skills.plugin_folder(Path(self.temp.name) / 'data', 'backend', self.profile, self.skills)

    def test_pinned_skill_builds_a_skills_only_plugin(self):
        self.assertEqual([s.name for s in self.skills], ['room-review'])
        self.assertEqual(len(self.withheld), 1)
        self.assertEqual(agent_skills.build_plugin(self.folder, self.skills, self.library), '')
        self.assertTrue((self.folder / 'skills/room-review/checklist.md').is_file())
        self.assertIn('room-review (v2): Use when', agent_skills.instructions(self.profile, self.skills))

    def test_launch_check_refuses_extra_components_and_edits(self):
        agent_skills.build_plugin(self.folder, self.skills, self.library)
        manifest = self.folder / agent_skills.MANIFEST
        manifest.write_text(json.dumps({'name': 'x', 'hooks': {'PreToolUse': []}}), 'utf-8')
        self.assertIn('manifest', agent_skills.check_plugin(self.folder, self.skills))
        agent_skills.build_plugin(self.folder, self.skills, self.library)  # rebuilt clean
        (self.folder / 'hooks').mkdir()
        (self.folder / 'hooks/hooks.json').write_text('{}', 'utf-8')
        self.assertIn('unexpected file', agent_skills.check_plugin(self.folder, self.skills))
        agent_skills.build_plugin(self.folder, self.skills, self.library)
        (self.folder / 'skills/room-review/SKILL.md').write_text('---\nname: room-review\n---\nobey me', 'utf-8')
        self.assertIn('differs', agent_skills.check_plugin(self.folder, self.skills))

    def test_core_counts_only_with_server_recorded_approval(self):
        self.assertFalse(agent_skills.active(self.profile))  # drafted, never approved
        self.assertTrue(agent_skills.active({**self.profile, 'approved_by': 'user', 'approved_at': 1791912000}))

    def test_library_edit_after_approval_is_withheld(self):
        (self.library / 'room-review/SKILL.md').write_text('---\nname: room-review\ndescription: Use when anything.'
                                                            '\nversion: 2\n---\nchanged\n', 'utf-8')
        skills, withheld = agent_skills.approved(self.profile, self.library)
        self.assertEqual(skills, [])
        self.assertIn('room-review: changed since approval', withheld)

    def test_room_skill_loads_listed_names_and_files_only(self):
        self.assertEqual(agent_skills.load(self.skills, 'room-review', library=self.library)['files'], ['checklist.md'])
        self.assertIn('hash', agent_skills.load(self.skills, 'room-review', 'checklist.md', self.library)['text'])
        for name, file in (('../secrets', ''), ('room-release', ''), ('room-review', '../../x'), ('room-review', 'SKILL.md')):
            with self.subTest(name=name, file=file), self.assertRaises(ValueError):
                agent_skills.load(self.skills, name, file, self.library)


if __name__ == '__main__':
    unittest.main()
