"""Team-builder consent and recovery checks; no providers or live services."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from zerocollab.runtime.agent_profiles import AgentProfiles
from zerocollab.runtime.room_runtime import RoomRuntime
from zerocollab.coordination.team_builder import TeamBuilder, install


class TeamBuilderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls, self.created = [], []
        self.r = SimpleNamespace(identity='test', project=None, data=Path(self.tmp.name), paused=False,
            cfg={'agents': {'native': {'managed': True, 'provider': 'codex', 'model': 'model',
                                     'effort': '', 'color': '#123456'}}},
            states={n: {'provider': p, 'state': 'ready'} for n, p in [('astra', 'codex'), ('opus', 'claude')]},
            profile_lock=asyncio.Lock(), goals=SimpleNamespace(goal={'text': 'saved goal'}),
            project_room=SimpleNamespace(path=Path(self.tmp.name) / 'coordination.json',
                state={'spec': 'saved spec'}, new_chain=lambda channel: 'chain'),
            parallel_capacity=lambda: 2, default_channel=lambda: 'general', owns_channel=lambda c: c == 'general',
            host=SimpleNamespace(registry=SimpleNamespace(get_instance=lambda n: None), broadcast_agents=AsyncMock()),
            publish_status=AsyncMock(), emit=AsyncMock())
        catalog = {'models': [{'value': 'model', 'efforts': []}], 'selected': {'model': 'model', 'effort': ''}, 'error': ''}
        def validate(source, model, effort):
            if model != 'model' or effort:
                raise ValueError('Invalid provider model')
        self.r.controls = SimpleNamespace(saved={}, catalogs={'native': catalog},
            describe=lambda name: copy.deepcopy(catalog), validate=validate)
        self.r.profiles = AgentProfiles(self.r)
        self.r.profiles.load('test')
        self.r.save_project = self.r.profiles.save
        self.r.workspace_for = lambda name: {'worktree': str(Path(self.tmp.name) / name)}
        def activate(profile, workspace):
            self.created.append(profile['id'])
            self.r.states[profile['id']] = {'provider': 'codex', 'state': 'ready'}
        self.r.activate_profile = activate
        self.r.change_profile = lambda body, **kwargs: RoomRuntime.change_profile(self.r, body, **kwargs)
        def enqueue(*args, **kwargs):
            self.calls.append((args, copy.deepcopy(kwargs)))
            return True
        self.r.enqueue = enqueue
        self.b = TeamBuilder(self.r)
        self.r.team_builder = self.b
        self.b.load()

    def member(self, handle):
        return dict(name=handle.title(), id=handle, template='native', model='model', effort='',
                    background='Expert on the project need.', rationale='Owns a missing skill.')

    def payload(self, handles=('researcher', 'designer')):
        return dict(members=[self.member(h) for h in handles],
            coverage=[{'need': h, 'lead': [h], 'support': []} for h in handles], summary='Reviewed project needs.')

    def ready(self):
        p = self.b.start({'source': 'text', 'brief': 'Build a tutorial hub'})
        self.b.submit('astra', p['id'], p['revision'], self.payload())
        return self.b.submit('opus', p['id'], p['revision'], self.payload())

    def action(self, member, action, **kwargs):
        return self.b.member_action(self.b.proposal['id'], member['member_id'], {'action': action, **kwargs})

    async def test_approval_does_not_create_and_add_is_idempotent(self):
        p = self.ready()
        self.action(p['members'][0], 'approve')
        self.assertEqual(self.created, [])
        self.assertEqual(len(self.calls), 2)
        first = await self.b.add(p['id'])
        second = await self.b.add(p['id'])
        self.assertEqual(self.created, ['researcher'])
        self.assertEqual(first['members'], second['members'])
        self.assertEqual(second['members'][1]['status'], 'proposed')
        self.assertEqual(len(self.calls), 2, 'Adding must not wake either new or existing agents')

    async def test_collision_keeps_success_and_retry_adds_only_missing(self):
        p = self.ready()
        for m in p['members']: self.action(m, 'approve')
        self.r.host.registry.get_instance = lambda n: object() if n == 'designer' else None
        result = await self.b.add(p['id'])
        self.assertEqual([m['status'] for m in result['members']], ['added', 'approved'])
        self.assertIn('already in use', result['members'][1]['error'])
        self.r.host.registry.get_instance = lambda n: None
        result = await self.b.add(p['id'])
        self.assertEqual(self.created, ['researcher', 'designer'])
        self.assertTrue(all(m['status'] == 'added' for m in result['members']))

    async def test_profile_key_recovers_creation_before_result_save(self):
        p = self.ready()
        self.action(p['members'][0], 'approve')
        before = copy.deepcopy(self.b.proposal)
        await self.b.add(p['id'])
        self.b.proposal = before
        self.b.save()
        self.b.load()
        result = await self.b.add(p['id'])
        self.assertEqual(result['members'][0]['status'], 'added')
        self.assertEqual(self.created, ['researcher'])

    async def test_edit_resets_consent_and_updates_coverage(self):
        p = self.ready(); member = p['members'][0]
        self.action(member, 'approve')
        result = self.action(member, 'edit', changes={'id': 'research-lead'})
        self.assertEqual(result['members'][0]['status'], 'proposed')
        self.assertEqual(result['coverage'][0]['lead'], ['research-lead'])
        await self.b.add(p['id'])
        self.assertEqual(self.created, [])

    async def test_revision_preserves_other_consent_and_rejects_old_submission(self):
        p = self.ready()
        self.action(p['members'][1], 'approve')
        revised = self.action(p['members'][0], 'request_revision', feedback='Focus on accessible content')
        with self.assertRaises(ValueError):
            self.b.submit('astra', p['id'], p['revision'], self.payload())
        replacement = self.payload(('research-lead',))
        replacement['coverage'].append({'need': 'design', 'lead': ['designer'], 'support': []})
        self.b.submit('astra', p['id'], revised['revision'], replacement)
        result = self.b.submit('opus', p['id'], revised['revision'], replacement)
        self.assertEqual([m['status'] for m in result['members']], ['proposed', 'approved'])
        self.assertEqual(result['members'][0]['member_id'], p['members'][0]['member_id'])

    async def test_stage_role_stop_restart_and_stale_tokens(self):
        p = self.b.start({'brief': 'A project'})
        token = self.calls[-1][1]['routing']
        with self.assertRaises(ValueError): self.b.submit('opus', p['id'], 1, self.payload())
        self.b.interrupt('astra')
        self.assertFalse(self.b.accepts(token))
        self.assertEqual(self.b.get()['status'], 'failed')
        self.b.retry(p['id'])
        self.assertFalse(self.b.accepts(token))
        self.b.load()
        self.assertEqual(self.b.get()['status'], 'failed')

    async def test_imported_brief_edits_and_full_feedback_survive(self):
        p = self.b.start({'source': 'spec', 'brief': 'Edited saved spec'})
        self.assertEqual(p['brief'], 'Edited saved spec')
        self.b.submit('astra', p['id'], 1, self.payload())
        self.b.submit('opus', p['id'], 1, self.payload())
        self.action(self.b.get()['members'][0], 'request_revision', feedback='F' * 4000)
        self.assertLess(len(self.calls[-1][1]['prompt']), 4000)
        self.assertEqual(self.b.context()['proposal']['feedback'], 'F' * 4000)

    async def test_project_change_cannot_create_in_other_project(self):
        p = self.ready(); self.action(p['members'][0], 'approve')
        with self.assertRaises(ValueError):
            await self.r.change_profile(p['members'][0], creation_key='key', expected_project='different')
        self.assertEqual(self.created, [])

    async def test_a_team_for_an_imported_project_is_written_there_and_nobody_starts(self):
        # Import (task 1379953e): this room's agents draft for another project, which gets the approved members
        # in its own agents.json; nothing is activated here, and Add twice adds once.
        target = Path(self.tmp.name) / 'projects' / 'abcd1234'
        target.mkdir(parents=True)
        (target / 'coordination.json').write_text('{}', 'utf-8')
        (target / 'project.json').write_text('{"name": "Atlas Notes"}', 'utf-8')
        p = self.b.start({'source': 'text', 'brief': 'Imported notes app', 'target_project': 'abcd1234'})
        self.assertEqual((p['target_project'], p['target_name']), ('abcd1234', 'Atlas Notes'))
        self.assertIn('another project, Atlas Notes', self.calls[0][1]['prompt'])
        self.b.submit('astra', p['id'], p['revision'], self.payload())
        p = self.b.submit('opus', p['id'], p['revision'], self.payload())
        self.action(p['members'][0], 'approve')
        await self.b.add(p['id'])
        result = await self.b.add(p['id'])
        self.assertEqual([m['status'] for m in result['members']], ['added', 'proposed'])
        written = json.loads((target / 'agents.json').read_text('utf-8'))
        self.assertEqual(list(written), ['researcher'])
        self.assertEqual(self.created, [], 'Nothing is activated in this room')
        with self.assertRaises(ValueError):
            self.b.start({'source': 'text', 'brief': 'x', 'target_project': '../escape'})

    async def test_parallel_capacity_does_not_cap_roster_and_context_is_explicit(self):
        p = self.b.start({'brief': 'Project needing several specialists'})
        self.assertIn('it does not limit team size', self.calls[-1][1]['prompt'])
        handles = ('researcher', 'designer', 'editor')
        self.b.submit('astra', p['id'], 1, self.payload(handles))
        self.assertIn('it does not limit team size', self.calls[-1][1]['prompt'])
        result = self.b.submit('opus', p['id'], 1, self.payload(handles))
        self.assertEqual(self.b.context()['capacity'], 2)
        self.assertEqual(len(result['members']), 3)

    async def test_coverage_short_labels_optional_detail_and_rejected_paragraph(self):
        p = self.b.start({'brief': 'Project needing specialists'})
        payload = self.payload()
        payload['coverage'][0].update(need='N' * 60, detail='D' * 200)
        result = self.b.submit('astra', p['id'], 1, payload)
        self.assertEqual(result['coverage'][0]['detail'], 'D' * 200)
        self.assertNotIn('detail', result['coverage'][1])
        before = copy.deepcopy(self.b.proposal)
        for key, invalid in [('need', 'N' * 61), ('detail', 'D' * 201), ('detail', 123)]:
            bad = copy.deepcopy(payload)
            bad['coverage'][0][key] = invalid
            with self.assertRaises(ValueError): self.b.submit('opus', p['id'], 1, bad)
            self.assertEqual(self.b.proposal, before)
        result = self.b.submit('opus', p['id'], 1, payload)
        self.b.load()
        self.assertEqual(self.b.get()['coverage'], result['coverage'])

    async def test_http_contract_uses_saved_consent(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        host = SimpleNamespace(app=FastAPI())
        install(host, self.r)
        async with AsyncClient(transport=ASGITransport(app=host.app), base_url='http://test') as client:
            context = (await client.get('/api/runtime/team-builder')).json()
            self.assertEqual(context['capacity'], 2)
            self.assertEqual(len(context['agents']), 2)
            self.assertEqual((await client.post('/api/runtime/team-builder/proposals', json=[])).status_code, 400)
            p = self.ready()
            path = '/api/runtime/team-builder/proposals/' + p['id']
            await client.post(path + '/add', json={'approved': ['researcher']})
            self.assertEqual(self.created, [], 'Request body cannot forge approval')
            result = await client.post(path + '/members/' + p['members'][0]['member_id'], json={'action': 'approve'})
            self.assertEqual(result.status_code, 200)
            result = await client.post(path + '/add')
            self.assertEqual(result.json()['members'][0]['status'], 'added')
            self.assertEqual((await client.get(path)).json()['members'][0]['status'], 'added')


if __name__ == '__main__':
    unittest.main()
