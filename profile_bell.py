"""Role profile approvals in the user's bell (task b0eaf89e, Zero f14bfb67).

When an agent's drafted role core (agent-profiles/<agent>/core.md) differs from what the user approved (its text,
version or any pinned skill, the digest approve_core binds), or the agent has none approved, the room keeps one open `system` follow-up for the user naming the agent and the draft. Approve
or Revoke on the agent's card resolves it. A newer draft rewrites the open item rather than opening a second one,
and a revoked draft stays closed until a different draft is published (revoke_core records `core_dismissed`).

The open items are derived from the drafts and the profile records on every pass, never from a saved pointer, so a
restart or a lost board save adopts the open item by its prefix and agent id instead of duplicating it (the pattern
of process_enforcement's train item, backend 0345591e).
"""
import logging
import time

from agent_profiles import draft_digest
from process_enforcement import SYSTEM, TICK_SECONDS, train_channel

LOGGER = logging.getLogger(__name__)
PREFIX = 'Role profile:'      # with the agent id, how the room recognises its own item
PROFILE_TASK = 'b8bc48b6'     # Agent profiles; linked when it is on this project's board


def due(runtime, now=None):
    """The runtime beat's rate limit: True at most once a minute. The beat then reads drafts in a thread and syncs."""
    now = time.time() if now is None else now
    if now - getattr(runtime, 'profile_bell_ticked', 0) < TICK_SECONDS:
        return False
    runtime.profile_bell_ticked = now
    return True


def drafts(runtime):
    """Each live agent's parsed draft, or None when it has none or it doesn't parse. File reads only, so the core
    endpoint can run it in a thread. A draft that doesn't parse can't be approved, so it gets no item; the reason
    is logged once and shown on the agent's Role profile card."""
    found = {}
    warned = getattr(runtime, 'profile_bell_warned', None)
    if warned is None:
        warned = runtime.profile_bell_warned = set()
    for name, profile in list(runtime.profiles.items.items()):
        if profile.get('archived'):
            found[name] = None  # judged, not read: its open item closes
            continue
        try:
            found[name] = runtime.profiles.core_draft(name)
        except (OSError, ValueError) as exc:
            found[name] = None
            if 'No drafted core' not in str(exc) and (name, str(exc)) not in warned:
                warned.add((name, str(exc)))
                LOGGER.warning('Role profile draft for %s is not approvable: %s', name, exc)
    return found


def approved_digest(profile):
    """The digest of what the user approved, as approve_core bound it; None when nothing is approved."""
    if not profile.get('core_version'):
        return None
    return draft_digest({field: profile.get(field) for field in ('core', 'core_version', 'skills')})


def pending(profile, draft):
    """A draft the user hasn't answered: different from what was approved (core text, version or a skill re-pinned
    without a version bump, which agent_skills.approved would otherwise withhold silently, harness 1de4eb74 B1),
    or nothing approved, and not revoked."""
    if not draft or draft['digest'] == profile.get('core_dismissed'):
        return False
    return draft['digest'] != approved_digest(profile)


def changes(profile, draft):
    """What differs from the approved record, for the item text: the version, the core text and each skill."""
    if not profile.get('core_version'):
        return ''
    notes = []
    if draft['core_version'] != profile['core_version']:
        notes.append(f"version {profile['core_version']} is approved")
    elif draft['core'] != profile.get('core'):
        notes.append('the core text changed without a version change')
    before = {s['name']: s['sha256'] for s in profile.get('skills') or []}
    versions = {s['name']: s['version'] for s in draft.get('skill_details') or []}
    for skill in draft['skills']:
        if skill['name'] not in before:
            notes.append(f"new skill {skill['name']}")
        elif before[skill['name']] != skill['sha256']:
            notes.append(f"{skill['name']} changed (now v{versions.get(skill['name'], '?')})")
    notes += [f'{name} removed' for name in before if name not in {s['name'] for s in draft['skills']}]
    return ' Since your approval: ' + '; '.join(notes) + '.' if notes else ''


