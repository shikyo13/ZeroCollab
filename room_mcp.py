"""Run-scoped local coordination tools for official native CLIs."""
import os
import base64
import json
from pathlib import Path
from typing import Literal

import httpx
from mcp.server.fastmcp import FastMCP, Image

# room_image returns pixels on demand; large files would crowd the model's context.
IMAGE_LIMIT = 5 * 1024 * 1024
# room_motion waits: starting a headless browser for up to 6 frames takes longer than a board call, and inspect
# decodes the whole video three times (video_analysis: up to 180 s per FFmpeg pass). An inspect that outlives
# its wait still finishes and is cached, so asking again returns at once.
MOTION_TIMEOUTS = {'frame': 60, 'inspect': 600}
# record_start waits up to 10 s for its turn in the media queue and for capture to begin (room_runtime
# RECORD_READY_SECONDS); stop and status answer at once.
BROWSER_TIMEOUTS = {'record_start': 60}
MAX_SHOWN_SHEETS = 6  # images attached to one inspect answer; the rest stay as urls in the JSON
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


mcp = RoomMCP('project_room', instructions='These tools operate the same durable project and kanban board visible to the user. Use room_task for ownership/dependencies, room_review for peer review, room_followup for tracked next actions, and room_read(section="inbox") at checkpoints. room_update leaves progress without waking peers. room_browser shows and drives the Browser panel the user sees (status, claim, open a local preview, inspect, screenshot, release); use it to check UI changes as the user sees them and to capture before and after screenshots. Native tools remain available; your final answer is published automatically.')


