"""room_motion actions that deliver results (task 78f77506). motion_studio.py holds the renderer and exporter
(systems, S3); this module only hands a finished render to people, so the two never edit the same file.

publish: a finished export becomes a room upload, posted to the agent's chat and optionally attached to a task,
where it plays inline (Zero #14291, PM decision deb4f237: videos arrive in chat and on tasks, not on a page).
"""
import asyncio
import os
import shutil
import uuid

from team_builder import text

MAX_VIDEO_BYTES = 1024 * 1024 * 1024  # an exported scene of up to 10 minutes stays far below this
# Remove with the v2 renderer (motion-design S5, task 45047b9a): until then only v1 scenes, and v2 scenes that
# are a migrated v1 scene, render (demo-marketing #14460).
NOT_RENDERABLE = ('Valid, but this install renders v1 scenes only (layout plus beats) until the v2 renderer ships; '
                  'frame and export will refuse this scene. It saves and keeps, and renders once the v2 renderer is '
                  'installed. For a video you need now, author it as v1.')


def render_note(result):
    """validate and save: say up front when a valid scene can't be rendered on this install."""
    summary = result.get('summary') if isinstance(result, dict) else None
    if isinstance(summary, dict) and summary.get('renderable') is False:
        result['warnings'] = [*result.get('warnings', []), NOT_RENDERABLE]
    return result


def share(source, target):
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


async def publish(runtime, run, arguments):
    exporter = runtime.motion
    job_id = str(arguments.get('job_id', ''))
    path = exporter.video(job_id)
    if not path:
        raise ValueError('No finished export with that job_id in this project; check export_status')
    if path.stat().st_size > MAX_VIDEO_BYTES:
        raise ValueError('The exported video is larger than 1 GB')
    job = next((j for j in exporter.finished() if j.get('id') == job_id), {})
    title = text(arguments.get('name') or f"{job.get('name') or job_id}.webm", 'Video name', 120)
    note = text(arguments.get('note') or '', 'Video note', 2000, required=False)
    room = runtime.project_room
    task_id = arguments.get('task_id')
    task = next((t for t in room.state['tasks'] if t['id'] == task_id), None) if task_id else None
    if task_id and not task:
        raise ValueError('Task was not found')  # before any file is written
    name = uuid.uuid4().hex[:8] + path.suffix.lower()
    target = runtime.host.store.upload_dir / name
    # A hard link when the export and uploads share a volume (no second copy of a large file), else a copy;
    # either way the upload outlives the export file, and the work runs off the event loop.
    await asyncio.to_thread(share, path, target)
    attachment = {'url': '/uploads/' + name, 'name': title, **({'note': note} if note else {})}
    result = {'url': attachment['url'], 'name': title, 'job_id': job_id}
    if task:
        try:
            # task() applies ownership, the card limit and history like any other attachment change.
            room.task(run.name, 'update', task_id=task_id, attachments=[*task.get('attachments', []), attachment])
        except ValueError:
            target.unlink(missing_ok=True)  # refused (for example, not the owner): leave no unreferenced copy
            raise
        result['task_id'] = task_id
    if arguments.get('post', True):
        message = runtime.host.store.add(run.name, note or f'Video: {title}', channel=run.channel,
                                         attachments=[dict(attachment)],
                                         metadata={'no_route': True, 'chain': run.entry['chain']})
        result['message_id'] = message['id']
    await runtime.activity_note(run, 'motion', f'Published video {attachment["url"]} from export {job_id}')
    return result
