"""Ready-task wakes: wake an idle owner once for authorised, actionable work it already owns.

Implements the approved contract exactly (room references r2.1 ea12acc9 and r2.2 9e1bad8b, security 5125bb26):
- Opt-in twice: the room limit `ready_task_wakes` (0 = off, default) and a task's owner- or user-set
  `next_action_state == 'ready'`, never inferred from text, status or labels.
- One wake per (task, owner, work_rev). work_rev advances only on external changes (project_room.track_work),
  so an owner's own edits cannot re-arm a consumed wake; initial readiness arms the current revision once.
- Check-and-claim is one synchronous step on the event loop, the same exclusion every task write has
  (ProjectRoom.task never awaits), and the claim is persisted before enqueue. A crash between the two loses
  that wake rather than duplicating it; it stays visible as status 'claimed'.
- Budget: the task's last recorded chain only. A missing or spent chain is refused, never replaced.
- A durable ceiling of 3 automatic wakes per agent per rolling hour; a refusal consumes that revision.
- Evaluated at turn end with an empty queue and on arming events only; no timer or polling.
- Re-checked in full at dispatch (accepts), including the switch and readiness, excluding the wake's own entry.
Stop, cancel, pause, admission, budgets and the error/blocked paths are unchanged and take precedence.
"""
import asyncio
import time

CEILING = 3
CEILING_SECONDS = 3600
CLAIMS_KEPT = 500
REASON = 'Ready-task wake (owner-marked ready; one per external change)'


