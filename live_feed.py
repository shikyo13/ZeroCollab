"""Live view feed (task b1af7a3a, Zero #14152): who is working with whom, on what, with which artifact.

The Board is organised around tasks; Live is organised around people. This module derives a people-centred
view from events the room already has (no provider calls, no new authority) and keeps two small rings so a
freshly opened Live view is never empty. Contract pinned with motion-design (follow-up ec632edd):

  GET /api/live?project=<id>  ->  {project_id, at, agents: [agent], interactions: [interaction]}
  WebSocket {type: 'live', data: {kind: 'agent', agent} | {kind: 'interaction', interaction}}

  agent: {id, name, order, state: working|waiting_on_user|failed|stopped|idle, reason, activity,
          task_id, task_title, since, artifact|null}
  artifact: {url, kind: screenshot|markup|upload|browser_frame, task_id, at, source_url?, page?, frame_at?}
  interaction: {id, at, channel, from, to, kind, task_id, task_title, record_id, text, from_owner?, to_owner?}
    kind: mention|handoff|review_request|review_verdict|followup|followup_result|decision_turn|question|answer
    from/to are agent ids, or the sentinels 'user' (Zero), 'team' (an @all), 'room' (the decision flow,
    sender of decision_turn) and 'unassigned' (a handoff from no owner).
    An unanswered question that expires gets an answer with text 'No answer (expired)'.
  browser_frame artifacts: url carries the page id (&page=); page holds that page's URL, for provenance.
  agent.order is the roster index, archived members included, so it can have gaps; sort by it.
  failed: the agent's state is blocked or signed out (a final failure sets blocked before runtime_end, with the
    reason Team shows). Automatic check-ins and a first startup retry stay ready by design, so they read idle.

Sealed decision turns stay sealed: their activity is "working on a team decision", they publish no artifact,
and decision_turn interactions carry no text. Hooks in room_runtime call observe(), woken(), recorded() and
artifact(); each returns the deltas to broadcast.
"""
import asyncio
import collections
import json
import time

RING = 200
TEXT = 140
SKIP_WAKES = ('ready_wake', 'goal_token', 'manager_audit', 'team_builder')


def clip(text):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= TEXT else text[:TEXT - 1] + '…'


