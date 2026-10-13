"""Deployments only offer newer releases; downgrades need an explicit rollback; reviews come from the board."""
import hashlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import room_deploy


def stage(spool, version, created, rehearsed=True):
    manifest = {'commit': version.replace('.', '') * 8, 'files': [{'path': 'VERSION', 'sha256': version}], 'deletions': []}
    raw = json.dumps(manifest).encode()
    sha = hashlib.sha256(raw).hexdigest()
    folder = spool / 'releases' / sha[:12]
    folder.mkdir(parents=True)
    (folder / 'manifest.json').write_bytes(raw)
    release = {'id': sha[:12], 'manifest_sha': sha, 'version': version, 'created': created, **manifest}
    (folder / 'RELEASE.json').write_text(json.dumps(release), 'utf-8')
    if rehearsed:
        (folder / 'rehearsal.json').write_text(json.dumps({'release_id': sha[:12], 'manifest_sha': sha, 'status': 'passed'}), 'utf-8')
    return release


class DeployCandidateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        data = Path(self.tmp.name)
        spool = data / 'deploy'
        self.old = stage(spool, '0.6.0', '2026-10-06 21:27:04')
        self.current = stage(spool, '0.7.0', '2026-10-06 22:02:24')
        # Built earlier than 0.7.0 but a higher version: version decides, not build time.
        self.new = stage(spool, '0.7.1', '2026-10-06 20:00:00')
        (spool / 'current.json').write_text(json.dumps(self.current), 'utf-8')
        (spool / 'status.json').write_text(json.dumps({'state': 'running', 'updated': time.time() + 600}), 'utf-8')
        (spool / 'releases' / self.new['id'] / 'review.json').write_text(json.dumps(
            {'release_id': self.new['id'], 'manifest_sha': self.new['manifest_sha'], 'reviews': ['r1', 'gone']}), 'utf-8')
        # A review file copied from another package must not attach to this one.
        (spool / 'releases' / self.old['id'] / 'review.json').write_text(json.dumps(
            {'release_id': self.new['id'], 'manifest_sha': self.new['manifest_sha'], 'reviews': ['r1']}), 'utf-8')
        state = {'reviews': [{'id': 'r1', 'status': 'approved', 'reviewer': 'astra', 'revision': 'abc123 diff', 'task_id': 't1'}],
                 'tasks': [{'id': 't1', 'title': 'Deploy fix'}]}
        host = SimpleNamespace(app=FastAPI())
        runtime = SimpleNamespace(data=data, project_room=SimpleNamespace(state=state), running={}, states={})
        room_deploy.install(host, runtime)
        self.state = host.deployment
        self.spool = spool
        self.client = AsyncClient(transport=ASGITransport(app=host.app), base_url='http://test')
        self.addAsyncCleanup(self.client.aclose)

    async def approve(self, release, **extra):
        return await self.client.post('/api/runtime/deployments/approve', json={
            'release_id': release['id'], 'manifest_sha': release['manifest_sha'], 'interrupt': False, 'drain_seconds': 600, **extra})

    async def test_relations_order_and_board_reviews(self):
        releases = (await self.client.get('/api/runtime/deployments')).json()['releases']
        self.assertEqual([(r['version'], r['relation']) for r in releases],
                         [('0.7.1', 'newer'), ('0.7.0', 'current'), ('0.6.0', 'older')])
        self.assertEqual(releases[0]['reviews'][0], {'id': 'r1', 'status': 'approved', 'reviewer': 'astra',
                                                         'revision': 'abc123 diff', 'task_id': 't1', 'task_title': 'Deploy fix'})
        self.assertEqual(releases[0]['reviews'][1], {'id': 'gone', 'status': 'not_found'})
        self.assertEqual(releases[2]['reviews'], [])

    async def test_downgrade_needs_explicit_rollback(self):
        refused = await self.approve(self.old)
        self.assertEqual(refused.status_code, 409)
        self.assertIn('older than the running release', refused.json()['error'])
        self.assertEqual((await self.approve(self.current, rollback=True)).status_code, 409)
        self.assertFalse(list((self.spool / 'requests').glob('*.json')))
        record = (await self.approve(self.old, rollback=True)).json()
        self.assertTrue(record['rollback'])

    async def test_newer_release_approves_normally(self):
        record = (await self.approve(self.new)).json()
        self.assertEqual(record['release_id'], self.new['id'])
        self.assertFalse(record['rollback'])
        self.assertIs(record['interrupt'], False)

    async def test_install_now_interrupts_only_from_the_users_route(self):
        # 9f3eca50, security 8401e355 R1: an explicit opt-in on the room's route; a literal bool only.
        body = {'release_id': self.new['id'], 'manifest_sha': self.new['manifest_sha'], 'interrupt': True, 'drain_seconds': 30}
        with self.assertRaisesRegex(ValueError, 'only Install now'):
            room_deploy.approve_release(self.state, body)  # the tray's call: no opt-in
        with self.assertRaisesRegex(ValueError, 'only Install now'):
            room_deploy.approve_release(self.state, body, allow_offline=True, allow_interrupt=True)
        refused = await self.approve(self.new, interrupt='yes')
        self.assertIn('true or false', refused.json()['error'])
        self.assertFalse(list((self.spool / 'requests').glob('*.json')))
        record = (await self.approve(self.new, interrupt=True, drain_seconds=30)).json()
        self.assertIs(record['interrupt'], True)
        self.assertEqual(record['drain_seconds'], 30)

    async def test_an_agent_install_always_drains_briefly_then_interrupts(self):
        # opus 7cdd51bd: no agent holds up an install; server policy, whatever the agent path's body says.
        approval = {'approved_by': 'release-manager:astra', 'project_id': 'p', 'profile_id': 'astra', 'run_id': 'r',
                    'protocol': 1, 'grant_revision': 1, 'grant_decision': 'user-enabled', 'expected_manifest_sha': 'x',
                    'stop_generation': 0, 'idempotency_key': 'k', 'acceptance_receipt': 'a'}
        body = {'release_id': self.new['id'], 'manifest_sha': self.new['manifest_sha'], 'rollback': False, 'interrupt': False}
        with self.assertRaisesRegex(ValueError, 'only Install now'):
            room_deploy._approve_release_locked(self.state, {**body, 'interrupt': True}, approval=approval)
        record = room_deploy._approve_release_locked(self.state, body, approval=approval)
        self.assertIs(record['interrupt'], True)
        self.assertEqual(record['drain_seconds'], room_deploy.AGENT_DRAIN_SECONDS)


if __name__ == '__main__':
    unittest.main()
