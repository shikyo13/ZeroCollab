"""Work continuity: the room keeps work moving itself (Docs/work-continuity.md r3, task c54e453b, Zero #16289).

- C2, continue: an agent whose last turn made progress on a task it owns, still `working` with a next action and
  not marked waiting, gets its next turn on that task.
- C3, unanswered asks: an open follow-up or review request for an idle agent that arrived after its last turn
  started wakes it once, through the normal record delivery. notify=false means "not now", never "never".
- C5, watchdog: an agent idle for IDLE_WAKE_SECONDS with ready working work is woken once per idle stretch, and
  the lead is woken when the whole team is idle while ready work waits.
- C4, visible stall: a wake refused by a ceiling or a spent budget, retries exhausted with no turn starting, an
  agent spinning (continued SPIN_TURNS times with no status, evidence or file change) or still idle after its
  watchdog wake marks the task needs_attention and opens one `system` item for the lead (the user's bell when the
  lead is the stalled agent or none is chosen). It closes itself when the agent moves the task again.

Bounds (security c334015c K1-K4, backend 558e35bd): continuations use the ending turn's own conversation and
never create one (K1); only changes to that task count as progress (K2); asks have their own per-recipient cap,
one outstanding ask-wake per recipient, and `user` and `system` are never woken (K3). Stop, pause, held writes,
turn admission, a busy or stopped agent and conversation budgets hold everything here; a held wake is retried,
never consumed. Claims are saved before anything is queued, so a crash re-sends at most MAX_SENDS times and then
surfaces as a stall instead of staying silently sent.
"""
import logging
import time

from process_enforcement import SYSTEM, announce

LOGGER = logging.getLogger(__name__)
TICK_SECONDS = 15                # evaluation cadence on the beat
CONTINUE_GRACE_SECONDS = 10      # after a turn ends, let a reply or queued request land first
ASK_GRACE_SECONDS = 120          # an ask waits this long for its normal delivery first
IDLE_WAKE_SECONDS = 600          # C5: idle this long with ready work, the agent is woken; again, the lead is told
TEAM_IDLE_SECONDS = 120          # C5: everyone idle this long while ready work waits, the lead is woken
CONTINUE_CEILING = 8             # C2 and C5 wakes per agent per rolling hour
ASK_CEILING = 4                  # C3 wakes per recipient per rolling hour, separate from CONTINUE_CEILING (K3)
CEILING_SECONDS = 3600
RETRY_SECONDS = 180              # a sent wake that started no turn in this time is sent again
MAX_SENDS = 2                    # per wake; then it is a visible stall, never silently 'sent' forever
SPIN_TURNS = 3                   # continued turns in a row with no status, evidence or file change
CLAIMS_KEPT = 500
WAITING_STATES = ('waiting', 'deferred')
SUBSTANTIVE = {'status', 'evidence'}
STALL_PREFIX = 'Stalled work:'   # with the agent and task, how the room recognises its own item after a crash


# Turn facts, recorded by the runtime as a turn runs and when it ends.

def mark_task(run, before, after):
    """A room_task call by this turn changed a task it owns: record which progress kinds (K2)."""
    if not after or after.get('owner') != run.name:
        return
    before = before or {}
    kinds = {field for field in ('status', 'next_action', 'evidence') if before.get(field) != after.get(field)}
    if len(after.get('updates') or []) > len(before.get('updates') or []):
        kinds.add('checkin')
    if kinds:
        run.progress.setdefault(after['id'], set()).update(kinds)


def mark_record(run, room, task_id):
    """A review or follow-up this turn requested, answered or resolved, linked to a task it owns."""
    task = next((t for t in room.state['tasks'] if t['id'] == task_id), None) if task_id else None
    if task and task.get('owner') == run.name:
        run.progress.setdefault(task_id, set()).add('record')


