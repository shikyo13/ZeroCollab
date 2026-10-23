"""Standalone media authority. Inputs and artifacts are files, never room objects."""
import asyncio
import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

from . import studio, scene as scene_format, encode, analysis
from .jobs import MediaJobs, Cancelled


@contextmanager
def asset_registry_lock(directory):
    """Serialize the registry read/replace across independent CLI/MCP processes."""
    with (directory / '.assets.lock').open('a+b') as lock:
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


class Workspace:
    def __init__(self, directory, *, ffmpeg=None, asset_dir=None):
        self.directory = Path(directory).expanduser().resolve()
        self.data = self.directory
        self.identity = hashlib.sha256(str(self.directory).encode()).hexdigest()[:16]
        self.cfg = {'motion': {'ffmpeg': ffmpeg}} if ffmpeg else {}
        self.asset_dir = Path(asset_dir).resolve() if asset_dir else self.directory / 'assets'

    def asset_path(self, key):
        manifest = self.directory / 'assets.json'
        rows = json.loads(manifest.read_text('utf-8')) if manifest.is_file() else {}
        entry = rows.get(key)
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
            raise ValueError('Unknown asset reference')
        path = Path(entry['path']).resolve()
        if not path.is_file():
            raise ValueError('Registered asset no longer exists')
        return path

    def load(self, reference):
        if isinstance(reference, dict):
            value = copy.deepcopy(reference)
        elif str(reference).startswith(('bundled:', 'project:')):
            found, _ = studio.catalogue(self)
            if reference not in found:
                raise ValueError('Unknown scene: ' + str(reference))
            value = copy.deepcopy(found[reference][1])
        else:
            path = Path(reference).expanduser().resolve()
            if path.stat().st_size > studio.MAX_SCENE_BYTES:
                raise ValueError('Scene exceeds the 256 KiB limit')
            value = json.loads(path.read_text('utf-8'))
        issues = scene_format.issues(value)
        if issues:
            raise ValueError(f"{issues[0]['path']}: {issues[0]['message']}")
        return value


