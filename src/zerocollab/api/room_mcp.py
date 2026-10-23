import sys as _bootstrap_sys
from pathlib import Path as _BootstrapPath
if __package__ in ('', None):
    _bootstrap_sys.path.insert(0, str(_BootstrapPath(__file__).resolve().parents[2]))

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
# record_start waits briefly for the external-tool probe and capture preparation.
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


mcp = RoomMCP('project_room', instructions='These tools operate the same durable project and kanban board visible to the user. Use room_task for ownership/dependencies, room_review for peer review, room_followup for tracked next actions, and room_read(section="inbox") at checkpoints. room_update leaves progress without waking peers. room_browser shows and drives the Browser panel the user sees (status, claim, open a local preview or site, inspect, screenshot, release); use it to check UI changes as the user sees them and to capture before and after screenshots. Native tools remain available; your final answer is published automatically.')


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
              close_pointer: str = '', acceptance: dict | None = None, wait_on: list[dict] | None = None) -> dict:
    """Get, create, claim, update, delegate, accept or release a kanban task. get returns the full record: history, details, attachments, reviews/followups and execution_state (executing, queued, waiting_on_human, needs_attention, blocked, waiting_on_dependency, in_review, idle). Other actions return a compact receipt (state, next_action, counts). delegate proposes a transfer; the current owner keeps the task until the recipient calls accept. next_action records the next executable step; priority is 1 (highest) to 5. wait {kind: followup|review, record_id} links an open request to the user on this task while you wait for them ({} clears it; a next_action is required). evidence records implementation, review, installation and acceptance separately. acceptance {manifest_sha, result: passed|failed, text} records your live check of one installed release (owner or user only; the room keeps who and when). Claim relative paths before editing; overlapping scopes and dependency cycles are refused. labels tag the task's area (e.g. frontend, backend, docs; up to 4) and group the user's board. attachments replaces the task's image list: [{url: '/uploads/<file>', name, note}] (the card shows the 8 newest; when more are added, older ones move to earlier images instead of failing, and the receipt lists them; up to 48 per call, split larger batches; a note describes an annotation). Ownership and status changes are recorded in task history automatically. Agents must resolve requested reviews and dependencies before marking done; the user can override. next_action_state (owner or user only): a working task with a next_action is continued by the room after a turn that moved it, and its idle owner is woken after 10 minutes; waiting or deferred (with the reason on the task), or a wait, stops that. ready also opts in to one wake per external change; empty clears it. A waiting or deferred task is not forgotten: wait_on (owner or user only, up to 4 of {task: id}, {review: id}, {followup: id} or {release: "0.12.55"}; [] clears it) names what it waits for, and the room wakes you once when all of them are met. A wait that names nothing is re-checked: you are woken once if it hasn't changed in 3 hours. A task whose last dependency finishes wakes you once.
    To end a task that delivered nothing itself, update with close_reason and close_pointer instead of status done (the lead may close any task this way): duplicate or superseded point at the task that carries the work on (its dependents are moved there), parked or wont_do point at that task or a decision note. A closure skips the review and installation gates because it claims no delivery, cancels the task's open reviews and follow-ups, clears any pending transfer or wait, shows its reason on the board, and is reopened by setting a status. A closed task reads status done with a closure record: count it as closed, never as delivered work."""
    args = dict(action=action, task_id=task_id, title=title, details=details,
                paths=paths, owner=owner, status=status, update=update, depends_on=depends_on,
                labels=labels, attachments=attachments, next_action=next_action, priority=priority,
                wait=wait, evidence=evidence, next_action_state=next_action_state)
    if close_reason or close_pointer:
        args.update(close_reason=close_reason or '', close_pointer=close_pointer)
    if acceptance is not None:
        # The live check of an installed release: {manifest_sha, result: passed|failed, text?}, owner or user only.
        args['acceptance'] = acceptance
    if wait_on is not None:
        args['wait_on'] = wait_on  # C6a: what a waiting task names as its wait
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
                files: list[str] | None = None, critical: list[dict] | None = None,
                notes: list[str] | None = None) -> dict:
    """Request peer review, respond with findings, or cancel with a reason. request needs task_id, reviewer, revision (an explicit artifact reference such as a commit/diff digest or proposal message ID), and text describing scope/questions. respond needs review_id, exact revision inspected, text with concrete evidence, and verdict approved or changes_requested. Severity is recorded, not argued: critical (up to 12 of {text, class}, text up to 1000 characters) lists concrete failures, each the inputs or state and then the wrong result; notes (up to 12 texts) is everything else. changes_requested needs at least one critical entry, and approved carries notes only; notes become one follow-up on the owner's task. On a release-pipeline task each critical names its class: room_down, data_loss, stop_overridden, unrecoverable or security_exposure. cancel needs review_id and text explaining why; an agent's cancel wakes nobody (the reviewer sees it in their next turn). New references supersede that peer's older review. Revision references are caller-supplied; use a new reference after material edits. Any agent may request a review of its own work on any task; the reviewer must be someone else, and the verdict reaches both the requester and the task's owner, and the owner can cancel a stray request on their task. On request the room also hashes the requester's files (files: relative paths in your workspace, default the task's file paths) and keeps a copy; approval is refused if they changed since, and room_read(section="review_artifact", query="<review-id>:<path>") returns the captured copy. An agent's request starts no turn at once: if the reviewer is idle and it is still pending after 2 minutes, the room wakes them once, so a re-request with a new revision replaces it first (notify applies only to the user's requests). Requests persist in the inbox. A respond always notifies the owner and the requester, since they are waiting on the verdict."""
    args = dict(action=action, task_id=task_id, review_id=review_id, reviewer=reviewer,
                revision=revision, text=text, verdict=verdict, notify=notify, files=files)
    if critical is not None:
        args['critical'] = critical
    if notes is not None:
        args['notes'] = notes
    return call('room_review', args)


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False})
def room_followup(action: str, followup_id: str = '', task_id: str = '', assigned_to: str = '',
                  text: str = '', result: str = '', notify: bool = True,
                  followup_ids: list[str] | None = None) -> dict:
    """Create a durable next action/question for a participant, optionally linked to a task. Resolve with a concrete result, or cancel with a reason. Open items remain in the assignee's inbox. notify attempts one bounded wake-up. With notify=false nobody is woken now; if the recipient is idle and it is still unanswered after 2 minutes, the room wakes them once. No other reminders, polling or agent spawning. A result from the user always wakes the other party. A result from an agent wakes the other party when the follow-up is on an open task they own; about anything else they see it in their next turn (a task whose wait_on names the follow-up still wakes its owner). An agent's cancel wakes nobody; the assignee sees it in their next turn.
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
def room_release(action: Literal['list', 'stage', 'rehearse', 'request', 'status', 'verify', 'rollback', 'check',
                                 'intake', 'handoff', 'disposition', 'archive'] = 'status',
                 repository: str = '', commit: str = '', reviews: list[str] | None = None, release_id: str = '',
                 manifest_sha: str = '', expected_manifest_sha: str = '', idempotency_key: str = '', id: str = '',
                 deployment_id: str = '', base: str = '', dependencies: list[str] | None = None, check_id: str = '',
                 handoff_id: str = '', disposition: str = '', reason: str = '', target: str = '', title: str = '',
                 notes: str = '', shipped_tasks: list[dict] | None = None) -> dict:
    """The room's release pipeline, without the browser (Zero #15583). The room takes who you are from this turn,
    never from arguments. Any managed agent may check its own donor and read status and intake; everything else
    belongs to the profile Zero granted release permission to. Rollback always goes to the release installed just
    before. If this tool refuses or is unavailable, report that; never use the Updates page, credentials or files.
    Any agent:
    check repository commit (40 hex) reviews (1-12 approved review ids, 8 hex) base (40 hex, the commit your donor
    applies to) dependencies (optional, up to 12 check ids or release ids your donor needs first): the room
    validates the reviewed donor and returns a check job; one check per agent every 30 s.
    status (id optional, 32 hex): the current deployment, gate and jobs, or one job, deployment receipt, check or
    handoff. Shared with every managed agent.
    intake: this project's donor checks and handoffs, with each handoff's state.
    Release manager only:
    handoff check_id: offer a passed check for the next release. disposition handoff_id disposition (open,
    deferred or dropped) reason (1-1000 characters) target (optional, up to 160): decide an offered handoff.
    archive check_id: move a check out of the active history when it is full; it still answers status.
    list: staged releases. stage repository commit reviews: package exactly that reviewed commit. Optional release
    notes: title (up to 120 characters) and notes (markdown, up to 16000) go into its RELEASE.json; shipped_tasks
    (up to 32 of {task_id: 8 hex, scope: what this release delivers for it, 1-1000 characters}; owners come from the
    board, never from the caller) go into its stage receipt, and after install the room binds their live checks from it.
    Omit all three to stage without them. rehearse
    release_id manifest_sha: an isolated rehearsal. request release_id manifest_sha expected_manifest_sha (the
    installed one) idempotency_key: ask to install it, under Zero's grant and his Stop. verify deployment_id: check
    an installed release. rollback expected_manifest_sha idempotency_key: return to the release installed before."""
    args = {'action': action}
    for key, value in dict(repository=repository, commit=commit, release_id=release_id, manifest_sha=manifest_sha,
                           expected_manifest_sha=expected_manifest_sha, idempotency_key=idempotency_key, id=id,
                           deployment_id=deployment_id, base=base, check_id=check_id, handoff_id=handoff_id,
                           disposition=disposition, reason=reason, target=target, title=title, notes=notes).items():
        if value:
            args[key] = value  # omitted optional fields (dependencies, target, stage metadata) take the core's defaults
    if reviews is not None:
        args['reviews'] = reviews
    if dependencies is not None:
        args['dependencies'] = dependencies
    if shipped_tasks is not None:
        args['shipped_tasks'] = shipped_tasks
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
    submit, open and reopen return a short receipt (stage, the ids recorded, who still owes, next step);
    call get for the full record.
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
    open accepts an http(s) page: a local preview or an outside site, where
    http://127.0.0.1:<room port>/?view=board opens the live room signed in, the same page the user sees.
    A link or script that opens a new tab or window adds it as a page and makes it active (status pages[]).
    inspect writes data-room-ref attributes on page elements and returns visible text and refs;
    use ref with click/type, or viewport x/y coordinates. type inserts text into the focused field.
    select chooses an option by ref and text (option value). key accepts Enter, Tab, Backspace, Delete,
    Escape, arrows, Home/End, PageUp/Down or any single character (modifiers: Alt=1, Ctrl=2, Meta=4,
    Shift=8; with Alt, Ctrl or Meta a character is a shortcut such as Ctrl+K, otherwise it types).
    screenshot returns the same page pixels the user sees. The page uses a temporary profile.
    Pages: actions apply to the active page unless page names one from status pages[]; a closed page
    is refused, never redirected. new_page (optional url) opens another page and makes it active;
    select_page and close_page take page. Keep to one page unless you need to compare two.
    Recording real product footage (your own browser only, while you hold control): record_start
    (max_seconds up to 120, default 60; fps 30 or 60, default 60) records the page as you drive it, with
    clicks, scrolls and key markers (never typed text) kept for the cursor layer; it stops by itself at
    max_seconds; closing or resizing the page cancels the take. The optional standalone motion CLI must be
    installed/configured for encoding. If phase is preparing, poll record_status recording_id and drive the page only once phase is
    recording; anything before that is not in the clip. record_stop recording_id returns at once; the
    external encode continues in its owned process. Poll record_status until phase is done (or
    failed, with error). record_cancel recording_id cancels a take you started. A done take has
    fileref (fileref:<id>) and clip_url: the clip stays where it was recorded, never copied. Pass the fileref to
    the standalone motion CLI/MCP after resolving it to a local file, or link clip_url in chat, where it plays inline.
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




if __name__ == '__main__':
    mcp.run(transport='stdio')