def finished(runtime, run, outcome):
    """The end of run_turn: remember the turn, settle the wakes it answered, and count spinning."""
    room = runtime.project_room
    if room.path is None:
        return
    state = store(room)
    owned = {t['id'] for t in room.state['tasks'] if t.get('owner') == run.name and t['status'] != 'done'}
    progress = {tid: sorted(kinds) for tid, kinds in run.progress.items() if tid in owned}
    now = time.time()
    state['agents'][run.name] = {'run': run.id, 'started': run.started_at or now, 'ended': now, 'outcome': outcome,
                                 'progress': progress, 'files': run.file_changes,
                                 'chain': (run.entry or {}).get('chain'), 'channel': run.channel}
    for claim in state['claims'].values():
        if claim['agent'] == run.name and claim['status'] != 'done':
            claim.update(status='done', at=now)  # a turn ran: every wake waiting for this agent is answered
    continued = next((q['continuity'] for q in run.entry.get('requests', [])
                      if (q.get('continuity') or '').startswith('continue:')), '')
    if continued:
        task_id = continued.split(':')[2]
        substantive = run.file_changes or SUBSTANTIVE & set(progress.get(task_id, ()))
        state['spin'][task_id] = 0 if substantive else state['spin'].get(task_id, 0) + 1
    for task_id in progress:
        if not state['spin'].get(task_id):  # a spinning continuation is not "moving again"
            clear_stall(runtime, room, state, task_id, f'{run.name} moved it again.')
    room.save()


# The beat.

def tick(runtime, now=None):
    """Called from the runtime beat. Returns what it did, or None when it didn't run (rate limit or a held room)."""
    now = time.time() if now is None else now
    if now - getattr(runtime, 'continuity_ticked', 0) < TICK_SECONDS:
        return None
    runtime.continuity_ticked = now
    room = runtime.project_room
    if room.path is None or not runtime.ready_wakes.enabled() or runtime.ready_wakes.room_gate():
        return None  # Stop, pause, held writes or closed admission: nothing sent, nothing consumed
    deployment = getattr(runtime, 'deployment', None)
    if deployment is not None and not deployment.enter_mutation():
        return None
    try:
        state = store(room)
        state.setdefault('since', now)  # a first enable re-arms from now, never replays history in one burst
        actions = evaluate(runtime, room, state, now)
        prune(state)
        room.save()
        for action in actions:
            if action[0] in ('opened', 'closed'):
                announce(runtime, *action)
        return [action[0] for action in actions]
    finally:
        if deployment is not None:
            deployment.leave_mutation()


def evaluate(runtime, room, state, now):
    actions, idle, busy = [], [], False
    for name in sorted(runtime.states):
        if name == SYSTEM or runtime.states[name].get('archived'):
            continue
        gate = runtime.ready_wakes.agent_gate(name)
        if gate:
            busy = busy or gate in ('agent busy', 'agent has queued work')
            continue  # busy, queued, stopped or unavailable: nothing to do now, nothing consumed
        last = state['agents'].get(name) or {}
        idle.append((name, now - max(last.get('ended', 0), state['since'])))
        for wake in (continuation(runtime, state, name, last, now), unseen_ask(runtime, state, name, last, now),
                     watchdog(runtime, state, name, last, now)):
            if wake and send(runtime, room, state, name, wake, now, actions):
                break
    if not busy:
        team_wake(runtime, room, state, idle, now, actions)
    stalls(runtime, room, state, now, actions)
    return actions


