"""Explicit shared goals. Event-driven continuation, never a background model poll."""
import json
import time
import uuid

from coordination_records import bounded
from durable import atomic_write


class RoomGoals:
    def __init__(self, runtime):
        self.runtime = runtime
        self.path = None
        self.goal = None
        self.seen = {}

    def load(self):
        self.path = self.runtime.project_room.path.with_name('goal.json')
        self.goal = json.loads(self.path.read_text('utf-8')) if self.path.exists() else None
        self.seen.clear()
        if self.goal and self.goal['status'] == 'active':
            self.pause('Server restarted; explicitly resume when ready.')

    def save(self):
        if self.goal:
            chain = self.runtime.project_room.chains.get(self.goal['chain'])
            if chain:
                self.goal['usage'] = {k: chain.get(k, 0) for k in ('turns', 'handoffs', 'reported_tokens', 'input_chars', 'paused')}
        content = json.dumps(self.goal, ensure_ascii=False)
        atomic_write(self.path, lambda stream: stream.write(content))

    def pause(self, reason):
        if self.goal and self.goal['status'] == 'active':
            self.goal.update(status='paused', evidence=reason, updated=time.time())
            self.goal['revision'] += 1
            self.save()

    def change(self, actor, action='status', goal_id='', evidence='', text='', owner='', channel='', max_rounds=12):
        if action == 'status':
            return self.goal
        human = actor == 'user'
        if not human and action not in ('complete', 'block'):
            raise ValueError('Agents may report completion or a blocker; only the user starts or resumes a goal.')
        if action == 'start':
            if self.goal and self.goal['status'] in ('active', 'paused', 'blocked'):
                raise ValueError('Complete or cancel the current goal first.')
            text = str(text).strip()
            if not text or len(text) > 2000:
                raise ValueError('Provide a goal of 1 to 2000 characters, including its done criteria.')
            if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or not 1 <= max_rounds <= 100:
                raise ValueError('Choose 1 to 100 goal rounds.')
            state = self.runtime.states.get(owner, {})
            if not state or state.get('archived'):
                raise ValueError('Choose an existing agent to lead the goal.')
            channel = channel or self.runtime.default_channel()
            if not self.runtime.owns_channel(channel):
                raise ValueError('Choose a chat in the current project.')
            self.goal = dict(id=uuid.uuid4().hex[:12], text=text, owner=owner, channel=channel,
                             status='active', evidence='', rounds=0, max_rounds=max_rounds,
                             chain=self.runtime.project_room.new_chain(channel), revision=1, updated=time.time())
        else:
            if not self.goal or goal_id != self.goal['id']:
                raise ValueError('Goal changed; read the current goal before updating it.')
            if self.goal['status'] in ('complete', 'cancelled'):
                raise ValueError('This goal is already closed.')
            if not human and actor != self.goal['owner']:
                raise ValueError('The goal lead records completion or blockers; coordinate with them.')
            evidence = bounded(evidence, 'evidence')
            if action in ('complete', 'block') and not evidence:
                raise ValueError('Provide completion evidence or the concrete blocker.')
            states = dict(complete='complete', block='blocked', pause='paused', resume='active', cancel='cancelled')
            if action not in states:
                raise ValueError('Choose status, start, pause, resume, complete, block or cancel.')
            if action == 'resume':
                if self.goal['rounds'] >= self.goal['max_rounds']:
                    raise ValueError('Goal round budget reached. Start a new explicitly scoped goal.')
                # Restore persisted counters, never create a fresh budget implicitly.
                if self.goal['chain'] not in self.runtime.project_room.chains:
                    if 'usage' not in self.goal:
                        raise ValueError('Saved budget unavailable. Cancel and start a new scoped goal.')
                    self.runtime.project_room.chains[self.goal['chain']] = {
                        'id': self.goal['chain'], 'channel': self.goal['channel'], **self.goal['usage']}
            self.goal.update(status=states[action], evidence=evidence, updated=time.time())
            self.goal['revision'] += 1
        self.save()
        return self.goal

    def accepts(self, request):
        token = request.get('goal_token')
        return not token or bool(self.goal and self.goal['status'] == 'active' and token == self.token())

    def token(self):
        return f"{self.goal['id']}:{self.goal['revision']}"

    def schedule(self):
        r, g = self.runtime, self.goal
        if not g or g['status'] != 'active':
            return
        self.save()
        state = r.states.get(g['owner'], {})
        if r.paused or state.get('paused') or state.get('archived') or not state:
            self.pause('Room or goal lead stopped or unavailable.')
            return
        if state.get('state') in ('blocked', 'login_required'):
            self.pause('Goal lead needs attention before continuing.')
            return
        # Human steering, outstanding peer work and existing queues take precedence.
        if r.running or any('task' in s or s.get('waiting') for s in r.states.values()) or any(not q.empty() for q in r.queues.values()):
            return
        chain = r.project_room.chains.get(g['chain'])
        limits = r.project_room.limits
        if (not chain or chain.get('paused') or g['rounds'] >= g['max_rounds']
                or (limits.get('agent_turns') and chain.get('turns', 0) >= limits['agent_turns'])
                or (limits.get('reported_token_budget') and chain.get('reported_tokens', 0) >= limits['reported_token_budget'])):
            self.pause('Goal or conversation budget reached; inspect progress before starting another goal.')
            return
        prompt = f"Continue shared goal {g['id']}. Read room_goal(status) if needed. Take the next concrete step; use room_goal(complete, goal_id, evidence) only when achieved, or block with the concrete obstacle."
        if r.enqueue(g['owner'], g['channel'], prompt=prompt, chain=g['chain'], routing={'human': False, 'goal_token': self.token()}):
            # A new explicit goal round gets a bounded peer handoff allowance.
            # Goal rounds and cumulative turns/tokens still bound total work.
            chain['handoffs'] = 0
            g['rounds'] += 1
            self.save()
        else:
            self.pause('Could not enqueue the goal lead.')

    def context(self, run):
        if not self.goal or self.goal['status'] not in ('active', 'blocked', 'paused'):
            return ''
        key = self.token()
        session = getattr(run, 'session_key', run.name)
        if run.previous_session and self.seen.get(session) == key:
            return ''
        self.seen[session] = key
        g = self.goal
        return '\nShared goal: ' + json.dumps({k: g[k] for k in ('id', 'text', 'owner', 'status')}, ensure_ascii=False) + '\nUse room_goal for status, completion evidence or blockers. User steering and stop controls take precedence.'