class Engine:
    """One queue and job owner per CLI/MCP instance, with joined cancellation."""
    def __init__(self, workspace, *, ffmpeg=None, asset_dir=None):
        self.workspace = Workspace(workspace, ffmpeg=ffmpeg, asset_dir=asset_dir)
        self.jobs = MediaJobs()
        self.tasks = {}

    def start(self, kind, operation):
        job = {'kind': kind, 'owner': 'local', 'project': self.workspace.identity}
        ticket = self.jobs.submit(job)

        async def owned():
            lease = None
            try:
                lease = await self.jobs.wait(ticket)
                ticket.check_cancel()
                result = await operation(job, ticket.check_cancel)
                ticket.check_cancel()
                self.jobs.finish(lease, phase='done', result=result)
            except (Cancelled, asyncio.CancelledError):
                if lease is not None:
                    self.jobs.finish(lease, phase='cancelled')
                raise
            except Exception as exc:
                if lease is not None:
                    self.jobs.finish(lease, phase='failed', error=str(exc))
                raise

        task = asyncio.create_task(owned())
        self.jobs.bind(ticket, task)
        self.tasks[job['id']] = task
        # Keep exceptions observable via status/wait without orphaned-task warnings.
        def settled(task):
            self.tasks.pop(job['id'], None)
            if not task.cancelled():
                task.exception()
        task.add_done_callback(settled)
        return self.status(job['id'])

    def status(self, job_id):
        return self.jobs.status(job_id, project=self.workspace.identity)

    async def wait(self, job):
        task = self.tasks.get(job['id'])
        if task is not None:
            await task
        state = self.status(job['id'])
        if state['phase'] == 'failed':
            raise ValueError(state.get('error', 'Media job failed'))
        if state['phase'] == 'cancelled':
            raise Cancelled('Media job was cancelled')
        return state

    def cancel(self, job_id, generation):
        self.jobs.cancel(job_id, generation, project=self.workspace.identity, actor='local')
        return self.status(job_id)

    async def close(self):
        self.jobs.begin_close()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        await self.jobs.close()
        cache = getattr(self.workspace, '_motion_voice_cache', None)
        if cache is not None and hasattr(cache, 'close'):
            await cache.close()

    def outputs(self, job, output=None):
        root = Path(output).expanduser().resolve() if output else self.workspace.directory / 'outputs'
        folder = root / job['id']
        folder.mkdir(parents=True, exist_ok=False)
        return folder

    def frame(self, reference, times=None, *, output=None, reduced=False, composition=None):
        value = self.workspace.load(reference)
        if composition:
            value = self.select(value, composition)
        moments = [0] if times is None else times
        if not 1 <= len(moments) <= 6 or any(type(t) not in (int, float) or not 0 <= t <= 600000 for t in moments):
            raise ValueError('Choose one to six frame times in milliseconds')

        async def operation(job, check):
            folder = self.outputs(job, output)
            committed = False
            try:
                pngs = await studio.render_frames(self.workspace, value, moments, reduced, check_cancel=check, job=job)
                paths = []
                for index, png in enumerate(pngs):
                    check()
                    path = folder / f'frame-{index:02}.png'
                    path.write_bytes(png)
                    paths.append(str(path))
                committed = True
                return {'files': paths, 'times_ms': moments}
            finally:
                if not committed:
                    shutil.rmtree(folder)

        return self.start('frame', operation)

    def preview(self, reference, *, output=None, reduced=False, composition=None):
        value = self.workspace.load(reference)
        async def operation(job, check):
            html = await asyncio.to_thread(studio.view_html, value,
                {'reduced': reduced, **({'composition': composition} if composition else {})}, self.workspace)
            check()
            destination = self.outputs(job, output) / 'preview.html'
            destination.write_text(html, 'utf-8')
            return {'path': str(destination), 'mime': 'text/html'}
        return self.start('frame', operation)

    def record_encode(self, request):
        from .recording import encode_take
        async def operation(job, check):
            return await encode_take(request, self.workspace, check_cancel=check)
        return self.start('export', operation)

    @staticmethod
    def select(value, composition):
        if value['version'] != 2:
            raise ValueError('Composition selection requires a v2 scene')
        value = copy.deepcopy(value)
        selected = next((c for c in value['compositions'] if c['id'] == composition), None)
        if selected is None:
            raise ValueError('Unknown composition')
        value['compositions'] = [selected]
        return value

    def export(self, reference, *, output=None, profile='webm', reduced=False, composition=None):
        value = self.workspace.load(reference)
        if composition:
            value = self.select(value, composition)
        if profile not in encode.PROFILES:
            raise ValueError('Unknown export profile')

        async def operation(job, check):
            facts = await asyncio.to_thread(studio.require_tools, self.workspace, profile)
            check()
            plan = studio.render_plan(value)
            selected = encode.PROFILES[profile]
            folder = self.outputs(job, output)
            destination = folder / ('clip.' + selected.extension)
            work = Path(tempfile.mkdtemp(prefix='work-', dir=folder))
            job.update(**plan, frames=plan['duration'], frame=0, scene_id=value['id'],
                       profile=profile, format=profile, reduced=reduced, transparent=selected.alpha)
            committed = False
            try:
                hashes = await studio.export_video(value, reduced, destination, work, job, facts['path'],
                                                    check_cancel=check, runtime=self.workspace)
                check()
                result = {'path': str(destination), 'mime': selected.mime, 'bytes': destination.stat().st_size,
                          'frames': job['frames'], 'frames_digest': hashlib.sha256(''.join(hashes).encode()).hexdigest()}
                committed = True
                return result
            finally:
                await studio.remove_tree(work)
                if not committed:
                    shutil.rmtree(folder)

        return self.start('export', operation)

    def inspect(self, path):
        path = Path(path).expanduser().resolve()
        if not path.is_file():
            raise ValueError('Media file does not exist')

        async def operation(job, check):
            binary = studio.ffmpeg_path(self.workspace)
            stop = self.jobs.entries[job['id']].stop
            return await asyncio.to_thread(analysis.analyze, binary, path, stop=stop)

        return self.start('inspect', operation)

    def import_asset(self, path, asset_id, kind='image'):
        if not scene_format.ID.fullmatch(asset_id):
            raise ValueError('Asset id must use lowercase letters, digits and hyphens')
        path = Path(path).expanduser().resolve(strict=True)
        if not path.is_file() or path.stat().st_size > 1024**3:
            raise ValueError('Asset must be a regular file no larger than 1 GiB')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        key = hashlib.sha256(str(path).encode()).hexdigest()[:16]
        directory = self.workspace.directory
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / 'assets.json'
        with asset_registry_lock(directory):
            rows = json.loads(target.read_text('utf-8')) if target.exists() else {}
            rows[key] = {'path': str(path), 'sha256': digest}
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps(rows, indent=2), 'utf-8')
            os.replace(temporary, target)
        return {'id': asset_id, 'kind': kind, 'ref': 'fileref:' + key, 'sha256': digest}
