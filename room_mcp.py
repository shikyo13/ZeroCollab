"""Run-scoped local coordination tools for official native CLIs."""
import os
import base64
import json
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP, Image

# room_image returns pixels on demand; large files would crowd the model's context.
IMAGE_LIMIT = 5 * 1024 * 1024
IMAGE_FORMATS = {'png': 'png', 'jpg': 'jpeg', 'jpeg': 'jpeg', 'gif': 'gif', 'webp': 'webp'}

class RoomMCP(FastMCP):
    async def list_tools(self):
        tools = await super().list_tools()
        # Tell the room this turn's provider really loaded the tools; a turn that never lists them
        # is reported as a capability problem instead of failing silently. Best effort only.
        try:
            with httpx.Client(timeout=3, trust_env=False) as client:
                client.post(os.environ['AGENT_ROOM_URL'] + '/api/runtime/coordination/' + os.environ['AGENT_ROOM_RUN_ID'],
                            headers={'X-Session-Token': os.environ['AGENT_ROOM_SESSION_TOKEN']},
                            json={'tool': 'tools_listed', 'arguments': {'count': len(tools)}})
        except (httpx.HTTPError, KeyError):
            pass
        return tools


mcp = RoomMCP('project_room', instructions='These tools operate the same durable project and kanban board visible to the user. Use room_task for ownership/dependencies, room_review for peer review, room_followup for tracked next actions, and room_read(section="inbox") at checkpoints. room_update leaves progress without waking peers. Native tools remain available; your final answer is published automatically.')


def call(tool, arguments):
    with httpx.Client(timeout=15, trust_env=False) as client:
        response = client.post(os.environ['AGENT_ROOM_URL'] + '/api/runtime/coordination/' + os.environ['AGENT_ROOM_RUN_ID'],
            headers={'X-Session-Token': os.environ['AGENT_ROOM_SESSION_TOKEN']}, json={'tool': tool, 'arguments': arguments})
    if response.status_code != 200:
        return {'error': response.json().get('error', 'Room request failed')}
    return response.json()


@mcp.tool(annotations={'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False})
def room_read(section: str = 'overview', offset: int = 0, limit: int = 4000, query: str = '', after_id: int = 0,
              task_id: str = '', owner: str = '', status: str = '') -> dict:
    """Read overview, spec, tasks, memory, messages (including attachments), patches, participants, inbox, reviews, followups or review_artifact (query "<review-id>:<path>"). Filter tasks/reviews/followups by task_id, owner or status. offset/limit count characters; follow next_offset for more. Check your inbox after meaningful work batches."""
    return call('room_read', dict(section=section, offset=offset, limit=limit, query=query, after_id=after_id,
                                 task_id=task_id, owner=owner, status=status))


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False})
def room_task(action: str, task_id: str = '', title: str = '', details: str = '', paths: list[str] | None = None,
              owner: str = '', status: str = '', update: str = '', depends_on: list[str] | None = None,
              labels: list[str] | None = None, attachments: list[dict] | None = None, next_action: str | None = None,
              priority: int | None = None, wait: dict | None = None, evidence: dict | None = None) -> dict:
    """Get, create, claim, update, delegate, accept or release a kanban task. get returns the full record: history, details, attachments, reviews/followups and execution_state (executing, queued, waiting_on_human, needs_attention, blocked, waiting_on_dependency, in_review, idle). Other actions return a compact receipt (state, next_action, counts). delegate proposes a transfer; the current owner keeps the task until the recipient calls accept. next_action records the next executable step; priority is 1 (highest) to 5. wait {kind: followup|review, record_id} links an open request to the user on this task while you wait for them ({} clears it; a next_action is required). evidence records implementation, review, installation and acceptance separately. Claim relative paths before editing; overlapping scopes and dependency cycles are refused. labels tag the task's area (e.g. frontend, backend, docs; up to 4) and group the user's board. attachments replaces the task's image list: [{url: '/uploads/<file>', name, note}] (up to 8; a note describes an annotation). Ownership and status changes are recorded in task history automatically. Agents must resolve requested reviews and dependencies before marking done; the user can override."""
    return call('room_task', dict(action=action, task_id=task_id, title=title, details=details,
                                paths=paths, owner=owner, status=status, update=update, depends_on=depends_on,
                                labels=labels, attachments=attachments, next_action=next_action, priority=priority,
                                wait=wait, evidence=evidence))


