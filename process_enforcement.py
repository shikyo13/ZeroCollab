"""E3, the release train as a room action (Docs/process-enforcement.md, task a7da2f10, Zero #16116).

When approved, user-visible work has waited past RELEASE_TRAIN_HOURS since the last install, the room opens one
"Cut a release" follow-up for the release manager and wakes them. If nothing is staged within
TRAIN_ESCALATE_SECONDS, it opens one bell item for the user. Both close themselves on the next install, and cancel
themselves when nothing is waiting any more. Agents can't create, extend or suppress these items; they come from
the `system` actor, written only here (security 514c970a S3, S5).

Inputs, all read on the room's own state:
- `project_room.delivery_signals(open_tasks)`, the same computation as the lead's live-context lines (backend's
  split of release-48 0015): `visible` (user-visible approved tasks waiting), `installed_at` and `due`.
- `release_manager.successful_stage_since(project, since)`: a validated successful stage receipt for this project
  and the current predecessor, published after `since` (Astra 180bbb4b). No core, or None, counts as not staged.
"""
import logging
import time

from project_room import RELEASE_GRANTEE

LOGGER = logging.getLogger(__name__)
SYSTEM = 'system'
TICK_SECONDS = 60                    # one evaluation a minute at most
TRAIN_ESCALATE_SECONDS = 3600        # release manager's item open this long with nothing staged: ask the user
TRAIN_COOLDOWN_SECONDS = 2 * 3600    # after the release manager closes an item without staging (S3)
TRAIN_PREFIX = 'E3 release train:'   # how the room recognises its own item after a crash (backend 0345591e)


def tick(runtime, now=None):
    """Called from the runtime beat after manager_wakes.tick(). Does nothing while the room is held."""
    now = time.time() if now is None else now
    if now - getattr(runtime, 'enforcement_ticked', 0) < TICK_SECONDS:
        return None
    runtime.enforcement_ticked = now
    room = runtime.project_room
    if room.path is None or runtime.ready_wakes.room_gate():
        return None
    if RELEASE_GRANTEE not in runtime.states:
        # Archived or renamed release manager: nobody to wake, so stay quiet instead of failing every minute.
        if not getattr(runtime, 'enforcement_warned', False):
            LOGGER.warning('Release train paused: %s is not a participant in this room', RELEASE_GRANTEE)
            runtime.enforcement_warned = True
        return None
    deployment = getattr(runtime, 'deployment', None)
    if deployment is not None and not deployment.enter_mutation():
        return None
    try:
        actions = release_train(runtime, now)
        if actions:
            room.save()
        for action in actions:
            announce(runtime, *action)
        return [kind for kind, _, _ in actions]
    finally:
        if deployment is not None:
            deployment.leave_mutation()


def release_train(runtime, now):
    """Evaluate E3 once. Returns (kind, follow-up, recipient) for each record it changed, to announce after save."""
    room = runtime.project_room
    tasks = [t for t in room.state['tasks'] if t['status'] != 'done']
    signals = room.delivery_signals(tasks)
    train = room.state.setdefault('enforcement', {}).setdefault('train', {})
    item, bell = record(room, train.get('item')), record(room, train.get('escalation'))
    if train and not item:
        train.clear()  # its follow-up is gone: start again rather than never firing (backend 0345591e)
    if not train:
        # A crash between creating the item and saving the board leaves it untracked; adopt it, never open a second.
        item = next((f for f in room.state.get('followups', []) if f['status'] == 'open' and f['created_by'] == SYSTEM
                     and f['assigned_to'] == RELEASE_GRANTEE and f['text'].startswith(TRAIN_PREFIX)), None)
        if item:
            train.update(item=item['id'], opened=item['created'])
    installed = signals.get('installed_at')
    actions = []
    if item and installed and installed > train['opened']:
        actions += close(room, [item, bell], f'Installed at {stamp(installed)}; the release train restarts from here.')
        train.clear()
    elif item and not signals.get('visible'):
        actions += close(room, [item, bell], 'No user-visible approved work is waiting any more.', cancel=True)
        train.clear()
    elif item and item['status'] != 'open':
        # The release manager closed it without an install (S3): no new item until an install or the cooldown ends.
        if not train.get('closed_at'):
            train['closed_at'] = item['updated']
        if signals.get('due') and now - train['closed_at'] >= TRAIN_COOLDOWN_SECONDS and not (installed and installed > train['closed_at']):
            train.clear()
    elif item and not bell and now - train['opened'] >= TRAIN_ESCALATE_SECONDS and not staged_since(runtime, train['opened']):
        hours = (now - installed) / 3600 if installed else None
        bell = create(room, 'user', train_text(signals, hours, escalation=True))
        train['escalation'] = bell['id']
        actions.append(('escalated', bell, 'user'))
    if not train and signals.get('due'):
        hours = (now - installed) / 3600 if installed else None
        item = create(room, RELEASE_GRANTEE, train_text(signals, hours))
        train.update(item=item['id'], opened=now)
        actions.append(('opened', item, RELEASE_GRANTEE))
    return actions