def call(tool, arguments, timeout=15):
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        response = client.post(os.environ['AGENT_ROOM_URL'] + '/api/runtime/coordination/' + os.environ['AGENT_ROOM_RUN_ID'],
            headers={'X-Session-Token': os.environ['AGENT_ROOM_SESSION_TOKEN']}, json={'tool': tool, 'arguments': arguments})
    if response.status_code != 200:
        try:
            body = response.json()
        except ValueError:  # a plain-text server error page, not the room's JSON
            body = None
        error = body.get('error') if isinstance(body, dict) else None
        return {'error': error or f'Room request failed (HTTP {response.status_code})'}
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
              priority: int | None = None, wait: dict | None = None, evidence: dict | None = None,
              next_action_state: str | None = None,
              close_reason: Literal['duplicate', 'superseded', 'parked', 'wont_do'] | None = None,
              close_pointer: str = '', acceptance: dict | None = None) -> dict:
    """Get, create, claim, update, delegate, accept or release a kanban task. get returns the full record: history, details, attachments, reviews/followups and execution_state (executing, queued, waiting_on_human, needs_attention, blocked, waiting_on_dependency, in_review, idle). Other actions return a compact receipt (state, next_action, counts). delegate proposes a transfer; the current owner keeps the task until the recipient calls accept. next_action records the next executable step; priority is 1 (highest) to 5. wait {kind: followup|review, record_id} links an open request to the user on this task while you wait for them ({} clears it; a next_action is required). evidence records implementation, review, installation and acceptance separately. acceptance {manifest_sha, result: passed|failed, text} records your live check of one installed release (owner or user only; the room keeps who and when). Claim relative paths before editing; overlapping scopes and dependency cycles are refused. labels tag the task's area (e.g. frontend, backend, docs; up to 4) and group the user's board. attachments replaces the task's image list: [{url: '/uploads/<file>', name, note}] (the card shows the 8 newest; when more are added, older ones move to earlier images instead of failing, and the receipt lists them; up to 48 per call, split larger batches; a note describes an annotation). Ownership and status changes are recorded in task history automatically. Agents must resolve requested reviews and dependencies before marking done; the user can override. next_action_state (owner or user only): ready opts this task in to one automatic wake per external change when the room's ready_task_wakes setting is on; deferred or waiting stops that; empty clears it.
    To end a task that delivered nothing itself, update with close_reason and close_pointer instead of status done: duplicate or superseded point at the task that carries the work on (its dependents are moved there), parked or wont_do point at that task or a decision note. A closure skips the review and installation gates because it claims no delivery, cancels the task's open reviews and follow-ups, clears any pending transfer or wait, shows its reason on the board, and is reopened by setting a status. A closed task reads status done with a closure record: count it as closed, never as delivered work."""
    args = dict(action=action, task_id=task_id, title=title, details=details,
                paths=paths, owner=owner, status=status, update=update, depends_on=depends_on,
                labels=labels, attachments=attachments, next_action=next_action, priority=priority,
                wait=wait, evidence=evidence, next_action_state=next_action_state)
    if close_reason or close_pointer:
        args.update(close_reason=close_reason or '', close_pointer=close_pointer)
    if acceptance is not None:
        # The live check of an installed release: {manifest_sha, result: passed|failed, text?}, owner or user only.
        args['acceptance'] = acceptance
    return call('room_task', args)


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
def room_update(kind: str, text: str, supersedes: str = '', resolves: str = '',
                release_checks: dict | None = None) -> dict:
    """Record concise progress, decision, reference, blocker or handoff information without waking peers. Use reference for shared project facts teammates should reuse: caches, helper scripts, data paths. A decision or reference can retire an active earlier one of the same kind by id: supersedes (replaced by this one) or resolves (no longer applies). Only current decisions and references appear in turn context.
    The room manager (or the user) can add release_checks {manifest_sha: the 64-hex installed manifest, tasks: up to 32 task ids} to a decision: the tasks whose current owners must check that release live. Each owner then records room_task update acceptance {manifest_sha, result: passed|failed, text}."""
    args = {'kind': kind, 'text': text, 'supersedes': supersedes, 'resolves': resolves}
    if release_checks is not None:
        args['release_checks'] = release_checks
    return call('room_update', args)


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False})
def room_review(action: str, task_id: str = '', review_id: str = '', reviewer: str = '',
                revision: str = '', text: str = '', verdict: str = '', notify: bool = True,
                files: list[str] | None = None) -> dict:
    """Request peer review, respond with findings, or cancel with a reason. request needs task_id, reviewer, revision (an explicit artifact reference such as a commit/diff digest or proposal message ID), and text describing scope/questions. respond needs review_id, exact revision inspected, text with concrete evidence, and verdict approved or changes_requested. cancel needs review_id and text explaining why. New references supersede that peer's older review. Revision references are caller-supplied; use a new reference after material edits. On request the room also hashes the task owner's files (files: relative paths, default the task's file paths) and keeps a copy; approval is refused if they changed since, and room_read(section="review_artifact", query="<review-id>:<path>") returns the captured copy. notify attempts one bounded wake-up; requests persist in the inbox even if a peer cannot start. A respond always notifies the owner, since they are waiting on the verdict."""
    return call('room_review', dict(action=action, task_id=task_id, review_id=review_id, reviewer=reviewer,
                                   revision=revision, text=text, verdict=verdict, notify=notify, files=files))


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False})
def room_followup(action: str, followup_id: str = '', task_id: str = '', assigned_to: str = '',
                  text: str = '', result: str = '', notify: bool = True,
                  followup_ids: list[str] | None = None) -> dict:
    """Create a durable next action/question for a participant, optionally linked to a task. Resolve with a concrete result, or cancel with a reason. Open items remain in the assignee's inbox. notify attempts one bounded wake-up; no polling, automatic reminders or agent spawning. A resolve always notifies the other party, since they are waiting on the result.
    To answer several follow-ups with the same result (for example one release receipt), resolve once with followup_ids (up to 20) instead of followup_id: each record resolves and each requester is told, with one chat line. An id you can't resolve refuses the whole call."""
    args = dict(action=action, followup_id=followup_id, task_id=task_id,
                assigned_to=assigned_to, text=text, result=result, notify=notify)
    if followup_ids:
        args['followup_ids'] = followup_ids
    return call('room_followup', args)


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False})
def room_repository(path: str, reason: str, create: bool = False, action: str = 'replace') -> dict:
    """Ask the user to add a repository (action add) or replace the primary (action replace). Use an existing absolute repository root, or create true for a new folder. Only the user approves. Running turns can finish and queued work is kept. Task/review paths may use key:relative/path; bare task paths are pinned to the primary when claimed."""
    return call('room_repository', dict(path=path, create=create, reason=reason, action=action))


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False})
def room_goal(action: str = 'status', goal_id: str = '', evidence: str = '') -> dict:
    """Read the shared room goal (status), or as its named lead finish it: complete or block with goal_id and evidence. The user starts, pauses and cancels goals."""
    return call('room_goal', dict(action=action, goal_id=goal_id, evidence=evidence))


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': True, 'openWorldHint': False})
def room_release(action: Literal['list', 'stage', 'rehearse', 'request', 'status', 'verify', 'rollback'] = 'status',
                 repository: str = '', commit: str = '', reviews: list[str] | None = None, release_id: str = '',
                 manifest_sha: str = '', expected_manifest_sha: str = '', idempotency_key: str = '', id: str = '',
                 deployment_id: str = '') -> dict:
    """The room's release pipeline, without the browser (Zero #15583). Only the profile Zero has granted release
    permission to can change anything; the room takes who you are from this turn, never from arguments, and
    rollback always goes to the release installed just before. If this tool refuses or is unavailable, report
    that; never use the Updates page, credentials or files instead.
    list: staged releases. stage repository commit (40 hex) reviews (1-12 approved review ids, 8 hex): package
    exactly that reviewed commit. rehearse release_id manifest_sha: an isolated rehearsal of it. request
    release_id manifest_sha expected_manifest_sha (the installed one) idempotency_key: ask to install it, under
    Zero's grant, his Stop and the typing guard. status (id optional, 32 hex): the current deployment, gate and
    jobs, or one job or deployment receipt. verify deployment_id: check an installed release. rollback
    expected_manifest_sha idempotency_key: return to the release installed just before."""
    args = {'action': action}
    for key, value in dict(repository=repository, commit=commit, release_id=release_id, manifest_sha=manifest_sha,
                           expected_manifest_sha=expected_manifest_sha, idempotency_key=idempotency_key, id=id,
                           deployment_id=deployment_id).items():
        if value:
            args[key] = value
    if reviews is not None:
        args['reviews'] = reviews
    return call('room_release', args, timeout=60)