@mcp.tool(annotations={'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False})
def room_image(url: str) -> Image:
    """View one image the room shares: a task attachment or chat attachment url (/uploads/<file> or its full http form). Fetch only images you need; they are not included in your prompt."""
    name = url.split('/uploads/', 1)[1] if '/uploads/' in url else ''
    if not name or not all(c.isascii() and (c.isalnum() or c in '._-') for c in name) or name.startswith('.'):
        raise ValueError('Pass an uploaded image url such as /uploads/1a2b3c4d.png')
    extension = name.rsplit('.', 1)[-1].lower()
    if extension not in IMAGE_FORMATS:
        raise ValueError(f'Unsupported image type .{extension}')
    with httpx.Client(timeout=15, trust_env=False) as client:
        response = client.get(os.environ['AGENT_ROOM_URL'] + '/uploads/' + name)
    if response.status_code != 200:
        raise ValueError('Image not found')
    if len(response.content) > IMAGE_LIMIT:
        raise ValueError(f'Image is larger than {IMAGE_LIMIT // 1024 // 1024} MB; ask for a smaller copy')
    return Image(data=response.content, format=IMAGE_FORMATS[extension])


MAGIC = {'png': (b'\x89PNG',), 'jpeg': (b'\xff\xd8\xff',), 'gif': (b'GIF87a', b'GIF89a'), 'webp': (b'RIFF',)}


def upload_screenshot(path):
    # Only raster images leave the machine-local path; anything else is refused before reading more than the header.
    path = Path(path).expanduser()
    extension = path.suffix.lower().lstrip('.')
    if extension not in IMAGE_FORMATS or not path.is_file():
        raise ValueError('Pass an existing PNG, JPEG, GIF or WebP file path, or an /uploads/ url')
    if path.stat().st_size > IMAGE_LIMIT:
        raise ValueError(f'Screenshot is larger than {IMAGE_LIMIT // 1024 // 1024} MB; capture a smaller window or region')
    data = path.read_bytes()
    kind = IMAGE_FORMATS[extension]
    if not data.startswith(MAGIC[kind]) or (kind == 'webp' and data[8:12] != b'WEBP'):
        raise ValueError(f'{path.name} is not a valid {kind.upper()} image')
    with httpx.Client(timeout=30, trust_env=False) as client:
        response = client.post(os.environ['AGENT_ROOM_URL'] + '/api/upload', headers={'X-Session-Token': os.environ['AGENT_ROOM_SESSION_TOKEN']},
                               files={'file': (path.name, data, 'image/' + kind)})
    if response.status_code != 200:
        raise ValueError(response.json().get('error', 'Upload failed'))
    return response.json()['url']


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False})
def room_markup(image: str, marks: list[dict] | None = None, summary: str = '', task_id: str = '', post: bool = True) -> dict:
    """Show instead of describe: share a screenshot or uploaded image with the team, optionally marked up. image is a local PNG/JPEG/GIF/WebP path (e.g. a screenshot you captured) or an /uploads/ url. marks: [{kind: box|arrow|pin, x1, y1, x2, y2, note, color}] with x/y as fractions 0..1 of width/height (a pin uses x1, y1), up to 20; they are drawn as numbered marks onto a new PNG whose note lists each one. Posts to this channel unless post is false; task_id adds it to a task you own. Returns the attachment url for reviews and messages."""
    try:
        url = image if '/uploads/' in image else upload_screenshot(image)
    except ValueError as exc:
        return {'error': str(exc)}
    return call('room_markup', dict(image=url, marks=marks or [], summary=summary, task_id=task_id, post=post))


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False})
def room_update(kind: str, text: str, supersedes: str = '', resolves: str = '') -> dict:
    """Record concise progress, decision, reference, blocker or handoff information without waking peers. Use reference for shared project facts teammates should reuse: caches, helper scripts, data paths. A decision or reference can retire an active earlier one of the same kind by id: supersedes (replaced by this one) or resolves (no longer applies). Only current decisions and references appear in turn context."""
    return call('room_update', {'kind': kind, 'text': text, 'supersedes': supersedes, 'resolves': resolves})


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False})
def room_review(action: str, task_id: str = '', review_id: str = '', reviewer: str = '',
                revision: str = '', text: str = '', verdict: str = '', notify: bool = True,
                files: list[str] | None = None) -> dict:
    """Request peer review, respond with findings, or cancel with a reason. request needs task_id, reviewer, revision (an explicit artifact reference such as a commit/diff digest or proposal message ID), and text describing scope/questions. respond needs review_id, exact revision inspected, text with concrete evidence, and verdict approved or changes_requested. cancel needs review_id and text explaining why. New references supersede that peer's older review. Revision references are caller-supplied; use a new reference after material edits. On request the room also hashes the task owner's files (files: relative paths, default the task's file paths) and keeps a copy; approval is refused if they changed since, and room_read(section="review_artifact", query="<review-id>:<path>") returns the captured copy. notify attempts one bounded wake-up; requests persist in the inbox even if a peer cannot start. A respond always notifies the owner, since they are waiting on the verdict."""
    return call('room_review', dict(action=action, task_id=task_id, review_id=review_id, reviewer=reviewer,
                                   revision=revision, text=text, verdict=verdict, notify=notify, files=files))


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False})
def room_followup(action: str, followup_id: str = '', task_id: str = '', assigned_to: str = '',
                  text: str = '', result: str = '', notify: bool = True) -> dict:
    """Create a durable next action/question for a participant, optionally linked to a task. Resolve with a concrete result, or cancel with a reason. Open items remain in the assignee's inbox. notify attempts one bounded wake-up; no polling, automatic reminders or agent spawning. A resolve always notifies the other party, since they are waiting on the result."""
    return call('room_followup', dict(action=action, followup_id=followup_id, task_id=task_id,
                                     assigned_to=assigned_to, text=text, result=result, notify=notify))


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False})
def room_repository(path: str, reason: str, create: bool = False, action: str = 'replace') -> dict:
    """Ask the user to add a repository (action add) or replace the primary (action replace). Use an existing absolute repository root, or create true for a new folder. Only the user approves. Running turns can finish and queued work is kept. Task/review paths may use key:relative/path; bare task paths are pinned to the primary when claimed."""
    return call('room_repository', dict(path=path, create=create, reason=reason, action=action))


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False})
def room_goal(action: str = 'status', goal_id: str = '', evidence: str = '') -> dict:
    """Read the shared room goal (status), or as its named lead finish it: complete or block with goal_id and evidence. The user starts, pauses and cancels goals."""
    return call('room_goal', dict(action=action, goal_id=goal_id, evidence=evidence))


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False})
def room_team_builder(action: str = 'get', proposal_id: str = '', revision: int = 0,
                      payload: dict | None = None) -> dict:
    """Read the current team proposal/context (get) or submit an assigned draft/challenge (submit).
    Submit the exact proposal_id and revision from your request, with payload containing members,
    coverage and summary. Only the assigned agent can submit the current stage. This tool never
    approves or creates members; the user does that in Team builder.
    """
    return call('room_team_builder', dict(action=action, proposal_id=proposal_id,
                                         revision=revision, payload=payload))


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False})
def room_browser(action: str = 'status', url: str = '', ref: int | None = None,
                 text: str = '', key: str = '', modifiers: int = 0,
                 x: float | None = None, y: float | None = None, delta: float = 500,
                 accept: bool = False) -> list:
    """Use the Browser panel's shared local-preview page. status/inspect/screenshot observe it.
    claim control before open/click/type/key/scroll/reload/dialog/stop; release when finished.
    Another agent or the user may own it; never steal control. Agent control expires after 60s.
    open accepts local http(s) previews, excluding room and control ports. Pop-ups are blocked.
    inspect writes data-room-ref attributes on page elements and returns visible text and refs;
    use ref with click/type, or viewport x/y coordinates. type inserts text into the focused field.
    select chooses an option by ref and text (option value). key accepts Enter, Tab, Backspace, Delete,
    Escape, arrows, Home/End, PageUp/Down or a (modifiers: Ctrl=2, Shift=8).
    screenshot returns the same page pixels the user sees. The page uses a temporary profile.
    """
    args = dict(action=action, url=url, ref=ref, text=text, key=key, modifiers=modifiers,
                delta=delta, accept=accept)
    if x is not None:
        args['x'] = x
    if y is not None:
        args['y'] = y
    result = call('room_browser', args)
    pixels = result.pop('image', None)
    content = [json.dumps(result)]
    if pixels:
        content.append(Image(data=base64.b64decode(pixels), format='jpeg'))
    return content


if __name__ == '__main__':
    mcp.run(transport='stdio')
