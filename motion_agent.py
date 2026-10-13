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
# A v2 scene renders when the room has the S5 renderer files (motion_studio.summary, V2_SCRIPTS); a copy of
# the room without them still says so up front instead of only at frame/export (demo-marketing #14460).
NOT_RENDERABLE = ('Valid, but this copy of the room is missing the v2 renderer files, so frame and export will refuse '
                  'this scene. It saves and keeps, and renders once they are installed.')


def render_note(result, scene=None):
    """validate and save: say up front what export will refuse for a valid scene, so the agent fixes it before
    spending a render (harness 0216d3b8): a missing v2 renderer, and sound, which export can't mix yet."""
    import motion_studio
    summary = result.get('summary') if isinstance(result, dict) else None
    if not isinstance(summary, dict):
        return result
    warnings = list(result.get('warnings', []))
    if summary.get('renderable') is False:
        warnings.append(NOT_RENDERABLE)
    if isinstance(scene, dict) and scene.get('version') == 2:
        for comp in scene.get('compositions', []):
            try:
                motion_studio.require_silent_composition(scene, comp['id'])  # the rule export applies
            except ValueError:
                warnings.append(f"Composition {comp['id']} has sound (an audio layer, or a video layer that isn't "
                                'muted). Export refuses it until audio mixing ships: set "muted": true on video '
                                'layers and remove audio layers before export. frame previews work as they are.')
    if warnings:
        result['warnings'] = warnings
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
    job = next((j for j in exporter.finished() if j.get('id') == job_id), {})
    # Only WebM plays inline in chat and on tasks (systems b1883cae). video() found the file from the job's
    # profile, so its suffix is the container; exports from before profiles are WebM.
    if path.suffix.lower() != '.webm':
        raise ValueError(f"This {job.get('profile') or path.suffix.lstrip('.')} export downloads from its link; "
                         f"publish posts WebM videos. Share /api/runtime/motion/exports/{job_id}/video?download=1")
    if path.stat().st_size > MAX_VIDEO_BYTES:
        raise ValueError('The exported video is larger than 1 GB')
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