class ReadyWakes:
    def __init__(self, runtime):
        self.runtime = runtime

    # Durable state lives in the project's coordination record, so restart and project switch keep it.
    @property
    def record(self):
        state = self.runtime.project_room.state
        record = state.setdefault('ready_wakes', {})
        for key in ('claims', 'fired', 'chains'):
            record.setdefault(key, {})
        return record

    def enabled(self):
        return self.runtime.project_room.limits.get('ready_task_wakes', 0) == 1

    def record_chain(self, task_id, chain):
        """The chain whose budget a later wake for this task uses (r2.1 point 5)."""
        if task_id and chain:
            self.record['chains'][task_id] = chain

    # Gates. Each returns '' when it passes, else the reason.
    def room_gate(self):
        r = self.runtime
        if r.paused:
            return 'room stopped'
        deployment = getattr(r, 'deployment', None)
        if deployment and (deployment.rehearsal or deployment.reload()['writes'] != 'open'):
            return 'deployment in progress or rehearsal'
        if not r.turn_admission_open():
            return 'turn admission closed'
        if not r.project_room.path:
            return 'no active project'
        return ''

    def task_gate(self, task):
        r, state = self.runtime, self.runtime.project_room.state
        owner = task.get('owner')
        if task.get('next_action_state') != 'ready':
            return 'not marked ready'
        if task.get('status') not in ('open', 'working'):
            return f"status {task.get('status')}"
        if not owner or owner not in r.states or r.states[owner].get('archived'):
            return 'owner is not an active agent'
        if task.get('transfer'):
            return 'pending transfer'
        if task.get('wait'):
            return 'waiting on a human request'
        done = {t['id'] for t in state['tasks'] if t['status'] == 'done'}
        if set(task.get('depends_on', [])) - done:
            return 'unfinished dependencies'
        if any(rv.get('task_id') == task['id'] and rv.get('status') == 'pending' and rv.get('requested_by') == owner
               for rv in state.get('reviews', [])):
            return 'waiting on a requested review'
        goal = r.goals.goal
        if goal and goal.get('status') == 'active' and goal.get('owner') == owner:
            return 'owner is leading an active goal'
        if getattr(r, 'repository_transition', None):
            return 'repository change in progress'
        return ''

    def agent_gate(self, name, own_entry=None):
        """Idle and available. At dispatch own_entry is the batch carrying this claim; it never counts (W2)."""
        r = self.runtime
        state = r.states.get(name, {})
        if state.get('paused') or state.get('archived') or state.get('state') in ('blocked', 'login_required'):
            return 'agent stopped, archived or unavailable'
        if own_entry is not None:
            return ''
        if name in r.running or state.get('waiting') or 'task' in state or name in r.deploy_held:
            return 'agent busy'
        queue = r.queues.get(name)
        if queue is not None and not queue.empty():
            return 'agent has queued work'
        return ''

    def budget_gate(self, task_id):
        pr = self.runtime.project_room
        identifier = self.record['chains'].get(task_id)
        if not identifier:
            return '', 'no recorded conversation for this task'
        chain = pr.chains.get(identifier)
        if not chain:
            return identifier, 'its conversation is no longer active'
        limits = pr.limits
        if (chain.get('paused') or (limits.get('agent_turns') and chain.get('turns', 0) >= limits['agent_turns'])
                or (limits.get('reported_token_budget') and chain.get('reported_tokens', 0) >= limits['reported_token_budget'])):
            return identifier, 'its conversation budget is spent'
        return identifier, ''

    def fired_recently(self, name, now):
        recent = [t for t in self.record['fired'].get(name, []) if now - t < CEILING_SECONDS]
        self.record['fired'][name] = recent
        return recent

    @staticmethod
    def key(task_id, owner, rev):
        return f'{task_id}:{owner}:{rev}'

    def history(self, task, text):
        """Visible on the task without touching 'updated', which pending record references compare."""
        task.setdefault('updates', []).append({'by': 'system', 'text': text, 'event': text, 'time': time.time(), 'auto': True})

    def event(self, kind, **data):
        try:
            asyncio.get_running_loop().create_task(self.runtime.emit('runtime_ready_wake', {'kind': kind, **data}, persist=True))
        except RuntimeError:
            pass  # No running loop (stand-in tests); the task history still records it.

    def settle(self, claim, task, status, reason=''):
        claim.update(status=status, reason=reason, at=time.time())
        self.history(task, f"ready wake {status} rev {claim['rev']}" + (f': {reason}' if reason else ''))
        self.event(status, task=claim['task'], owner=claim['owner'], rev=claim['rev'], reason=reason)

    def prune(self):
        claims = self.record['claims']
        if len(claims) > CLAIMS_KEPT:
            for old in sorted(claims, key=lambda k: claims[k].get('at', 0))[:len(claims) - CLAIMS_KEPT]:
                claims.pop(old, None)

    def schedule(self):
        """Claim and send at most one wake per idle agent. Synchronous: no other task write can interleave."""
        if not self.enabled() or self.room_gate():
            return []
        pr, r = self.runtime.project_room, self.runtime
        sent, now = [], time.time()
        claims = self.record['claims']
        tasks = sorted(pr.state['tasks'], key=lambda t: (t.get('priority', 3), t.get('updated', 0)))
        for task in tasks:
            owner = task.get('owner')
            if self.task_gate(task) or self.agent_gate(owner) or owner in sent:
                continue  # Not eligible or already covered by existing work: nothing sent, nothing claimed.
            rev = task.get('work_rev', 0)
            key = self.key(task['id'], owner, rev)
            if key in claims:
                continue  # This revision was consumed; only a new external change re-arms it.
            claim = {'task': task['id'], 'owner': owner, 'rev': rev, 'cause': task.get('work_cause', 'initial readiness'),
                     'status': 'claimed', 'at': now}
            claims[key] = claim
            chain, refused = self.budget_gate(task['id'])
            if not refused and len(self.fired_recently(owner, now)) >= CEILING:
                refused = f'{owner} already had {CEILING} automatic wakes in the last hour'
            if refused:
                self.settle(claim, task, 'refused', refused)
                continue
            claim['chain'] = chain
            self.record['fired'].setdefault(owner, []).append(now)
            pr.save()  # The claim is durable before the wake is queued.
            channel = pr.chains[chain]['channel']
            prompt = (f"{REASON}. Task {task['id']} (revision {rev}): {task['title']}\n"
                      f"Cause: {claim['cause']}.\nNext action on record: {task.get('next_action') or 'none recorded'}.\n"
                      "Continue it now, or record on the task what it is waiting for; set next_action_state to "
                      "waiting or deferred to stop these wakes.")
            queued = r.enqueue(owner, channel, prompt=prompt, chain=chain,
                               routing={'human': False, 'task_id': task['id'], 'ready_wake': key})
            if queued:
                self.settle(claim, task, 'sent')
                r.states[owner]['detail'] = f"Woken for ready task {task['id']}"
                sent.append(owner)
            else:
                self.settle(claim, task, 'refused', 'the wake could not be queued')
        self.prune()
        pr.save()
        return sent

    def cover(self, name, requests):
        """An admitted turn already carries work for these tasks (a delegation, review, follow-up or task start),
        so it covers their current revision: coalescing means no second wake for the same change."""
        state = self.runtime.project_room.state
        claims, now, changed = self.record['claims'], time.time(), False
        for request in requests:
            if request.get('ready_wake'):
                continue
            ref, ids = request.get('ref') or {}, set()
            if request.get('task_id'):
                ids.add(request['task_id'])
            if ref.get('collection') == 'tasks':
                ids.add(ref.get('id'))
            elif ref.get('collection') in ('reviews', 'followups'):
                item = next((x for x in state.get(ref['collection'], []) if x.get('id') == ref.get('id')), {})
                if item.get('task_id'):
                    ids.add(item['task_id'])
            for task in state['tasks']:
                if task['id'] in ids and task.get('owner') == name and task['status'] != 'done':
                    key = self.key(task['id'], name, task.get('work_rev', 0))
                    if key not in claims:
                        claims[key] = {'task': task['id'], 'owner': name, 'rev': task.get('work_rev', 0),
                                       'status': 'covered', 'reason': 'an existing wake carried this change', 'at': now}
                        changed = True
        if changed:
            self.runtime.project_room.save()

    def accepts(self, request):
        """Dispatch re-check (r2.1 point 6, r2.2 W1/W2). Ordinary requests always pass."""
        key = request.get('ready_wake')
        if not key:
            return True
        claim = self.record['claims'].get(key)
        if not claim or claim.get('status') != 'sent':
            return False
        pr = self.runtime.project_room
        task = next((t for t in pr.state['tasks'] if t['id'] == claim['task']), None)
        reason = ''
        if not self.enabled():
            reason = 'ready_task_wakes was turned off'
        elif not task:
            reason = 'task no longer exists'
        elif task.get('owner') != claim['owner']:
            reason = 'task owner changed'
        elif task.get('work_rev', 0) != claim['rev']:
            reason = 'task changed since the wake was claimed'
        else:
            reason = (self.task_gate(task) or self.room_gate() or self.agent_gate(claim['owner'], own_entry=request)
                      or self.budget_gate(task['id'])[1])
            if not reason and self.record['chains'].get(task['id']) != request.get('chain'):
                reason = "the task's conversation changed"
            if not reason and len(self.fired_recently(claim['owner'], time.time())) > CEILING:
                reason = 'automatic wake ceiling reached'
        if reason:
            reason = 'disabled before dispatch' if reason == 'not marked ready' or 'turned off' in reason else reason
            self.settle(claim, task or {'updates': []}, 'dropped', reason)
            pr.save()
            return False
        claim['status'] = 'dispatched'
        pr.save()
        return True