def item_text(profile, draft):
    handle = profile.get('handle', profile['id'])
    return (f"{PREFIX} {profile['id']} (@{handle}) has a drafted role core awaiting your approval: version "
            f"{draft['core_version']}, {draft['words']} words, {len(draft['skills'])} "
            f"skill{'' if len(draft['skills']) == 1 else 's'}.{changes(profile, draft)} "
            f"Open Team, then {profile['name']}'s edit card, then Role profile, to approve or revoke it. This closes "
            'itself when you do.')


def outcome(profile, draft):
    if profile.get('archived'):
        return 'The agent was archived.', True
    if draft is None:
        return 'The draft is no longer available.', True
    if draft['digest'] == profile.get('core_dismissed'):
        return f"Revoked. Version {draft['core_version']} stays unapproved until a new draft is published.", False
    return f"Approved: version {profile.get('core_version')} is on record from the agent's next turn.", False


def sync(runtime, found):
    """Open, rewrite or close each agent's item to match `found` (from drafts). Returns the actions taken."""
    room = runtime.project_room
    if room.path is None or runtime.ready_wakes.room_gate():
        return None
    deployment = getattr(runtime, 'deployment', None)
    if deployment is not None and not deployment.enter_mutation():
        return None  # the next minute catches up
    try:
        actions = reconcile(runtime, room, found)
        if actions:
            room.save()
        for action in actions:
            announce(runtime, *action)
        return [kind for kind, _ in actions]
    finally:
        if deployment is not None:
            deployment.leave_mutation()


def reconcile(runtime, room, found):
    profiles = runtime.profiles.items
    task_id = PROFILE_TASK if any(t['id'] == PROFILE_TASK for t in room.state['tasks']) else ''
    open_items = {}
    for item in room.state.get('followups', []):
        if item['status'] == 'open' and item['created_by'] == SYSTEM and item['assigned_to'] == 'user' \
                and item['text'].startswith(PREFIX + ' '):
            name = item['text'][len(PREFIX) + 1:].split(' ', 1)[0]
            if name in open_items:
                # Only a pre-adoption crash can make a second one; keep the first, close the rest.
                open_items.setdefault('', []).append(item)
            else:
                open_items[name] = item
    actions = []
    for item in open_items.pop('', []):
        actions.append(close(room, item, 'A duplicate of this agent\'s open item.', cancel=True))
    for name in set(open_items) | {n for n, d in found.items() if d}:
        profile, draft, item = profiles.get(name), found.get(name), open_items.get(name)
        if profile is None:
            actions.append(close(room, item, 'The agent no longer exists.', cancel=True))
        elif pending(profile, draft):
            text = item_text(profile, draft)
            if item is None:
                item = room.records.followup(SYSTEM, 'create', task_id=task_id, assigned_to='user', text=text)['item']
                actions.append(('opened', item))
            elif item['text'] != text:
                # A newer draft: the same item, rewritten (records keep no separate edit history for system items).
                item.update(text=text, updated=time.time())
                room.records.save(SYSTEM, room_task(room, item), f"Follow-up {item['id']} updated: {text}")
                actions.append(('updated', item))
        elif item is not None and name in found:
            # Only agents whose drafts were read this pass are judged, so a partial read never closes an item.
            result, cancel = outcome(profile, draft)
            actions.append(close(room, item, result, cancel=cancel))
    return actions


def room_task(room, item):
    return next((t for t in room.state['tasks'] if t['id'] == item['task_id']), None) if item['task_id'] else None


def close(room, item, result, cancel=False):
    room.records.followup(SYSTEM, 'cancel' if cancel else 'resolve', followup_id=item['id'], result=result)
    return ('closed', item)


def announce(runtime, kind, item):
    """One system chat line and a board refresh, so the bell shows the change now. The user is never woken; the
    bell is the delivery (as for E5's item)."""
    channel = train_channel(runtime)
    item.setdefault('channel', channel)
    ref = {'collection': 'followups', 'id': item['id'], 'updated': item['updated'], 'action': kind}
    text = (f"Follow-up {item['id']} closed by the room: {item['result']}" if kind == 'closed'
            else f"Follow-up {item['id']} for user: {item['text']}")
    runtime.host.store.add(SYSTEM, text, channel=channel, metadata={'no_route': True, 'coordination_ref': ref})
    runtime.project_state_soon()
