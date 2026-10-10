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

# A proposal file larger than this is treated as corrupt rather than read (security d4cdc9a4).
PROPOSAL_BYTES = 1_000_000



def inert(value):
    """Agent-written text for a system chat line (security a09e47f4). The chat renderer turns a literal
    backslash-n into a newline before it protects code, so backslashes and backticks are replaced and
    whitespace collapsed; the caller shows the result as inline code, which renders it literally."""
    return ' '.join(str(value).replace('\\', '/').replace('`', "'").split()) or '-'


CHALLENGE_FEEDBACK = ('The user asked you to challenge this agent-suggested member. Check that the role is really '
                      'missing from the team, that the instructions (background) are right and complete, and that the '
                      'connection, model and effort fit. Return exactly one revised member; you cannot withdraw the '
                      'suggestion. Coverage may be empty. Put your critique in summary.')


class Gone(ValueError):
    """The named proposal is no longer this project's proposal; never fall back to the current one."""


class OpenProject(ValueError):
    """Only the open project's proposal can be acted on: Add creates agents in the running room."""


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
            raise Gone('This suggestion or proposal is no longer available; reload the team builder')
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
            if p.get('source') == 'suggestion':
                self.challenge_ended('failed', 'The reviewing agent is unavailable')
                return
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
            if p.get('source') == 'suggestion':
                self.challenge_ended('failed', 'The review could not be queued')
                return
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
        if p.get('source') == 'suggestion':
            for member in members:
                self.refuse_keyed(member)  # Security C1 also covers a requested challenge's revision.
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
        suggestion = p.get('source') == 'suggestion'
        if suggestion and len(payload['members']) != 1:
            raise ValueError('Return exactly the one revised member; a suggestion cannot be withdrawn')
        if not isinstance(coverage, list) or (not coverage and not suggestion):
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
            if suggestion and p.get('challenge'):
                p['challenge'].update(status='done', summary=summary)
                self.announce_challenge(f'{self.label(actor)} reviewed {self.label(p["suggested_by"])}\'s suggestion of '
                                        f'`{inert(members[0]["name"])}`. Its summary: `{inert(summary)}` '
                                        'Both versions are on Team.')
            self.save()
        return self.get()

    def turn_finished(self, entry):
        if self.proposal and any(r.get('team_builder') == self.token() for r in entry.get('requests', [])):
            if self.proposal.get('source') == 'suggestion':
                self.challenge_ended('failed', "The reviewing agent's turn ended without a revision")
                return
            self.proposal.update(status='failed', error='Agent turn ended without an accepted proposal. Retry drafting.')
            self.save()

    def interrupt(self, actor=''):
        p = self.proposal
        if p and p['status'] in ('drafting', 'challenging'):
            expected = p['drafter'] if p['status'] == 'drafting' else p['challenger']
            if not actor or actor == expected:
                if p.get('source') == 'suggestion':
                    self.challenge_ended('paused', 'The review was stopped or the agent is unavailable')
                    return
                p.update(status='failed', error='Draft stopped. Resume the room/agent, then retry when ready.')
                self.save()

    def label(self, name):
        return self.runtime.states.get(name, {}).get('label') or name

    def announce_challenge(self, text):
        p = self.proposal
        self.runtime.host.store.add('system', text, channel=p['channel'], metadata={'no_route': True, 'team_suggestion': {
            'project_id': self.runtime.identity, 'proposal_id': p['id'], 'status': p['status']}})

    def challenge_ended(self, status, reason):
        """The suggestion returns to the user's review exactly as it was; there is no automatic retry."""
        p = self.proposal
        member = next((m for m in p['members'] if m['member_id'] == p.get('revision_member')), None)
        if member and member['status'] == 'revision_requested':
            member['status'] = 'proposed'
        p.update(status='ready', revision_member='', feedback='', error='')
        p.setdefault('challenge', {}).update(status=status, reason=reason)
        self.save()
        self.announce_challenge(f'The requested review of {self.label(p["suggested_by"])}\'s suggestion did not finish: '
                                f'{reason}. The suggestion is unchanged; retry the review or decide on Team.')

    def challenge(self, identifier, body):
        """The user asks a named agent to review a suggestion. It returns one revised member; the original is
        kept beside it, and the user still decides."""
        self.check_open(body)
        self.get(identifier)
        p = self.proposal
        if p.get('source') != 'suggestion' or p['status'] != 'ready':
            raise ValueError('Only a suggestion waiting for review can be challenged')
        member = p['members'][0]
        if member['status'] not in ('proposed', 'approved'):
            raise ValueError('A suggestion can be challenged only before it is added or declined')
        agent = (body or {}).get('agent')
        if agent == p['suggested_by'] or agent not in {a['id'] for a in self.agents()}:
            raise ValueError('Choose an available agent other than the one who suggested it')
        p.setdefault('original_member', copy.deepcopy(member))
        member['status'] = 'revision_requested'
        p.update(status='challenging', challenger=agent, revision=p['revision'] + 1, revision_member=member['member_id'],
                 feedback=CHALLENGE_FEEDBACK, error='', chain=self.runtime.project_room.new_chain(p['channel']))
        p['challenge'] = {'agent': agent, 'status': 'running', 'reason': '', 'summary': ''}
        self.save()
        self.queue()
        return self.get()

    def refuse_keyed(self, member):
        """Security C1: on a suggestion, an agent never puts a member on a billed API-key connection;
        only the user chooses one, in Edit."""
        if self.runtime.profiles.templates.get(member['template'], {}).get('key_target'):
            raise ValueError('Use a signed-in connection; the user chooses API-key connections')

    def finished(self):
        """No proposal, or one whose every member was added or declined: nothing of the user's is open."""
        p = self.proposal
        return not p or (p['status'] == 'ready' and all(m['status'] in ('added', 'declined') for m in p['members']))

    def suggest(self, actor, channel, member, reason):
        """An agent proposes one new member. It goes straight to the user's review: no agent turn runs,
        nothing is added, and the user approves, edits, declines and adds as with any proposal."""
        if not self.finished():
            raise ValueError('A team proposal is still open for review. Mention the missing skill in chat instead.')
        reason = text(reason, 'Reason', 1000)
        if not isinstance(member, dict):
            raise ValueError('Suggest one member as an object')
        normalized = self.normalize_member(member)
        self.refuse_keyed(normalized)
        handle = normalized['id']
        previous = self.proposal or {}
        declined = list(previous.get('declined_suggestions', []))
        if previous.get('source') == 'suggestion':
            declined += [{'by': previous['suggested_by'], 'handle': m['id']}
                         for m in previous['members'] if m['status'] == 'declined']
        if handle in self.runtime.profiles.items or self.runtime.host.registry.get_instance(handle):
            raise ValueError(f'@{handle} is already a team member')
        if {'by': actor, 'handle': handle} in declined:
            raise ValueError(f'The user already declined your suggestion of @{handle}')
        if not self.runtime.owns_channel(channel):
            channel = self.runtime.default_channel()
        self.proposal = dict(id=uuid.uuid4().hex[:16], revision=1, source='suggestion', suggested_by=actor,
            brief=reason, channel=channel, chain=self.runtime.project_room.new_chain(channel),
            drafter=actor, challenger='', status='ready', members=[normalized], coverage=[],
            summary=reason, history=[{'actor': actor, 'stage': 'suggestion', 'summary': reason}], error='',
            revision_member='', feedback='', created=time.time(), declined_suggestions=declined[-50:])
        self.save()
        p = self.proposal
        label = self.runtime.states.get(actor, {}).get('label') or actor
        providers = {x['id']: x for x in self.runtime.profiles.describe()['providers']}
        connection = providers.get(normalized['template'], {}).get('label', normalized['template'])
        # Security C3: the name and reason are the agent's words, shown inert and attributed, so they can
        # never read as a room instruction. The full reason stays as written in the proposal.
        self.runtime.host.store.add(
            'system',
            f'{label} suggests adding `{inert(normalized["name"])}` (@{handle}, {connection}). '
            f'Its reason: `{inert(reason)}` Review it on Team.',
            channel=channel, metadata={'no_route': True, 'team_suggestion': {
                'project_id': self.runtime.identity, 'proposal_id': p['id'], 'status': 'ready'}})
        return self.get()

    def read(self, project_id, proposal_id):
        """Read-only view of a named proposal, also in a project that is not open (design 7097f58).
        Nothing is switched, loaded or started; a different proposal is refused, never substituted."""
        if project_id == self.runtime.identity:
            return {**self.get(proposal_id), 'project_id': project_id, 'read_only': False}
        import project_registry
        if not isinstance(project_id, str) or not project_registry.IDENTITY.fullmatch(project_id):
            raise ValueError('Choose a listed project')
        root = (self.runtime.data / 'projects').resolve()
        path = (root / project_id / 'team-builder.json').resolve()
        if not path.is_relative_to(root) or not path.parent.is_dir():
            raise ValueError('Choose a listed project')
        try:
            if path.stat().st_size > PROPOSAL_BYTES:
                raise Gone('This suggestion is no longer available')
            proposal = json.loads(path.read_text('utf-8'))
        except FileNotFoundError:
            raise Gone('This suggestion is no longer available') from None
        except Gone:
            raise
        except (OSError, ValueError):
            raise Gone('This suggestion is no longer available; its saved record could not be read') from None
        if not isinstance(proposal, dict) or proposal.get('id') != proposal_id:
            raise Gone('This suggestion is no longer available')
        return {**proposal, 'project_id': project_id, 'read_only': True}

    def check_open(self, body):
        """Actions may name the project they act in; another project's proposal is read-only (409)."""
        project = (body or {}).get('project_id')
        if project is not None and project != self.runtime.identity:
            raise OpenProject('Open this project to act on its suggestion')

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
        self.check_open(body)
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
        elif action == 'use_original':
            original = p.get('original_member')
            if p.get('source') != 'suggestion' or not original:
                raise ValueError('Only a challenged suggestion has an original to return to')
            member.update({**copy.deepcopy(original), 'member_id': member_id, 'status': 'proposed', 'error': ''})
        elif action in ('approve', 'decline'):
            member.update(status='approved' if action == 'approve' else 'declined', error='')
        elif action == 'request_revision':
            if p.get('source') == 'suggestion':
                # The drafting stage would queue the suggester with no challenger; a suggestion is
                # reviewed only by a named agent the user picks.
                raise ValueError('Ask a named agent to challenge this suggestion instead')
            feedback = text(body.get('feedback'), 'Revision feedback', 4000)
            member['status'] = 'revision_requested'
            p.update(revision_member=member_id, feedback=feedback, status='drafting',
                     revision=p['revision'] + 1, chain=self.runtime.project_room.new_chain(p['channel']))
        else:
            raise ValueError('Choose approve, edit, request_revision, use_original or decline')
        self.save()
        if action == 'request_revision':
            self.queue()
        return self.get()

    async def add(self, identifier, body=None):
        self.check_open(body)
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
        except Gone as exc:
            return JSONResponse({'error': str(exc)}, 409)
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, 400)

    @host.app.get('/api/runtime/team-builder/projects/{project_id}/proposals/{identifier}')
    async def project_proposal(project_id: str, identifier: str):
        # UI session only (run tokens reach just coordination and upload); read-only for other projects.
        try:
            return builder.read(project_id, identifier)
        except Gone as exc:
            return JSONResponse({'error': str(exc)}, 409)
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, 404)

    async def change(operation, request=None, **kwargs):
        try:
            body = await request.json() if request is not None else None
            if body is not None and not isinstance(body, dict):
                raise ValueError('Expected a JSON object')
            async with builder.lock:
                result = await builder.add(**kwargs, body=body) if operation == 'add' else getattr(builder, operation)(
                    **kwargs, **({'body': body} if body is not None else {}))
            await runtime.emit('team_builder', result)
            return result
        except (Gone, OpenProject) as exc:
            return JSONResponse({'error': str(exc)}, 409)
        except (ValueError, TypeError, KeyError) as exc:
            return JSONResponse({'error': str(exc)}, 400)

    @host.app.post('/api/runtime/team-builder/proposals')
    async def start(request: Request):
        return await change('start', request)

    @host.app.post('/api/runtime/team-builder/proposals/{identifier}/members/{member_id}')
    async def member(identifier: str, member_id: str, request: Request):
        return await change('member_action', request, identifier=identifier, member_id=member_id)

    @host.app.post('/api/runtime/team-builder/proposals/{identifier}/add')
    async def add(identifier: str, request: Request):
        # An optional {project_id} body pins the exact project; older clients send no body.
        has_body = bool(await request.body())
        return await change('add', request if has_body else None, identifier=identifier)

    @host.app.post('/api/runtime/team-builder/proposals/{identifier}/challenge')
    async def challenge(identifier: str, request: Request):
        return await change('challenge', request, identifier=identifier)

    @host.app.post('/api/runtime/team-builder/proposals/{identifier}/retry')
    async def retry(identifier: str):
        return await change('retry', identifier=identifier)
