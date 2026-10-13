"""Room release core: bounded reads and receipt-bound installation verification.

Staging uses the existing packer in a private owned worker. This slice cannot
queue a deployment. Verification jobs never confer feature acceptance.
"""
import asyncio
import copy
import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path

from release_interface import MUTATIONS, arguments
from release_pack import canonical, safe_relative

MAX_RECORD = 2_000_000
MAX_FILES = 4096
MAX_BYTES = 2 * 1024 ** 3
VERIFY_SECONDS = 120
ID = re.compile(r'[0-9a-f]{12}')
SHA = re.compile(r'[0-9a-f]{64}')
PACKAGE_KEYS = ('id', 'version', 'manifest_sha', 'commit')


def package_identity(record):
    return {key: record.get(key, '') for key in PACKAGE_KEYS}


class ReleaseManager:
    ID = ID
    def __init__(self, runtime, live_root):
        self.runtime = runtime
        self.state = runtime.deployment
        self.root = self.state.root.resolve()
        self.live_root = Path(live_root).resolve()
        # Loaded at app installation, never refreshed from an on-disk candidate.
        self.running = package_identity(self.state.version)
        self.jobs = {}
        self.active = None
        self.closing = False

    def _path(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('Release record escapes the deployment directory')
        return path

    def _bytes(self, relative):
        try:
            with self._path(relative).open('rb') as stream:
                raw = stream.read(MAX_RECORD + 1)
        except FileNotFoundError:
            return None
        if len(raw) > MAX_RECORD:
            raise ValueError('Release record exceeds its size limit')
        return raw

    def _record(self, relative, default=None):
        raw = self._bytes(relative)
        if raw is None:
            return default
        value = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(value, dict):
            raise ValueError('Release record must be an object')
        return value

    def _caller(self, caller):
        if not isinstance(caller, dict) or set(caller) != {'profile', 'project', 'run'}:
            raise ValueError('A managed room caller is required')
        rt = self.runtime
        if caller['profile'] != 'astra' or caller['project'] != rt.identity:
            raise ValueError('Release actions belong to the active release manager')
        run = next((run for run in rt.running.values() if run.id == caller['run']), None)
        if run is None or run.name != caller['profile']:
            raise ValueError('The managed release-manager run is no longer active')

    async def action(self, caller, action, supplied):
        values = arguments(action, supplied)
        self._caller(caller)
        if action == 'status':
            return self.status(values.get('id'))
        if self.closing or not self.state.admission_open():
            raise ValueError('Release operations wait until the room reopens')
        if action in MUTATIONS:
            self.state.release_grant.require(caller, self.runtime.profiles.items.get('astra'))
            if action == 'stage':
                from release_stage import prepare, revalidate
                context = prepare(self, caller, values)
                revalidate(self, context)
                return self.start_stage(caller, context)
            if action == 'rehearse':
                from release_rehearse import prepare
                return self.start_rehearse(caller, prepare(self, caller, values))
            raise ValueError('Release mutation core is not available in this build')
        if action == 'list':
            return self.list_releases()
        if action == 'verify':
            return self.start_verify(caller, values['deployment_id'])
        raise ValueError('Release action is not available')

    def list_releases(self):
        current = self._record('current.json', {})
        releases = []
        folders = sorted(self._path('releases').glob('*'))
        for folder in folders:
            if not ID.fullmatch(folder.name) or folder.is_symlink():
                continue
            item = self._record(f'releases/{folder.name}/RELEASE.json', {})
            if item.get('id') != folder.name:
                continue
            review = self._record(f'releases/{folder.name}/review.json', {})
            rehearsal = self._record(f'releases/{folder.name}/rehearsal.json', {})
            fingerprint = (item['id'], item.get('manifest_sha'))
            reviews = review.get('reviews', []) if (review.get('release_id'), review.get('manifest_sha')) == fingerprint else []
            records = {r.get('id'): r for r in self.runtime.project_room.state.get('reviews', [])}
            releases.append({**package_identity(item), 'current': fingerprint == (current.get('id'), current.get('manifest_sha')),
                'reviews': [{'id': identifier, 'status': records.get(identifier, {}).get('status', 'not_found')}
                            for identifier in reviews[:12] if isinstance(identifier, str)],
                'rehearsal_passed': (rehearsal.get('release_id'), rehearsal.get('manifest_sha'), rehearsal.get('status'))
                                    == (*fingerprint, 'passed')})
            if len(releases) == 40:
                break
        return {'current': package_identity(current), 'running': self.running, 'releases': releases,
                'limit': 40, 'integrity': 'not_checked', 'owner_acceptance': 'not_evaluated'}

    @staticmethod
    def _deployment(record, phase):
        request = record.get('request') or record
        return {'id': record.get('id'), 'phase': phase, 'release_id': record.get('release_id'),
                'manifest_sha': request.get('manifest_sha'), 'receipt_id': record.get('id') if phase in
                ('deployed', 'aborted', 'rejected', 'rolled_back', 'rollback_failed', 'conflict', 'recovery_required') else None}

    def status(self, identifier=None):
        if identifier in self.jobs:
            return self._job_answer(self.jobs[identifier])
        if identifier:
            receipt = self._record(f'receipts/{identifier}.json')
            if receipt:
                return self._deployment(receipt, receipt.get('status'))
            request = self._record(f'requests/{identifier}.json')
            if request:
                return self._deployment(request, 'queued')
        journal = self._record('journal.json', {})
        if journal and (not identifier or journal.get('id') == identifier):
            return self._deployment(journal, journal.get('phase'))
        if identifier:
            raise ValueError('Unknown deployment or verification job; jobs expire on app restart')
        gate = self.state.reload()
        supervisor = self._record('status.json', {})
        return {'current': package_identity(self._record('current.json', {})), 'running': self.running,
                'maintenance': {key: gate.get(key) for key in ('seq', 'admission', 'writes', 'deployment_id')},
                'supervisor': {key: supervisor.get(key) for key in ('state', 'deployment_id', 'updated')},
                'active_runs': len(self.runtime.running), 'active_mutations': self.state.mutations,
                'jobs': [self._job_answer(job) for job in list(self.jobs.values())[-10:]]}

    @staticmethod
    def _job_answer(job):
        return {key: copy.deepcopy(job[key]) for key in ('id', 'action', 'deployment_id', 'state', 'deadline', 'result', 'error')
                if key in job}

    def start_verify(self, caller, deployment_id):
        if self.active is not None:
            raise ValueError('A release verification is already running')
        receipt = self._record(f'receipts/{deployment_id}.json')
        if not receipt or receipt.get('id') != deployment_id:
            raise ValueError('A terminal deployment receipt is required')
        if receipt.get('status') != 'deployed':
            raise ValueError('This receipt does not identify a successfully deployed candidate')
        identifier = uuid.uuid4().hex
        job = {'id': identifier, 'action': 'verify', 'deployment_id': deployment_id, 'state': 'running',
               'deadline': time.time() + VERIFY_SECONDS, 'project': caller['project'], 'stop': threading.Event()}
        self._launch(job, lambda: self._verify_job(job, copy.deepcopy(receipt)))
        return self._job_answer(job)

    def _launch(self, job, factory):
        if self.active is not None or self.closing:
            raise ValueError('A release job is running or the manager is closing')
        if not self.state.enter_mutation():
            raise ValueError('The deployment write gate is closed')
        job['counted'] = True
        self.active = job
        try:
            self.jobs[job['id']] = job
            while len(self.jobs) > 32:
                del self.jobs[next(iter(self.jobs))]
            job['task'] = asyncio.create_task(factory())
            # Also covers cancellation before the coroutine's first instruction.
            job['task'].add_done_callback(lambda task: self._release_job(job))
        except BaseException:
            self._release_job(job)
            raise

    def _release_job(self, job):
        if job.get('counted'):
            job['counted'] = False
            self.state.leave_mutation()
        if self.active is job:
            self.active = None
        if job['state'] == 'running' and job.get('task') and job['task'].cancelled():
            job.update(state='cancelled', error='Release job cancelled before startup')

    def start_stage(self, caller, context):
        from release_stage import STAGE_SECONDS
        job = {'id': uuid.uuid4().hex, 'action': 'stage', 'state': 'running',
               'deadline': time.time() + STAGE_SECONDS, 'project': caller['project'], 'stop': threading.Event()}
        self._launch(job, lambda: self._stage_job(job, context))
        return self._job_answer(job)

    async def _stage_job(self, job, context):
        from release_stage import stage
        try:
            result = await stage(self, job, context)
            job.update(state='complete', result=result)
        except asyncio.CancelledError:
            job['stop'].set()
            job.update(state='cancelled', error='Staging cancelled')
        except Exception:
            job.update(state='failed', error='Staging refused or failed; no deployment was requested')
        finally:
            self._release_job(job)

    def start_rehearse(self, caller, context):
        from release_rehearse import REHEARSE_SECONDS
        job = {'id': uuid.uuid4().hex, 'action': 'rehearse', 'state': 'running',
               'deadline': time.time() + REHEARSE_SECONDS, 'project': caller['project'], 'stop': threading.Event()}
        self._launch(job, lambda: self._rehearse_job(job, context))
        return self._job_answer(job)

    async def _rehearse_job(self, job, context):
        from release_rehearse import rehearse
        try:
            job.update(state='complete', result=await rehearse(self, job, context))
        except asyncio.CancelledError:
            job['stop'].set()
            job.update(state='cancelled', error='Rehearsal cancelled')
        except Exception:
            job.update(state='failed', error='Rehearsal refused or failed; no deployment was requested')
        finally:
            self._release_job(job)

    async def _verify_job(self, job, receipt):
        worker = asyncio.create_task(asyncio.to_thread(self._verify, job, receipt))
        try:
            result = await asyncio.shield(worker)
            if job['project'] != self.runtime.identity:
                raise ValueError('Project changed during verification')
            job.update(state='complete', result=result)
        except asyncio.CancelledError:
            job['stop'].set()
            # Keep the mutation counted until the thread has really stopped.
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if worker.done() and not worker.cancelled():
                worker.exception()
            job.update(state='cancelled', error='Verification cancelled')
        except Exception:
            # Do not expose host paths or arbitrary exception text through MCP.
            job.update(state='failed', error='Release verification failed; no acceptance was granted')
        finally:
            self._release_job(job)

    def _verify(self, job, receipt):
        deadline = time.monotonic() + VERIFY_SECONDS
        def check():
            if job['stop'].is_set() or time.monotonic() >= deadline:
                raise TimeoutError('Verification stopped')
        check()
        request = receipt.get('request') or {}
        identifier, expected = receipt.get('release_id'), request.get('manifest_sha')
        if not isinstance(identifier, str) or not ID.fullmatch(identifier) or not isinstance(expected, str) or not SHA.fullmatch(expected):
            raise ValueError('Receipt lacks an exact candidate fingerprint')
        manifest = self._record(f'releases/{identifier}/manifest.json')
        release = self._record(f'releases/{identifier}/RELEASE.json')
        current = self._record('current.json')
        raw = self._bytes(f'releases/{identifier}/manifest.json')
        if not manifest or raw != canonical(manifest) or hashlib.sha256(raw).hexdigest() != expected or expected[:12] != identifier:
            raise ValueError('Staged manifest differs from the receipt')
        if not release or (release.get('id'), release.get('manifest_sha'), release.get('commit')) != (identifier, expected, manifest.get('commit')):
            raise ValueError('Staging receipt differs from the manifest')
        if release.get('files') != manifest.get('files') or release.get('deletions') != manifest.get('deletions'):
            raise ValueError('Staging inventory differs from the manifest')
        files = manifest.get('files')
        deletions = manifest.get('deletions')
        if not isinstance(files, list) or not isinstance(deletions, list) or len(files) + len(deletions) > MAX_FILES:
            raise ValueError('Release inventory exceeds its bounds')
        matching = current == release and self.running == package_identity(release)
        failures, names, total = [], set(), 0
        for item in files:
            check()
            name, expected_hash = item['path'], item['sha256']
            relative = safe_relative(name)
            if str(relative) != name or name.casefold() in names or not SHA.fullmatch(expected_hash):
                raise ValueError('Invalid inventory entry')
            names.add(name.casefold())
            target = (self.live_root / relative).resolve()
            if not target.is_relative_to(self.live_root):
                raise ValueError('Installed path escapes the room')
            digest = hashlib.sha256()
            try:
                with target.open('rb') as stream:
                    while data := stream.read(1024 * 1024):
                        check()
                        total += len(data)
                        if total > MAX_BYTES:
                            raise ValueError('Verification byte limit exceeded')
                        digest.update(data)
                valid = digest.hexdigest() == expected_hash
            except FileNotFoundError:
                valid = False
            if not valid:
                failures.append(name)
        for name in deletions:
            check()
            target = (self.live_root / safe_relative(name)).resolve()
            if not target.is_relative_to(self.live_root):
                raise ValueError('Deleted path escapes the room')
            if target.exists():
                failures.append(name)
        check()
        # A manual/external installation changing current.json invalidates this observation.
        matching = matching and self._record('current.json') == current
        return {'receipt_id': receipt['id'], 'release_id': identifier, 'manifest_sha': expected,
                'integrity': 'passed' if matching and not failures else 'failed',
                'identity_matches': matching, 'checked_files': len(files), 'mismatch_count': len(failures),
                'mismatches': failures[:40], 'owner_acceptance': 'not_evaluated'}

    async def close(self):
        self.closing = True
        job = self.active
        if job is not None:
            job['stop'].set()
            job['task'].cancel()
            while not job['task'].done():
                try:
                    await asyncio.shield(job['task'])
                except asyncio.CancelledError:
                    continue
            self._release_job(job)