def train_text(signals, hours, escalation=False):
    visible = signals.get('visible') or []
    ids = ', '.join(t['id'] for t in visible[:8]) + (f' and {len(visible) - 8} more' if len(visible) > 8 else '')
    waited = f'{hours:.1f} h' if hours is not None else 'an unknown time'
    if escalation:
        return (f'E3: approved work you would see has waited {waited} since the last install and nothing is staged '
                f'({ids}). Ask the release manager to cut a release, or resolve this when you have.')
    return (f'{TRAIN_PREFIX} {len(visible)} approved user-visible tasks have waited {waited} since the last install '
            f'({ids}). Cut a release from what is approved now. This closes itself on the next install.')


def create(room, recipient, text):
    return room.records.followup(SYSTEM, 'create', assigned_to=recipient, text=text)['item']


def close(room, items, result, cancel=False):
    actions = []
    for item in items:
        if item and item['status'] == 'open':
            room.records.followup(SYSTEM, 'cancel' if cancel else 'resolve', followup_id=item['id'], result=result)
            actions.append(('closed', item, item['assigned_to']))
    return actions


def record(room, followup_id):
    return next((f for f in room.state.get('followups', []) if f['id'] == followup_id), None) if followup_id else None


def staged_since(runtime, since):
    """Only a validated successful stage receipt counts; a newer folder or a rehearsal never does (Astra 180bbb4b)."""
    query = getattr(getattr(runtime, 'release_manager', None), 'successful_stage_since', None)
    if not callable(query):
        return False
    try:
        return bool(query(runtime.identity, since))
    except Exception:
        LOGGER.exception('Stage receipt query failed; the release train escalates as not staged')
        return False


def announce(runtime, kind, item, recipient):
    """The dispatcher's tail (backend 2eab3be4): one system chat line, then the normal follow-up delivery."""
    channel = train_channel(runtime)
    item.setdefault('channel', channel)
    ref = {'collection': 'followups', 'id': item['id'], 'updated': item['updated'], 'action': kind}
    text = (f"Follow-up {item['id']} for {recipient}: {item['text']}" if kind != 'closed'
            else f"Follow-up {item['id']} closed by the room: {item['result']}")
    runtime.host.store.add(SYSTEM, text, channel=channel, metadata={'no_route': True, 'coordination_ref': ref})
    if kind != 'closed':
        runtime.deliver_record(recipient, [item], channel, None, text, ref)


def train_channel(runtime):
    """The manager check-in chat when the room still routes it, else the project's default chat, as release
    completions do."""
    channel = (getattr(getattr(runtime, 'manager_wakes', None), 'record', None) or {}).get('channel')
    if channel and runtime.owns_channel(channel) and runtime.decisions.routes(channel):
        return channel
    return runtime.default_channel()


def stamp(seconds):
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(seconds))
