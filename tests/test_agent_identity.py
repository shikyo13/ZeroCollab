"""Profile rename preserves ownership and routes aliases; no providers or live data."""
import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from zerocollab.runtime.agent_profiles import AgentProfiles
from zerocollab.runtime.registry import RuntimeRegistry
from zerocollab.runtime.room_runtime import RoomRuntime
from zerocollab.coordination.router import Router


class AgentIdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.registry = RuntimeRegistry(self.tmp.name)
        self.router = Router([], default_mention='none')
        self.registry.on_change(lambda: self.router.update_agents(
            self.registry.get_active_names(), self.registry.get_mention_aliases()))
        cfg = {'managed': True, 'provider': 'codex', 'label': 'Codex', 'model': 'model',
               'effort': 'high', 'color': '#123456'}
        self.r = SimpleNamespace(data=Path(self.tmp.name), project=None,
            cfg={'agents': {'codex': cfg}}, states={}, profile_lock=asyncio.Lock(),
            host=SimpleNamespace(registry=self.registry, broadcast_agents=AsyncMock()),
            publish_status=AsyncMock(), controls=SimpleNamespace(saved={}, catalogs={}, values={}))
        self.r.profiles = AgentProfiles(self.r)
        self.r.profiles.load('project')
        self.r.save_project = self.r.profiles.save
        self.profile = dict(id='astra', template='codex', name='Astra', color='#123456',
                            model='model', effort='high', background='', archived=False)
        self.r.profiles.items = {'astra': dict(self.profile)}
        self.r.states = {'astra': dict(label='Astra', color='#123456', worktree='existing-tree', paused=False)}
        self.r.controls.values['astra'] = {'model': 'model', 'effort': 'high'}
        self.registry.register_managed('astra', self.r.profiles.config(self.profile))

    async def rename(self, name='Sol', handle='sol'):
        return await RoomRuntime.change_profile(self.r, {'name': name, 'handle': handle}, 'astra')

    async def test_rename_preserves_identity_and_broadcasts_updated_name(self):
        token = self.registry.get_instance('astra')['identity_id']
        await self.rename()
        self.assertEqual(set(self.r.states), {'astra'})
        self.assertEqual(self.r.states['astra']['worktree'], 'existing-tree')
        self.assertEqual(self.registry.get_instance('astra')['identity_id'], token)
        self.assertEqual(self.r.profiles.items['astra']['id'], 'astra')
        self.assertEqual(self.r.states['astra']['label'], 'Sol')
        self.assertEqual(self.registry.get_agent_config()['astra']['handle'], 'sol')
        self.r.host.broadcast_agents.assert_awaited_once()
        self.r.publish_status.assert_awaited_once()
        for text in ('@sol', '@astra', '@SOL @astra'):
            self.assertEqual(self.router.get_targets('user', text), ['astra'])
        self.assertEqual(self.router.get_targets('astra', '@sol'), [])
        self.assertEqual(self.router.get_targets('user', '@all'), ['astra'])

    async def test_aliases_persist_and_new_agents_cannot_reuse_them(self):
        await self.rename()
        await self.rename('Sun', 'sun')
        restored = AgentProfiles(self.r)
        restored.load('project')
        for value in ('astra', 'sol', 'sun', '@SOL'):
            self.assertEqual(restored.resolve(value), 'astra')
        self.assertEqual(restored.recipient_arguments({'owner': 'sol', 'reviewer': 'sun', 'assigned_to': '@sol'}),
                         {'owner': 'astra', 'reviewer': 'astra', 'assigned_to': 'astra'})
        self.r.profiles.items['other'] = {**self.profile, 'id': 'other'}
        for handle in ('astra', 'sol', 'sun'):
            with self.assertRaises(ValueError):
                self.r.profiles.prepare({'handle': handle}, 'other')
        with self.assertRaises(ValueError):
            self.r.profiles.prepare({**self.profile, 'id': 'sol'})
        self.assertEqual(self.router.get_targets('user', '@sol @sun'), ['astra'])
        await self.rename('Astra again', 'astra')
        self.assertEqual(self.router.get_targets('user', '@sol @sun @astra'), ['astra'])

    async def test_invalid_handle_has_no_partial_save(self):
        before = copy.deepcopy(self.r.profiles.items)
        for handle in ('all', 'user', 'system', 'two words', '', None, 7):
            with self.assertRaises(ValueError):
                await self.rename(handle=handle)
        self.assertEqual(before, self.r.profiles.items)
        self.r.host.broadcast_agents.assert_not_awaited()

    async def test_legacy_edit_and_archived_alias(self):
        saved = await RoomRuntime.change_profile(self.r, {'name': 'New label'}, 'astra')
        self.assertEqual(saved['handle'], 'astra')
        await self.rename()
        self.r.profiles.items['astra']['archived'] = True
        self.registry.deactivate_managed('astra')
        self.assertEqual(self.router.parse_mentions('@sol'), [])
        self.assertEqual(self.r.profiles.resolve('sol'), 'astra')


if __name__ == '__main__':
    unittest.main()
