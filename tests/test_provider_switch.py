"""Existing-member connection switches: real profile/runtime/session paths, no provider processes."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from zerocollab.coordination.project_room import ProjectRoom
from zerocollab.runtime.room_runtime import RoomRuntime
from zerocollab.storage.store import MessageStore
from tests.test_agent_identity import AgentIdentityTests


class ProviderSwitchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        AgentIdentityTests.setUp(self)
        self.r.running = {}
        self.r.identity = 'project'
        self.r.cfg['server'] = {'port': 1}
        self.r.host.store = MessageStore(str(self.r.data / 'messages.jsonl'))
        self.r.diffs = lambda: {}
        self.r.channel_title = lambda channel: channel
        self.r.states['astra'].update(provider='codex', template='codex', state='ready')
        self.r.queues = {'astra': object()}
        self.queue = self.r.queues['astra']
        catalog = {'models': [{'value': 'model', 'label': 'Model', 'efforts': ['high']}],
                   'error': '', 'loading': False}
        self.r.controls.catalogs['astra'] = copy.deepcopy(catalog)
        for name in ('claude', 'claude-two'):
            self.r.profiles.templates[name] = {**self.r.profiles.templates['codex'], 'provider': 'claude'}
            self.r.profiles.template_catalogs[name] = copy.deepcopy(catalog)
        self.r.project_room = ProjectRoom(self.r)
        self.r.project_room.use_project('project')
        self.room = self.r.project_room
        self.room.state['sessions'] = {
            'astra:general': self.saved(), 'astra:other': self.saved(), 'astral:general': self.saved()}
        self.room.state['cursors'] = {'astra:general': {'message_id': 17}}
        self.room.state['receipts'] = {'astra:general': ['message:17']}
        self.room.save()

    def saved(self, provider='codex', **extra):
        return {'id': 'synthetic-session', 'provider': provider, 'settings': {'model': 'model', 'effort': 'high'},
                'instruction_hash': 'h', 'context': {'message_id': 17}, **extra}

    def turn(self):
        return SimpleNamespace(name='astra', channel='general', session_key='astra:general',
            settings={'model': 'model', 'effort': 'high'}, instruction_hash='h',
            provider_session='new-synthetic-session', context_delivery={'message_id': 18}, receipts=set())

    async def switch(self, template):
        return await RoomRuntime.change_profile(self.r, {'template': template}, 'astra')

    async def test_switch_back_preserves_identity_queue_and_resets_every_channel(self):
        token = self.registry.get_instance('astra')['identity_id']
        original = copy.deepcopy(self.r.profiles.items['astra'])
        for template, provider in [('claude', 'claude'), ('codex', 'codex')]:
            await self.switch(template)
            self.assertEqual(self.r.states['astra']['provider'], provider)
            self.assertEqual(self.r.states['astra']['template'], template)
            self.assertEqual(self.r.states['astra']['worktree'], 'existing-tree')
            self.assertIs(self.r.queues['astra'], self.queue)
            self.assertEqual(self.registry.get_instance('astra')['identity_id'], token)
            self.assertEqual(self.registry.get_base_config('astra')['provider'], provider)
            for key in ('id', 'name', 'color', 'background'):
                self.assertEqual(self.r.profiles.items['astra'][key], original[key])
            self.assertEqual(self.r.profiles.items['astra']['handle'], 'astra')
            self.assertEqual(self.room.previous_session(self.turn()), {})
            self.assertEqual(set(self.room.state['sessions']), {'astral:general'})
            self.room.complete(self.turn())
            self.assertEqual(self.room.state['sessions']['astra:general']['template'], template)
        self.assertEqual(self.room.state['cursors']['astra:general'], {'message_id': 18})
        self.assertEqual(self.room.state['receipts']['astra:general'], ['message:17'])

    async def test_same_kind_connection_and_legacy_sessions_cannot_resume_after_switch(self):
        await self.switch('claude')
        self.room.complete(self.turn())
        self.assertTrue(self.room.previous_session(self.turn()))
        old = copy.deepcopy(self.room.state['sessions']['astra:general'])
        await self.switch('claude-two')
        self.assertEqual(self.room.previous_session(self.turn()), {})
        self.room.state['sessions']['astra:general'] = old
        self.assertEqual(self.room.previous_session(self.turn()), {})
        del old['template']
        self.assertTrue(self.room.previous_session(self.turn()))  # pre-switch legacy compatibility
        await self.switch('claude')
        self.assertEqual(self.room.previous_session(self.turn()), {})

    async def test_running_switch_refuses_without_mutation(self):
        before = copy.deepcopy(self.room.state)
        self.r.running['astra'] = object()
        with self.assertRaisesRegex(ValueError, 'current turn'):
            await self.switch('claude')
        self.assertEqual(self.r.profiles.items['astra']['template'], 'codex')
        self.assertEqual(self.room.state, before)

    async def test_same_model_strings_still_validate_destination_and_offered_flag(self):
        self.r.profiles.template_catalogs['claude']['models'][0]['value'] = 'different'
        with self.assertRaisesRegex(ValueError, 'current catalog'):
            await self.switch('claude')
        self.r.profiles.templates['claude'].update(key_target='synthetic', enabled=False)
        with self.assertRaisesRegex(ValueError, 'turned off'):
            await self.switch('claude')
        self.assertEqual(self.r.profiles.items['astra']['template'], 'codex')

    async def test_reset_persistence_failure_refuses_before_grant_or_profile_changes(self):
        grant = Mock()
        self.r.deployment = SimpleNamespace(release_grant=grant)
        with patch.object(self.room, 'save', side_effect=OSError('disk unavailable')):
            with self.assertRaisesRegex(OSError, 'disk unavailable'):
                await self.switch('claude')
        self.assertEqual(self.r.profiles.items['astra']['template'], 'codex')
        self.assertEqual(self.r.states['astra']['provider'], 'codex')
        grant.invalidate_profile.assert_not_called()

    async def test_switch_lapses_release_grant_and_persists_session_reset(self):
        grant = Mock()
        self.r.deployment = SimpleNamespace(release_grant=grant)
        await self.switch('claude')
        grant.invalidate_profile.assert_called_once_with('project', 'astra')
        restored = ProjectRoom(self.r)
        restored.use_project('project')
        self.assertNotIn('astra:general', restored.state['sessions'])
        self.assertNotIn('astra:other', restored.state['sessions'])
        self.assertIn('astral:general', restored.state['sessions'])


if __name__ == '__main__':
    unittest.main()