def send(runtime, room, state, name, wake, now, actions):
    """One wake through its claim. True when something was queued for this agent."""
    key, kind, task_id, prompt, chain, channel, record = wake
    claim = state['claims'].setdefault(key, {'agent': name, 'task': task_id, 'kind': kind, 'sends': 0,
                                             'status': 'due', 'at': now})
    if claim['status'] in ('done', 'exhausted'):
        return False
    if claim['status'] == 'sent':
        if now - claim['at'] < RETRY_SECONDS:
            return False
        if claim['sends'] >= MAX_SENDS:
            claim.update(status='exhausted', at=now, reason=f'{MAX_SENDS} wakes sent and no turn started')
            return False
    ceiling, fired = (ASK_CEILING, 'asked') if kind == 'ask' else (CONTINUE_CEILING, 'fired')
    if len(recent(state, fired, name, now)) >= ceiling:
        return hold(claim, now, f'{ceiling} automatic {"ask wakes" if kind == "ask" else "turns"} in the last hour')
    if kind != 'ask' and not budget_open(room, chain):
        return hold(claim, now, 'its conversation is gone, paused or spent (no new budget is created)')
    claim.update(status='sent', at=now, sends=claim['sends'] + 1, reason='')
    state[fired].setdefault(name, []).append(now)
    room.save()  # durable before anything is queued; a lost enqueue is re-sent after RETRY_SECONDS
    if kind == 'ask':
        collection, item = record
        ref = {'collection': collection, 'id': item['id'], 'updated': item.get('updated', item['created']),
               'action': 'continuity'}
        queued = runtime.deliver_record(name, [item], channel, item.get('chain'), prompt, ref).get('queued')
    else:
        queued = runtime.enqueue(name, channel, prompt=prompt, chain=chain,
                                 routing={'human': False, 'task_id': task_id, 'continuity': key})
    if not queued:
        return hold(claim, now, 'the wake could not be queued')
    runtime.states[name]['detail'] = f"{'Continuing' if kind == 'continue' else 'Woken for'} {task_id or 'an ask'}"
    actions.append(('woke', key, name))
    return True


def hold(claim, now, reason):
    if claim['status'] != 'held':
        claim['held_since'] = now
    claim.update(status='held', at=now, reason=reason)
    return False


# The three wakes. Each returns (key, kind, task, prompt, chain, channel, record) or None.

def continuation(runtime, state, name, last, now):
    """C2: the last turn completed with progress on an owned task that is still working and ready."""
    if last.get('outcome') != 'completed' or not last.get('progress') or now - last['ended'] < CONTINUE_GRACE_SECONDS:
        return None
    tasks = {t['id']: t for t in runtime.project_room.state['tasks']}
    ready = sorted((tasks[i] for i in last['progress'] if i in tasks and not task_hold(runtime, tasks[i], name)
                    and state['spin'].get(i, 0) < SPIN_TURNS),
                   key=lambda t: (t.get('priority', 3), -t.get('updated', 0)))
    if not ready:
        return None
    task = ready[0]
    prompt = (f"Work continuity: your last turn made progress on task {task['id']} ({task['title']}), which is still "
              f"working. Its next action, as you recorded it: {quoted(task)}\nContinue it now. If it is waiting on a "
              'person or an event, record that on the task (a wait, or next_action_state waiting or deferred with the '
              'reason) and the room stops continuing it.')
    return (f"continue:{last['run']}:{task['id']}", 'continue', task['id'], prompt, last.get('chain'),
            last.get('channel') or runtime.default_channel(), None)


def unseen_ask(runtime, state, name, last, now):
    """C3: the oldest open ask for this agent that changed after its last turn started and has waited ASK_GRACE."""
    if any(c['agent'] == name and c['kind'] == 'ask' and c['status'] == 'sent' for c in state['claims'].values()):
        return None  # one outstanding ask-wake per recipient (K3)
    room_state, seen = runtime.project_room.state, last.get('started', state['since'])
    asks = [('followups', f) for f in room_state.get('followups', [])
            if f['status'] == 'open' and f['assigned_to'] == name and f['created_by'] != name]
    asks += [('reviews', r) for r in room_state.get('reviews', []) if r['status'] == 'pending' and r['reviewer'] == name]
    due = sorted((changed(item), kind, item) for kind, item in asks
                 if changed(item) > max(seen, state['since']) and now - changed(item) >= ASK_GRACE_SECONDS)
    if not due:
        return None
    stamp, kind, item = due[0]
    noun = 'Follow-up' if kind == 'followups' else 'Review request'
    prompt = (f"Work continuity: {noun.lower()} {item['id']} for you is unanswered (task {item.get('task_id') or 'none'}). "
              'Read room_read(section="inbox") and act on it, or answer it with what it is waiting for.')
    channel = item.get('channel') or last.get('channel') or runtime.default_channel()
    return f"ask:{item['id']}:{stamp}", 'ask', item.get('task_id', ''), prompt, None, channel, (kind, item)


