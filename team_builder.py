"""User-approved team proposals, drafted by the room's connected native agents."""
import asyncio
import copy
import json
import re
import time
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from project_room import write_json


def text(value, label, maximum=16000, required=True):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f'{label} must be text between {1 if required else 0} and {maximum} characters')
    return value.strip()


class TeamBuilder:
    def __init__(self, runtime):
        self.runtime = runtime
        self.lock = asyncio.Lock()
        self.path = None
        self.proposal = None

    def load(self):
        self.path = self.runtime.project_room.path.with_name('team-builder.json')
        self.proposal = json.loads(self.path.read_text('utf-8')) if self.path.exists() else None
        if self.proposal and self.proposal['status'] in ('drafting', 'challenging'):
            self.proposal.update(status='failed', error='Draft interrupted by restart or project change. Retry to continue.')
            self.save()

    def save(self):
        self.proposal['updated'] = time.time()
        write_json(self.path, self.proposal)

    def get(self, identifier=''):
        if not self.proposal or (identifier and identifier != self.proposal['id']):
            raise ValueError('Proposal changed; reload the team builder')
        if self.proposal['status'] in ('drafting', 'challenging'):
            actor = self.proposal['drafter'] if self.proposal['status'] == 'drafting' else self.proposal['challenger']
            if self.runtime.paused or actor not in {a['id'] for a in self.agents()}:
                self.interrupt(actor)
        result = copy.deepcopy(self.proposal)
        for member in result['members']:
            member['handle_available'] = not (member['id'] in self.runtime.profiles.items or
                self.runtime.host.registry.get_instance(member['id']))
        return result

    def agents(self):
        return [{'id': name, 'name': state.get('label', name), 'provider': state['provider']}
                for name, state in self.runtime.states.items()
                if state['provider'] in ('codex', 'claude', 'gemini', 'antigravity')
                and not state.get('archived') and not state.get('paused')
                and state['state'] not in ('blocked', 'login_required')]

    def context(self):
        return {**self.runtime.profiles.describe(), 'agents': self.agents(),
                'capacity': self.runtime.parallel_capacity(),
                'spec': self.runtime.project_room.state.get('spec', ''),
                'goal': (self.runtime.goals.goal or {}).get('text', ''),
                'proposal': self.get() if self.proposal else None}

    def token(self):
        p = self.proposal
        return {'id': p['id'], 'revision': p['revision'], 'stage': p['status']}

    def accepts(self, request):
        token = request.get('team_builder')
        return not token or (self.proposal and token == self.token())

    def queue(self):
        p = self.proposal
        actor = p['drafter'] if p['status'] == 'drafting' else p['challenger']
        if actor not in {a['id'] for a in self.agents()}:
            p.update(status='failed', error='Selected drafter or challenger is unavailable. Resume the agent and retry.')
            self.save()
            return
        target = next((m for m in p['members'] if m['member_id'] == p['revision_member']), None)
        prompt = (
            'Team builder: call room_team_builder(action="get") FIRST for the full saved brief, '
            'members, provider choices, revision target and feedback. Do not rely on this preview. '
            f'Stage={p["status"]}; proposal_id={p["id"]}; revision={p["revision"]}. '
            'Propose additions only: do not create agents, edit files, run commands or message others. '
            'capacity limits how many agents run at the same time; it does not limit team size. '
            'Extra members queue for turns, so propose the experts the brief needs. '
            'The drafter proposes; the challenger critiques overlaps/gaps and submits a revised proposal. '
            'Use descriptive role names, unique lowercase handles, native provider templates and their '
            'selected model/effort defaults. Choose effort by role: a coordinator, integrator or anyone on the '
            "owner's critical path gets at least the default effort, never low; keep low effort for narrow, "
            'low-risk lookups, and say why in the rationale. '
            'Account for active current members; never invent peer agreement. '
            'Submit with room_team_builder(action="submit", proposal_id, revision, payload). '
            'Payload: {members:[{name,id,template,model,effort,background,rationale}], '
            'coverage:[{need,detail,lead:[handle],support:[handle]}],summary}. '
            'Use a short need label (maximum 60 characters), with optional detail (maximum 200 characters). '
            'Coverage references active current '
            'and proposed handles without @. Empty members is valid when no additions are needed. '
            'For a member revision return ONLY its replacement plus complete updated coverage; '
            'other member decisions must stay intact. Finish with a brief chat summary after submission.\n'
            + (f'Revision target: {target["name"]} (@{target["id"]}); '
               f'feedback preview: {p["feedback"][:500]}\n' if target else '')
            + 'Brief preview: ' + p['brief'][:500])
        queued = self.runtime.enqueue(actor, p['channel'], prompt=prompt, chain=p['chain'],
                    routing={'human': False, 'team_builder': self.token()})
        if not queued:
            p.update(status='failed', error='Draft could not be queued. Resume the room/agent or clear its queue, then retry.')
            self.save()

    def start(self, body):
        source = body.get('source', 'text')
        if source not in ('spec', 'goal', 'text'):
            raise ValueError('Choose spec, goal or text as the brief source')
        context = self.context()
        brief = text(body.get('brief', context[source] if source != 'text' else ''), 'Brief', 64000)
        available = [a['id'] for a in self.agents()]
        drafter = body.get('drafter') or (available[0] if available else '')
        challenger = body.get('challenger') or next((a for a in available if a != drafter), '')
        if drafter not in available or challenger not in available or drafter == challenger:
            raise ValueError('Two available native agents are needed: one drafts and one challenges the proposal')
        channel = body.get('channel') or self.runtime.default_channel()
        if not self.runtime.owns_channel(channel):
            raise ValueError('Choose a chat in the current project')
        if self.runtime.paused:
            raise ValueError('Resume the room before drafting a team')
        self.proposal = dict(id=uuid.uuid4().hex[:16], revision=1, source=source, brief=brief,
            channel=channel, chain=self.runtime.project_room.new_chain(channel),
            drafter=drafter, challenger=challenger, status='drafting', members=[], coverage=[],
            summary='', history=[], error='', revision_member='', feedback='')
        self.save()
        self.queue()
        return self.get()

    def normalize_member(self, raw, member_id=None):
        if not isinstance(raw, dict):
            raise ValueError('Each proposed member must be an object')
        name = text(raw.get('name'), 'Name', 60)
        handle = raw.get('id') or re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:32]
        if not isinstance(handle, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,39}', handle) or handle in ('all', 'both', 'user', 'system', 'everyone'):
            raise ValueError('Handle must start with a letter and use lowercase letters, digits or hyphens')
        providers = {p['id']: p for p in self.runtime.profiles.describe()['providers']}
        template = raw.get('template')
        if template not in providers or providers[template]['provider'] == 'api':
            raise ValueError('Choose a configured native provider connection')
        defaults = providers[template]['controls'].get('selected', {})
        return dict(member_id=member_id or uuid.uuid4().hex[:12], name=name, id=handle,
            template=template, model=text(raw.get('model', defaults.get('model', '')), 'Model', 200, False),
            effort=text(raw.get('effort', defaults.get('effort', '')), 'Effort', 80, False),
            background=text(raw.get('background'), 'Background'),
            rationale=text(raw.get('rationale'), 'Rationale', 4000), status='proposed', error='')

    def submit(self, actor, proposal_id, revision, payload):
        self.get(proposal_id)
        p = self.proposal
        expected = p['drafter'] if p['status'] == 'drafting' else p['challenger']
        if p['status'] not in ('drafting', 'challenging') or actor != expected or revision != p['revision']:
            raise ValueError('Submission is stale or this agent is not assigned to the current stage')
        if not isinstance(payload, dict) or not isinstance(payload.get('members'), list):
            raise ValueError('Submit an object with members, coverage and summary')
        if len(json.dumps(payload)) > 256000:
            raise ValueError('Proposal is too large; shorten member backgrounds')
        members = [self.normalize_member(m) for m in payload['members']]
        if p['revision_member']:
            if len(members) != 1:
                raise ValueError('Return exactly the one member requested for revision')
            members[0]['member_id'] = p['revision_member']
            members = [members[0] if m['member_id'] == p['revision_member'] else m for m in p['members']]
        handles = [m['id'] for m in members]
        if len(set(handles)) != len(handles):
            raise ValueError('Proposed handles must be distinct')
        allowed = set(handles) | {n for n, m in self.runtime.profiles.items.items() if not m.get('archived')}
        coverage = payload.get('coverage')
        if not isinstance(coverage, list) or not coverage:
            raise ValueError('Provide expertise coverage for the brief')
        normalized = []
        for row in coverage:
            if not isinstance(row, dict):
                raise ValueError('Each coverage row must be an object')
            item = {'need': text(row.get('need'), 'Coverage need label', 60)}
            detail = text(row.get('detail', ''), 'Coverage detail', 200, False)
            if detail:
                item['detail'] = detail
            for key in ('lead', 'support'):
                values = row.get(key, [])
                if not isinstance(values, list) or any(not isinstance(v, str) or v not in allowed for v in values):
                    raise ValueError('Coverage must reference existing or proposed handles without @')
                item[key] = list(dict.fromkeys(values))
            normalized.append(item)
        summary = text(payload.get('summary'), 'Draft/challenge summary', 8000)
        p['history'].append({'actor': actor, 'stage': p['status'], 'summary': summary})
        p.update(members=members, coverage=normalized, summary=summary, error='')
        if p['status'] == 'drafting':
            p['status'] = 'challenging'
            self.save()
            self.queue()
        else:
            p.update(status='ready', revision_member='', feedback='')
            self.save()
        return self.get()

    def turn_finished(self, entry):
        if self.proposal and any(r.get('team_builder') == self.token() for r in entry.get('requests', [])):
            self.proposal.update(status='failed', error='Agent turn ended without an accepted proposal. Retry drafting.')
            self.save()

    def interrupt(self, actor=''):
        p = self.proposal
        if p and p['status'] in ('drafting', 'challenging'):
            expected = p['drafter'] if p['status'] == 'drafting' else p['challenger']
            if not actor or actor == expected:
                p.update(status='failed', error='Draft stopped. Resume the room/agent, then retry when ready.')
                self.save()

    def retry(self, identifier):
        self.get(identifier)
        if self.proposal['status'] != 'failed':
            raise ValueError('Only a failed draft can be retried')
        self.proposal.update(status='drafting', revision=self.proposal['revision'] + 1, error='',
            chain=self.runtime.project_room.new_chain(self.proposal['channel']))
        self.save()
        self.queue()
        return self.get()

    def member_action(self, identifier, member_id, body):
        self.get(identifier)
        p = self.proposal
        if p['status'] != 'ready':
            raise ValueError('Wait for the draft and challenge to finish')
        member = next((m for m in p['members'] if m['member_id'] == member_id), None)
        if not member or member['status'] == 'added':
            raise ValueError('Choose a proposed member that has not been added')
        action = body.get('action')
        if action == 'edit':
            changes = body.get('changes')
            if not isinstance(changes, dict):
                raise ValueError('Provide member changes')
            old_handle = member['id']
            updated = self.normalize_member({**member, **{k: v for k, v in changes.items()
                if k in ('name', 'id', 'background', 'template', 'model', 'effort')}}, member_id)
            if any(m['member_id'] != member_id and m['id'] == updated['id'] for m in p['members']):
                raise ValueError('Another proposal already uses that handle')
            member.update(updated)
            for row in p['coverage']:
                for key in ('lead', 'support'):
                    row[key] = [member['id'] if handle == old_handle else handle for handle in row[key]]
        elif action in ('approve', 'decline'):
            member.update(status='approved' if action == 'approve' else 'declined', error='')
        elif action == 'request_revision':
            feedback = text(body.get('feedback'), 'Revision feedback', 4000)
            member['status'] = 'revision_requested'
            p.update(revision_member=member_id, feedback=feedback, status='drafting',
                     revision=p['revision'] + 1, chain=self.runtime.project_room.new_chain(p['channel']))
        else:
            raise ValueError('Choose approve, edit, request_revision or decline')
        self.save()
        if action == 'request_revision':
            self.queue()
        return self.get()

    async def add(self, identifier):
        self.get(identifier)
        p, path, identity = self.proposal, self.path, self.runtime.identity
        if p['status'] != 'ready':
            raise ValueError('Wait for the draft and challenge to finish')
        for member in p['members']:
            if member['status'] != 'approved':
                continue
            try:
                profile = await self.runtime.change_profile(member,
                    creation_key=p['id'] + ':' + member['member_id'], expected_project=identity)
                member.update(status='added', id=profile['id'], error='')
            except (ValueError, OSError, RuntimeError) as exc:
                member['error'] = str(exc)
            p['updated'] = time.time()
            write_json(path, p)
            if self.runtime.identity != identity:
                raise ValueError('Project changed during creation. Return to the original project to see results.')
        return self.get()