class LiveFeed:
    def __init__(self, runtime):
        self.runtime = runtime
        self.ring = collections.deque(maxlen=RING)
        self.ids = set()
        self.artifacts, self.activity, self.since, self.sent = {}, {}, {}, {}
        self.questions = {}  # approval id -> agent, for the matching answer
        self.owners = None   # task id -> owner at the last board snapshot (None until the first one)

    # ---- views ---------------------------------------------------------------------------------

    def task_title(self, task_id):
        task = next((t for t in self.runtime.project_room.state.get('tasks', []) if t['id'] == task_id), None)
        return task['title'] if task else ''

    def current_task(self, name):
        """A running turn's focus: the same task the Board places its speech on, or '' when it is not tied to one
        (task b5526ef8, frontend 7e85bd4c). Without a running turn, the single task this agent owns that is working."""
        run = self.runtime.running.get(name)
        if run is not None and hasattr(run, 'focus'):
            return run.task_id or ''
        explicit = sorted(getattr(run, 'task_ids', ()) or ()) if run else []
        if len(explicit) == 1:
            return explicit[0]
        working = [t['id'] for t in self.runtime.project_room.state.get('tasks', [])
                   if t.get('owner') == name and t.get('status') == 'working']
        return working[0] if len(working) == 1 else ''

    def agent(self, name):
        r = self.runtime
        s = r.states[name]
        run = r.running.get(name)
        reason = ''
        if s.get('paused') or r.paused:
            state = 'stopped'
        elif any(a['display'].get('agent') == name for a in r.approvals.values()):
            state = 'waiting_on_user'
        elif s.get('state') in ('blocked', 'login_required'):
            state = 'failed'
            reason = (f'{name} is signed out. Sign in on Team > Providers, then click Refresh connections.'
                      if s['state'] == 'login_required' else r.plain_failure(name, s.get('detail') or ''))
        elif run:
            state = 'working'
        else:
            state = 'idle'
        if self.since.get(name, (None,))[0] != state:
            self.since[name] = (state, time.time())
        sealed = bool(run and getattr(run, 'sealed', False))
        task_id = '' if sealed else self.current_task(name)
        return {'id': name, 'name': s.get('label') or name, 'order': list(r.states).index(name), 'state': state,
                'reason': reason,
                'activity': ('working on a team decision' if sealed else self.activity.get(name, ''))
                            if state in ('working', 'waiting_on_user') else '',
                'task_id': task_id, 'task_title': self.task_title(task_id) if task_id else '',
                'since': self.since[name][1], 'artifact': self.artifacts.get(name)}

    def snapshot(self):
        r = self.runtime
        return {'project_id': r.identity or '', 'at': time.time(),
                'agents': [self.agent(n) for n in r.states if not r.states[n].get('archived')],
                'interactions': list(self.ring)}

    # ---- deltas --------------------------------------------------------------------------------

    def agent_delta(self, name):
        if name not in self.runtime.states or self.runtime.states[name].get('archived'):
            return []
        view = self.agent(name)
        if self.sent.get(name) == view:
            return []
        self.sent[name] = view
        return [{'kind': 'agent', 'agent': view}]

    def interaction(self, ident, kind, sender, to, channel='', task_id='', record_id='', text='', **extra):
        if ident in self.ids:
            return []
        if len(self.ring) == self.ring.maxlen:
            self.ids.discard(self.ring[0]['id'])
        self.ids.add(ident)
        item = {'id': ident, 'at': time.time(), 'channel': channel or '', 'from': sender, 'to': to, 'kind': kind,
                'task_id': task_id or '', 'task_title': self.task_title(task_id) if task_id else '',
                'record_id': record_id or '', 'text': clip(text), **extra}
        self.ring.append(item)
        return [{'kind': 'interaction', 'interaction': item}]

    def observe(self, kind, data):
        """An event the room emitted (RoomRuntime.emit)."""
        if not isinstance(data, dict):
            return []
        if kind == 'runtime_tool':
            name = data.get('agent')
            if data.get('status') != 'note' and data.get('label') and name:
                self.activity[name] = data['label']
                return self.agent_delta(name)
            return []
        if kind == 'runtime_focus':
            return self.agent_delta(data.get('agent'))
        if kind in ('runtime_start', 'runtime_end'):
            name = data.get('agent')
            if kind == 'runtime_start':
                self.activity[name] = 'starting'
            else:
                self.activity.pop(name, None)
            return self.agent_delta(name)
        if kind == 'runtime_state':
            return [d for name in self.runtime.states for d in self.agent_delta(name)]
        if kind == 'runtime_approval':
            name = data.get('agent')
            out = self.agent_delta(name)
            if data.get('question'):
                self.questions[data['id']] = name
                params = data.get('params') or {}
                asked = params.get('message') or next((q.get('question') for q in params.get('questions') or []
                                                      if isinstance(q, dict) and q.get('question')), '') or data.get('reason')
                out += self.interaction(f"question:{data['id']}", 'question', name, 'user', data.get('channel'),
                                        record_id=data['id'], text=asked, task_id=self.current_task(name))
            return out
        if kind == 'runtime_decision':
            name = self.questions.pop(data.get('id'), None)
            out = self.interaction(f"answer:{data['id']}", 'answer', 'user', name, record_id=data['id']) if name else []
            return out + (self.agent_delta(name) if name else [])
        if kind == 'runtime_approval_resolved':
            # A question nobody answered (ask() times out after 120 s) is closed too, or Live would show it open
            # forever (motion-design 766d3678 F1). An answered one already left self.questions in runtime_decision.
            name = self.questions.pop(data.get('id'), None)
            out = self.interaction(f"answer:{data['id']}", 'answer', 'user', name, record_id=data['id'],
                                   text='No answer (expired)') if name else []
            return out + [d for agent in self.runtime.states for d in self.agent_delta(agent)]
        if kind == 'project_state':
            return self.handoffs(data.get('tasks') or [])
        if kind == 'browser_activity':
            activity = data.get('activity') or {}
            name = activity.get('label')
            run = self.runtime.running.get(name)
            if activity.get('phase') != 'done' or not run or getattr(run, 'sealed', False) \
                    or activity.get('action') in ('status', 'claim', 'release', 'screenshot'):
                return []
            page = activity.get('page') or ''
            url = f"/api/runtime/browser/frame?project={self.runtime.identity}" + (f'&page={page}' if page else '')
            return self.set_artifact(name, {'url': url, 'kind': 'browser_frame', 'page': data.get('url') or '',
                                            'frame_at': time.time()})
        return []

    def handoffs(self, tasks):
        owners = {t['id']: t.get('owner') or '' for t in tasks if isinstance(t, dict) and 'id' in t}
        previous, self.owners = self.owners, owners
        if previous is None:
            return []  # the first snapshot is the baseline
        out = []
        for task_id, owner in owners.items():
            before = previous.get(task_id)
            if before is not None and before != owner and owner:
                out += self.interaction(f'handoff:{task_id}:{before}:{owner}:{int(time.time())}', 'handoff',
                                        before or 'unassigned', owner, task_id=task_id, record_id=task_id,
                                        from_owner=before, to_owner=owner)
        return out

    def woken(self, name, channel, request):
        """A wake the room queued (RoomRuntime.enqueue): who asked whom, about what."""
        if request.get('ref') or any(request.get(k) for k in SKIP_WAKES):
            return []  # coordination records are reported by recorded(); system wakes are not interactions
        token = request.get('decision')
        if token:
            ident = f"decision:{token.get('id')}:{token.get('stage')}:{token.get('revision')}:{token.get('epoch')}:{name}"
            return self.interaction(ident, 'decision_turn', 'room', name, channel, record_id=token.get('id', ''))
        message_id = request.get('message_id')
        if message_id is not None:
            message = self.runtime.host.store.get_by_id(message_id)
            if not message:
                return []
            decisions = getattr(self.runtime, 'decisions', None)
            hidden = False
            if decisions is not None:
                try:
                    hidden = decisions.hidden(message, '')
                except Exception:
                    hidden = True
            to = 'team' if request.get('broadcast') else name
            sender = message.get('sender') or 'user'
            return self.interaction(f'mention:{message_id}:{to}', 'mention', sender, to, channel,
                                    task_id=request.get('task_id', ''), record_id=str(message_id),
                                    text='' if hidden else message.get('text', ''))
        if request.get('task_id') and request.get('human'):
            return self.interaction(f"start:{request['task_id']}:{int(time.time())}", 'mention', 'user', name, channel,
                                    task_id=request['task_id'], record_id=request['task_id'])
        return []

    def recorded(self, actor, tool, action, item, channel=''):
        """A review or follow-up the room recorded (RoomRuntime.coordination_record)."""
        if not isinstance(item, dict):
            return []
        base = f"{item.get('id')}:{action}:{item.get('updated')}"
        if tool == 'room_review' and action == 'request':
            return self.interaction(f'review_request:{base}', 'review_request', actor, item.get('reviewer', ''), channel,
                                    item.get('task_id', ''), item.get('id', ''), item.get('request') or item.get('revision'))
        if tool == 'room_review' and action == 'respond':
            return self.interaction(f'review_verdict:{base}', 'review_verdict', actor, item.get('owner') or item.get('requested_by', ''),
                                    channel, item.get('task_id', ''), item.get('id', ''),
                                    f"{item.get('status', '')}: {item.get('findings', '')}")
        if tool == 'room_followup' and action == 'create':
            return self.interaction(f'followup:{base}', 'followup', actor, item.get('assigned_to', ''), channel,
                                    item.get('task_id', ''), item.get('id', ''), item.get('text'))
        if tool == 'room_followup' and action == 'resolve':
            return self.interaction(f'followup_result:{base}', 'followup_result', actor, item.get('created_by', ''),
                                    channel, item.get('task_id', ''), item.get('id', ''), item.get('result'))
        return []

    def artifact(self, run, url, kind, source_url=''):
        """A visual an agent produced in its turn: a browser screenshot, a markup or an upload."""
        if getattr(run, 'sealed', False) or not url:
            return []
        item = {'url': url, 'kind': kind}
        if source_url:
            item['source_url'] = source_url
        return self.set_artifact(run.name, item)

    def set_artifact(self, name, item):
        self.artifacts[name] = {**item, 'task_id': self.current_task(name), 'at': time.time()}
        return self.agent_delta(name)

    # ---- delivery ------------------------------------------------------------------------------

    async def publish(self, deltas):
        for delta in deltas:
            await self.runtime.host._broadcast(json.dumps({'type': 'live', 'data': delta}, ensure_ascii=False))

    def publish_soon(self, deltas):
        """For synchronous hooks (enqueue): deliver on the running loop, or drop if there is none."""
        if not deltas:
            return
        try:
            asyncio.get_running_loop().create_task(self.publish(deltas))
        except RuntimeError:
            pass


def install(host, runtime):
    from fastapi import Request
    from fastapi.responses import JSONResponse
    feed = runtime.live

    @host.app.get('/api/live')
    async def live(request: Request):
        if request.query_params.get('project') != runtime.identity:
            return JSONResponse({'error': 'Project changed; reload the Live view'}, 409)
        return feed.snapshot()