@mcp.tool(annotations={'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False})
def room_skill(action: Literal['list', 'load'] = 'list', name: str = '', file: str = '') -> dict:
    """Your approved skills. list gives each name and when to use it; load name returns its full procedure, which you then follow; load name with file returns one of that skill's listed extra files. Only your own skills load."""
    return call('room_skill', dict(action=action, name=name, file=file))


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False})
def room_team_builder(action: str = 'get', proposal_id: str = '', revision: int = 0,
                      payload: dict | None = None, member: dict | None = None, reason: str = '') -> dict:
    """Read the current team proposal/context (get) or submit an assigned draft/challenge (submit).
    Submit the exact proposal_id and revision from your request, with payload containing members,
    coverage and summary. Only the assigned agent can submit the current stage. This tool never
    approves or creates members; the user does that in Team builder.
    suggest: when the team lacks a skill the work needs, propose ONE new member for the user's review.
    member = {name, id (lowercase handle), template (a signed-in native connection from get), model,
    effort, background (the new agent's full instructions), rationale}; reason = the missing
    capability and the expected deliverable (up to 1000 characters). Call once per member: while a
    suggestion is waiting, further suggestions join it and the user decides on each one separately.
    Refused while a team proposal the user started is open, for an existing or already suggested
    handle, or for one the user already declined. Suggest only for a real gap.
    """
    arguments = dict(action=action, proposal_id=proposal_id, revision=revision, payload=payload)
    if action == 'suggest':
        arguments.update(member=member, reason=reason)
    return call('room_team_builder', arguments)


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False})
def room_decision(action: str = 'get', decision_id: str = '', stage: str = '', revision: int = 0,
                  epoch: int = 0, payload: dict | None = None, message_id: int = 0, question: str = '',
                  criteria: list | None = None, participants: list | None = None, inputs: list | None = None,
                  task_id: str = '', reason: str = '') -> dict:
    """Take part in a team decision. get returns the question, criteria and the contributions you may
    see (other positions stay sealed until everyone has submitted). submit records your contribution
    for the exact stage, revision and epoch from your request; stale or unowed submissions are refused.
    Positions take payload {text, evidence, attachments: [{url: "/uploads/...", name}]} for mockups.
    open (the lead or backup answering a user message): start a decision in this chat on message_id,
    when a choice affects more than one owner or a shared interface, or the user asks to deliberate.
    Give question, criteria, participants (2-8 agents, you lead) and inputs: the user's ideas as
    [{quote: word for word from their message, message_id, kind: requirement|suggestion}]; optional
    task_id. Opening the same message again returns the existing decision. reopen (lead only, with
    reason) starts another round when the user asks to reconsider. The user records the outcome; a
    decision grants no deploy, settings or credential authority.
    """
    args = dict(action=action, decision_id=decision_id, stage=stage, revision=revision, epoch=epoch, payload=payload)
    if action == 'open':
        args.update(message_id=message_id, question=question, criteria=criteria or [],
                    participants=participants or [], inputs=inputs or [], task_id=task_id)
    if action == 'reopen':
        args['reason'] = reason
    return call('room_decision', args)


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False})
def room_browser(action: str = 'status', url: str = '', ref: int | None = None,
                 text: str = '', key: str = '', modifiers: int = 0,
                 x: float | None = None, y: float | None = None, delta: float = 500,
                 accept: bool = False, page: str = '', recording_id: str = '',
                 max_seconds: float | None = None, fps: Literal[30, 60] | None = None) -> list:
    """See and drive the user's Browser panel: check a UI change the way the user sees it, and capture
    before and after screenshots for mockups and reviews. screenshot also returns an /uploads URL to pass
    to room_markup or task attachments. status/inspect/screenshot observe the page.
    claim control before open/click/type/key/scroll/reload/dialog/stop; release when finished.
    Another agent or the user may own it; never steal control. Agent control expires after 60s.
    open accepts local http(s) previews, excluding room and control ports. Pop-ups are blocked.
    inspect writes data-room-ref attributes on page elements and returns visible text and refs;
    use ref with click/type, or viewport x/y coordinates. type inserts text into the focused field.
    select chooses an option by ref and text (option value). key accepts Enter, Tab, Backspace, Delete,
    Escape, arrows, Home/End, PageUp/Down or a (modifiers: Ctrl=2, Shift=8).
    screenshot returns the same page pixels the user sees. The page uses a temporary profile.
    Pages: actions apply to the active page unless page names one from status pages[]; a closed page
    is refused, never redirected. new_page (optional url) opens another page and makes it active;
    select_page and close_page take page. Keep to one page unless you need to compare two.
    Recording real product footage (your own browser only, while you hold control): record_start
    (max_seconds up to 120, default 60; fps 30 or 60, default 60) records the page as you drive it, with
    clicks, scrolls and key markers (never typed text) kept for the cursor layer; it stops by itself at
    max_seconds or when the page closes or is resized. A take shares the room's one-at-a-time media queue
    with exports, frames and inspect: record_start waits a few seconds for it to begin, and if phase is
    still queued or preparing, poll record_status recording_id and drive the page only once phase is
    recording; anything before that is not in the clip. record_stop recording_id returns at once; the
    encode may wait its turn (stage encode, phase queued). Poll record_status until phase is done (or
    failed, with error). room_motion job_cancel (job_id = the recording_id, with its generation) cancels a take you started. A done take has
    fileref (fileref:<id>) and clip_url: the clip stays where it was recorded, never copied. Pass the fileref to
    room_motion inspect url, or link clip_url in chat, where it plays inline.
    """
    args = dict(action=action, url=url, ref=ref, text=text, key=key, modifiers=modifiers,
                delta=delta, accept=accept)
    if page:
        args['page'] = page
    if recording_id:
        args['recording_id'] = recording_id
    if action == 'record_start':
        args.update({k: v for k, v in dict(max_seconds=max_seconds, fps=fps).items() if v is not None})
    if x is not None:
        args['x'] = x
    if y is not None:
        args['y'] = y
    result = call('room_browser', args, timeout=BROWSER_TIMEOUTS.get(action, 15))
    pixels = result.pop('image', None)
    content = [json.dumps(result)]
    if pixels:
        content.append(Image(data=base64.b64decode(pixels), format='jpeg'))
    return content


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False})
def room_motion(action: str = 'list', scene: str = '', scene_json: dict | None = None, overwrite: bool = False,
                t_ms: float | list[float] = 0, reduced: bool = False, job_id: str = '', task_id: str = '',
                note: str = '', name: str = '', post: bool = True,
                profile: Literal['webm', 'vp9-alpha', 'av1', 'prores4444', 'gif', 'png', 'png-sequence'] = 'webm',
                new_id: str = '', composition: str = '', props: dict | None = None, ops: list[dict] | None = None,
                url: str = '', asset_id: str = '', kind: str = '', layer_id: str = '', vs: str = '',
                sheets: list[Literal['contact', 'dense', 'motion', 'shots', 'frames']] | None = None,
                start: float | None = None, end: float | None = None, fps: float | None = None,
                generation: int = 0) -> list:
    """Author motion scenes and product videos with the room's motion engine (data, never code).
    list: bundled and project scenes. get: one scene by ref (bundled:<id> or project:<id>).
    validate: check scene_json; returns {ok, issues: [{path, message}]}. Fix every issue, then save.
    save: write scene_json as project:<id> (overwrite to replace). frame: render the scene at t_ms (one
    time or up to 6) exactly as the video will look; returns /uploads PNGs plus a contact sheet you can
    post with room_markup. export: start the video (one at a time) in profile webm (default), vp9-alpha
    or av1 (WebM), prores4444 (.mov with alpha), gif, png (first frame only; use frame for a chosen time)
    or png-sequence (.zip of numbered frames); a profile this room's FFmpeg can't encode is refused, never
    swapped. export_status / export_cancel job_id. publish job_id: deliver a finished WebM export into this
    chat (post false to skip), and task_id attaches it to a task you own; it plays inline there. Other
    profiles are not published; share their video_url from export_status. Author new work as v2
    (Docs/motion-scene-v2.md: compositions, layers, keyframes, transitions); v1 room replays (get
    bundled:m01) still render. frame and export use the first composition. Sound (audio, voiceover,
    unmuted video) exports once this room's offline voice worker is installed; until then export refuses it,
    and validate and save say what is missing.
    variant scene new_id: save a template re-render as project:<new_id>, with composition (default first)
    and props overriding its props, checked against props_schema. patch scene ops: RFC 6902 JSON Patch on a
    saved project scene (for example replace /compositions/0/props/title); nothing is written unless the
    result validates. import_asset url asset_id: turn a room upload (a room_browser screenshot url, a
    recording, an image) into an asset with its real sha256 and, for images, width, height and how it fits
    the frame; with scene it's added to that project scene (kind is inferred, or image, capture, video,
    font, audio). url can also be a registered original used where it lies: fileref:<id> from a recording
    or a pasted path. A recording also returns layers: a muted video layer (id layer_id, default asset_id)
    and its cursor layer from the recorded clicks, ready to place together in one clip. inspect job_id (an export) or url (a /uploads video, or a pasted file as fileref:<id> or
    its /api/files/ref link): measures resolution, fps, cuts and transitions, shot lengths, motion and holds,
    and loudness (LUFS); vs (the same forms, or export:<job_id>) adds a side-by-side against a reference.
    sheets (default contact and motion) draws contact (8 frames), dense (every 1/fps s from start to end,
    fps up to 10, at most 48 tiles: small icon motion), motion (movement over time, cuts marked), shots
    (the middle of each shot) and frames (at t_ms); they come back as /uploads PNGs and are shown here.
    Heavy jobs (export, frame, inspect, recordings) take turns, one at a time on this room. If inspect is
    still waiting or running after a few seconds, it returns its handle (job_id, generation, phase,
    position) and carries on: job_status job_id returns its phase and, once done, its result (sheets shown
    here); job_cancel job_id generation stops a job you started. export_status lists this project's
    media_jobs.
    Loop: validate until clean, save, frame 2-3 key moments and show them, export, inspect it against the
    reference, then publish."""
    args = dict(action=action)
    if scene:
        args['scene'] = scene
    if scene_json is not None:
        args['scene_json'] = scene_json
    if action in ('save', 'variant', 'import_asset'):
        args['overwrite'] = overwrite
    if action == 'variant':
        args.update(new_id=new_id, composition=composition, props=props or {}, name=name)
    if action == 'patch':
        args['ops'] = ops
    if action == 'import_asset':
        args.update(url=url, asset_id=asset_id, kind=kind, **({'layer_id': layer_id} if layer_id else {}))
    if action == 'inspect':
        args.update({k: v for k, v in dict(url=url, vs=vs, sheets=sheets, start=start, end=end, fps=fps).items()
                     if v not in (None, '')})
    if action in ('frame', 'inspect'):
        args['t_ms'] = t_ms
    if action in ('frame', 'export'):
        args['reduced'] = reduced
    if action == 'export':
        args['profile'] = profile
    if job_id:
        args['job_id'] = job_id
    if action == 'job_cancel' and generation:
        args['generation'] = generation
    if action == 'publish':
        args.update(task_id=task_id, note=note, name=name, post=post)
    result = call('room_motion', args, timeout=MOTION_TIMEOUTS.get(action, 15))
    content = [json.dumps(result)]
    shown = []
    # Only frame and inspect return images; an export job's 'frames' is its frame count.
    if action == 'frame' and isinstance(result, dict) and isinstance(result.get('frames'), list) and result['frames']:
        shown = [result.get('sheet_url') or result['frames'][0].get('url')]
    if action == 'job_status' and isinstance(result, dict) and isinstance(result.get('result'), dict):
        result = result['result']  # a finished frame or inspect: show its images as the call itself would
        action = 'frame' if isinstance(result.get('frames'), list) else 'inspect'
        if action == 'frame' and result['frames']:
            shown = [result.get('sheet_url') or result['frames'][0].get('url')]
    if action == 'inspect' and isinstance(result, dict):
        shown = [sheet.get('url') for sheet in result.get('sheets', [])][:MAX_SHOWN_SHEETS]
    if not any(shown):
        return content
    with httpx.Client(timeout=15, trust_env=False) as client:
        for url in filter(None, shown):
            image = client.get(os.environ['AGENT_ROOM_URL'] + url)
            if image.status_code == 200 and len(image.content) <= IMAGE_LIMIT:
                content.append(Image(data=image.content, format='png'))
    return content


if __name__ == '__main__':
    mcp.run(transport='stdio')