def install(host, runtime):
    builder = runtime.team_builder

    @host.app.get('/api/runtime/team-builder')
    async def context():
        return builder.context()

    @host.app.get('/api/runtime/team-builder/proposals/{identifier}')
    async def proposal(identifier: str):
        try:
            return builder.get(identifier)
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, 400)

    async def change(operation, request=None, **kwargs):
        try:
            body = await request.json() if request is not None else None
            if body is not None and not isinstance(body, dict):
                raise ValueError('Expected a JSON object')
            async with builder.lock:
                result = await builder.add(**kwargs) if operation == 'add' else getattr(builder, operation)(
                    **kwargs, **({'body': body} if body is not None else {}))
            await runtime.emit('team_builder', result)
            return result
        except (ValueError, TypeError, KeyError) as exc:
            return JSONResponse({'error': str(exc)}, 400)

    @host.app.post('/api/runtime/team-builder/proposals')
    async def start(request: Request):
        return await change('start', request)

    @host.app.post('/api/runtime/team-builder/proposals/{identifier}/members/{member_id}')
    async def member(identifier: str, member_id: str, request: Request):
        return await change('member_action', request, identifier=identifier, member_id=member_id)

    @host.app.post('/api/runtime/team-builder/proposals/{identifier}/add')
    async def add(identifier: str):
        return await change('add', identifier=identifier)

    @host.app.post('/api/runtime/team-builder/proposals/{identifier}/retry')
    async def retry(identifier: str):
        return await change('retry', identifier=identifier)