def watchdog(runtime, state, name, last, now):
    """C5: idle IDLE_WAKE_SECONDS while owning ready working work, whatever its last turn did."""
    since = max(last.get('ended', 0), state['since'])
    if now - since < IDLE_WAKE_SECONDS:
        return None
    task = next(iter(ready_tasks(runtime, name)), None)
    if not task:
        return None
    chain = last.get('chain') or runtime.ready_wakes.record['chains'].get(task['id'])
    prompt = (f"Work continuity: you have been idle for {int((now - since) / 60)} minutes while task {task['id']} "
              f"({task['title']}) is working with a next action, as recorded: {quoted(task)}\nContinue it now, or record "
              'on the task what it is waiting for (a wait, or next_action_state waiting or deferred with the reason).')
    return (f"idle:{name}:{last.get('run') or 'start'}", 'idle', task['id'], prompt, chain,
            last.get('channel') or runtime.default_channel(), None)


def team_wake(runtime, room, state, idle, now, actions):
    """C5: nobody busy, every available agent idle TEAM_IDLE_SECONDS, and ready work waits: wake the lead once."""
    lead = lead_of(runtime)
    names = [n for n, _ in idle]
    if (not lead or lead not in names or min(seconds for _, seconds in idle) < TEAM_IDLE_SECONDS
            or any(action[0] == 'woke' for action in actions)):
        return  # someone is already being woken this pass; the team isn't idle any more
    waiting = [t for n in names for t in ready_tasks(runtime, n)]
    if not waiting:
        return
    stretch = max([a.get('ended', 0) for a in state['agents'].values()] + [state['since']])
    last = state['agents'].get(lead) or {}
    listed = ', '.join(f"{t['id']} ({t['owner']})" for t in waiting[:8])
    prompt = (f'Work continuity: the whole team has been idle for at least {TEAM_IDLE_SECONDS // 60} minutes while '
              f'ready work waits: {listed}. Get it moving: wake its owners, reassign it, or record what it waits for.')
    send(runtime, room, state, lead, (f'team:{int(stretch)}', 'team', waiting[0]['id'], prompt, last.get('chain'),
                                      last.get('channel') or runtime.default_channel(), None), now, actions)


# C4: stalls are visible on the task and in the lead's inbox, and close themselves.

def stalls(runtime, room, state, now, actions):
    for key, claim in list(state['claims'].items()):
        task_id, name = claim.get('task'), claim['agent']
        task = next((t for t in room.state['tasks'] if t['id'] == task_id), None) if task_id else None
        if not task or claim['kind'] in ('ask', 'team'):
            continue
        reason = ''
        if claim['status'] == 'exhausted':
            reason = claim['reason']
        elif claim['status'] == 'held' and now - claim.get('held_since', now) >= IDLE_WAKE_SECONDS:
            reason = f"its wake is held: {claim['reason']}"
        elif claim['kind'] == 'idle' and claim['status'] == 'sent' and now - claim['at'] >= IDLE_WAKE_SECONDS:
            reason = f'still idle {IDLE_WAKE_SECONDS // 60} minutes after the room woke it'
        if reason and not task_hold(runtime, task, name):
            open_stall(runtime, room, state, task, name, reason, actions)
    for task_id, count in state['spin'].items():
        task = next((t for t in room.state['tasks'] if t['id'] == task_id), None)
        if task and count >= SPIN_TURNS and not task_hold(runtime, task, task['owner']):
            open_stall(runtime, room, state, task, task['owner'],
                       f'{count} continued turns with no status, evidence or file change', actions)
    for task_id in list(state['stalls']):
        task = next((t for t in room.state['tasks'] if t['id'] == task_id), None)
        if not task or task_hold(runtime, task, task.get('owner')):
            state['spin'].pop(task_id, None)
            clear_stall(runtime, room, state, task_id, 'It is no longer ready work.', actions)


def open_stall(runtime, room, state, task, name, reason, actions):
    if task['id'] in state['stalls']:
        return
    lead = lead_of(runtime)
    recipient = lead if lead and lead != name else 'user'
    prefix = f"{STALL_PREFIX} {name} {task['id']}"
    item = next((f for f in room.state.get('followups', []) if f['status'] == 'open' and f['created_by'] == SYSTEM
                 and f['assigned_to'] == recipient and f['text'].startswith(prefix)), None)  # adopt after a crash
    if item is None:
        text = (f"{prefix}: {name}'s task {task['id']} ({task['title']}) is ready work that isn't moving: {reason}. "
                "Wake or redirect them, reassign it, or record what it waits for. This closes itself when it moves.")
        item = room.records.followup(SYSTEM, 'create', task_id=task['id'], assigned_to=recipient, text=text)['item']
        actions.append(('opened', item, recipient))
    state['stalls'][task['id']] = {'item': item['id'], 'agent': name, 'reason': reason}
    task['stall'] = {'reason': reason, 'item': item['id']}


def clear_stall(runtime, room, state, task_id, result, actions=None):
    stall = state['stalls'].pop(task_id, None)
    task = next((t for t in room.state['tasks'] if t['id'] == task_id), None)
    if task:
        task.pop('stall', None)
    item = next((f for f in room.state.get('followups', []) if stall and f['id'] == stall['item']), None)
    if item and item['status'] == 'open':
        room.records.followup(SYSTEM, 'resolve', followup_id=item['id'], result=result)
        if actions is not None:
            actions.append(('closed', item, item['assigned_to']))
        else:
            announce(runtime, 'closed', item, item['assigned_to'])


# Shared checks.

def task_hold(runtime, task, name):
    """Why this task is not ready work for its owner, or '' when it is (the ready_wakes gates, plus C2's rule)."""
    state = runtime.project_room.state
    if not name or task.get('owner') != name or task.get('status') != 'working':
        return 'not working for this owner'
    if not (task.get('next_action') or '').strip():
        return 'no next action recorded'
    if task.get('next_action_state') in WAITING_STATES or task.get('wait') or task.get('transfer'):
        return 'marked waiting'
    done = {t['id'] for t in state['tasks'] if t['status'] == 'done'}
    if set(task.get('depends_on', [])) - done:
        return 'unfinished dependencies'
    if any(r.get('task_id') == task['id'] and r.get('status') == 'pending' and r.get('requested_by') == name
           for r in state.get('reviews', [])):
        return 'waiting on a requested review'
    goal = runtime.goals.goal
    if goal and goal.get('status') == 'active' and goal.get('owner') == name:
        return 'leading an active goal'
    return ''


def ready_tasks(runtime, name):
    tasks = [t for t in runtime.project_room.state['tasks'] if t.get('owner') == name and not task_hold(runtime, t, name)]
    return sorted(tasks, key=lambda t: (t.get('priority', 3), -t.get('updated', 0)))


def budget_open(room, chain):
    """The conversation exists and isn't paused or spent. A missing one is never replaced (K1)."""
    record = room.chains.get(chain) if chain else None
    if not record or record.get('paused'):
        return False
    limits = room.limits
    if limits.get('agent_turns') and record.get('turns', 0) >= limits['agent_turns']:
        return False
    return not (limits.get('reported_token_budget') and record.get('reported_tokens', 0) >= limits['reported_token_budget'])


def lead_of(runtime):
    lead = (runtime.project_room.state.get('responders') or {}).get('lead')
    return lead if lead in runtime.states and not runtime.states[lead].get('archived') else None


def quoted(task):
    """The next action as recorded data, never as the room's own instruction (design r3, optional point)."""
    who = next((u['by'] for u in reversed(task.get('updates') or []) if u.get('by')), task.get('owner'))
    return f'"{task["next_action"].strip()[:600]}" (last update by {who})'


def changed(item):
    return item.get('updated', item['created'])


def store(room):
    state = room.state.setdefault('continuity', {})
    for key in ('agents', 'claims', 'fired', 'asked', 'spin', 'stalls'):
        state.setdefault(key, {})
    return state


def recent(state, kind, name, now):
    fired = state[kind]
    fired[name] = [t for t in fired.get(name, []) if now - t < CEILING_SECONDS]
    return fired[name]


def prune(state):
    claims = state['claims']
    if len(claims) > CLAIMS_KEPT:
        for key in sorted(claims, key=lambda k: claims[k].get('at', 0))[:len(claims) - CLAIMS_KEPT]:
            claims.pop(key, None)
