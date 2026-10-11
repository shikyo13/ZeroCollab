"""Team decisions: a bounded, attributed discussion that ends in a user-recorded decision.

The room's normal agent turns do the talking; this module owns the stages, tokens and
submissions. Contributions are append-only and attributed; nothing is a single mutable
proposal. A discussion grants no deploy, restart, settings or credential authority.
Model: Docs/decision-flow-model.md (reviewed by opus in f93e3350).
"""
import asyncio
import copy
import json
import math
import re
import time
import uuid
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse
from project_room import write_json
from team_builder import text

OPEN = ('positions', 'critique', 'proposal', 'verdicts')
CLOSED = ('recorded', 'stopped')
NEXT = {'positions': 'critique', 'critique': 'proposal', 'proposal': 'verdicts', 'verdicts': 'review'}
STAGE_SECONDS = 15 * 60
MAX_RETURNS = MAX_EDITS = 2
MAX_PARTICIPANTS = 8
KEEP = 40
NOTE_LIMIT = 4000
MAX_INPUTS = 8
MAX_ATTACHMENTS = 6
UPLOAD_URL = re.compile(r'^/uploads/[A-Za-z0-9][A-Za-z0-9._-]{0,120}$')
# What a sealed contribution keeps until its round's positions are revealed: who and when, nothing else.
SEALED_FIELDS = ('id', 'author', 'kind', 'stage', 'round', 'revision', 'epoch', 'created')


def items(value, label, maximum, length):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f'{label} must be a list of at most {maximum} entries')
    return [text(v, label, length) for v in value]


def evidence(value):
    return items(value, 'Evidence', 8, 500)


class DecisionFlow:
    def __init__(self, runtime):
        self.runtime = runtime
        self.lock = asyncio.Lock()
        self.path = None
        self.items = []
        self.timers = {}

    # ---- storage -------------------------------------------------------------------------

    def load(self):
        for handle in self.timers.values():
            handle.cancel()
        self.timers.clear()
        self.path = self.runtime.project_room.path.with_name('decisions.json')
        self.items = json.loads(self.path.read_text('utf-8')) if self.path.exists() else []
        changed = False
        for d in self.items:
            if d['stage'] in OPEN and not d.get('interrupted'):
                self.pause(d, 'Interrupted by a restart or project change.')
                changed = True
        if changed:
            self.save()

    def save(self):
        finished = [d for d in self.items if d['stage'] in CLOSED]
        while len(self.items) > KEEP and finished:
            self.items.remove(finished.pop(0))
        write_json(self.path, self.items)

    def find(self, identifier):
        d = next((x for x in self.items if x['id'] == identifier), None)
        if d is None:
            raise ValueError('Discussion was not found; reload decisions')
        return d

    def active(self, channel):
        return next((d for d in self.items if d.get('channel') == channel and d['stage'] not in CLOSED + ('draft',)), None)

    def dedicated(self, channel):
        """A live discussion that owns its chat (started by the user in its own chat). In-place decisions
        share the conversation and never take it over."""
        return next((d for d in self.items if d.get('channel') == channel and not d.get('in_place')
                     and d['stage'] not in CLOSED + ('draft',)), None)

    def sealed_rounds(self, d):
        """Rounds whose positions are still hidden: the current one while it collects positions, and any
        earlier round that was reopened before its positions were revealed."""
        hidden = {r['round'] for r in d.get('rounds', []) if not r.get('revealed')}
        if d['stage'] in ('draft', 'positions'):
            hidden.add(d.get('round', 1))
        return hidden

    def sealing(self, run):
        """The decision whose positions this run is writing, while they are sealed, else None. Its chat
        output, markup posts and Activity notes stay out of sight until the reveal (security 365efc14 D1)."""
        for request in run.entry.get('requests', []):
            token = request.get('decision')
            d = token and next((x for x in self.items if x['id'] == token.get('id')), None)
            if d and token.get('stage') == 'positions' and d.get('round', 1) in self.sealed_rounds(d):
                return d
        return None

    # ---- derived state -------------------------------------------------------------------

    def token(self, d):
        return {'id': d['id'], 'stage': d['stage'], 'revision': d['revision'], 'epoch': d['epoch']}

    def accepts(self, request):
        token = request.get('decision')
        if not token:
            return True
        d = next((x for x in self.items if x['id'] == token.get('id')), None)
        return bool(d and not d.get('interrupted') and token == self.token(d))

    def here(self, d, stage=None):
        """Contributions for the open stage at the current revision and epoch."""
        stage = stage or d['stage']
        return [c for c in d['contributions'] if c['stage'] == stage
                and c['revision'] == d['revision'] and c['epoch'] == d['epoch']]

    def proposal(self, d):
        return next((c for c in reversed(d['contributions'])
                     if c['kind'] in ('proposal', 'edit') and c['revision'] == d['revision']), None)

    def owed(self, d):
        if d['stage'] not in OPEN:
            return []
        done = {c['author'] for c in self.here(d)}
        if d['stage'] == 'proposal':
            return [] if done else [d['lead']]
        if d['stage'] == 'verdicts':
            current = self.proposal(d)
            done = {c['author'] for c in self.here(d) if current and c.get('proposal_id') == current['id']} | \
                   {c['author'] for c in self.here(d) if c['kind'] == 'missing'}
        return [p for p in d['participants'] if p not in done]

    def verdicts(self, d):
        current = self.proposal(d)
        if not current:
            return []
        return [c for c in d['contributions'] if c['kind'] == 'verdict' and c.get('proposal_id') == current['id']
                and c['epoch'] == d['epoch']]

    def outcome(self, d):
        votes = {c['author']: c['verdict'] for c in self.verdicts(d)}
        if not self.proposal(d) or set(votes) != set(d['participants']):
            return 'unresolved'
        return 'agreed' if all(v == 'agree' for v in votes.values()) else 'dissent'

    def actions(self, d):
        stage, interrupted = d['stage'], bool(d.get('interrupted'))
        result = []
        if stage == 'draft':
            result.append('start')
        if stage in OPEN and interrupted:
            result.append('continue')
        if stage in OPEN and not interrupted:
            result += ['retry', 'close_stage']
        if (d.get('record') or {}).get('note_id'):
            # Recorded; only failed follow-up tasks can be retried.
            return ['record'] if stage == 'review' else []
        if stage == 'review' or (interrupted and self.proposal(d)):
            if self.proposal(d) and d['edits_used'] < MAX_EDITS:
                result.append('edit')
            if self.proposal(d) and d['returns_used'] < MAX_RETURNS and d['lead'] not in self.missing(d):
                result.append('return')
        if stage == 'review':
            result.append('record')
        if d.get('in_place') and stage not in CLOSED + ('draft',):
            # Another round, or reclassifying one of the user's own inputs, from the card.
            result += ['reopen', 'input_kind']
        if stage not in CLOSED:
            result.append('stop')
        return result

    def missing(self, d):
        return {c['author'] for c in d['contributions'] if c['kind'] == 'missing'
                and c['revision'] == d['revision'] and c['epoch'] == d['epoch']}

    def coverage(self, d):
        """The current round's positions: who submitted, who was invited, who is missing."""
        superseded = set(d.get('superseded', []))
        submitted = list(dict.fromkeys(c['author'] for c in d['contributions'] if c['kind'] == 'position'
                                       and c.get('round', 1) == d.get('round', 1) and c['id'] not in superseded))
        return {'submitted': submitted, 'invited': list(d['participants']),
                'missing': [p for p in d['participants'] if p not in submitted]}

    def phase(self, d):
        """What the card may claim: discussing, candidate (a proposal not yet ratified), review_ready
        (verdicts are in, with the real outcome), recorded or stopped. Never consensus before review."""
        if d['stage'] in ('recorded', 'stopped', 'draft'):
            return d['stage']
        if d['stage'] == 'review':
            return 'review_ready'
        return 'candidate' if d['stage'] in ('proposal', 'verdicts') and self.proposal(d) else 'discussing'

    def view(self, d, reader=''):
        """Sealed for every reader, the user included: until a round's positions are revealed, a position
        is only who and when, unless the reader wrote it. The flow token goes only to agents (security D2)."""
        result = copy.deepcopy(d)
        hidden = self.sealed_rounds(d)
        result['contributions'] = [
            {k: c[k] for k in SEALED_FIELDS if k in c} | {'sealed': True}
            if c['kind'] == 'position' and c.get('round', 1) in hidden and not (reader and c['author'] == reader) else c
            for c in result['contributions']]
        superseded = set(d.get('superseded', []))
        for c in result['contributions']:
            if c['id'] in superseded:
                c['before_new_direction'] = True
        current = self.proposal(d)
        result.update(owed=self.owed(d), outcome=self.outcome(d), coverage=self.coverage(d), phase=self.phase(d),
                      round=d.get('round', 1), rounds=d.get('rounds', []),
                      current_proposal=current['id'] if current else '',
                      actions=[] if reader else self.actions(d))
        if reader:
            result['token'] = self.token(d)
        if current and current['kind'] == 'proposal':
            answered = {a['challenge_id'] for a in current.get('answers', [])}
            # Challenges the lead did not answer stay visible instead of being silently dropped.
            result['unanswered_challenges'] = [c['id'] for c in d['contributions']
                                               if c['kind'] == 'challenge' and c['id'] not in answered | superseded]
        return result

    def summary(self, d):
        return {k: d[k] for k in ('id', 'question', 'stage', 'channel', 'participants', 'lead', 'updated')} | \
               {'interrupted': d.get('interrupted', ''), 'outcome': self.outcome(d)}

    # ---- queueing, deadlines and stage changes ---------------------------------------------

    def deadline_seconds(self, d):
        # Turns queue when participants exceed parallel slots, so the stage clock scales with
        # the number of admission rounds; nobody is marked missing before they could run.
        per_turn = self.runtime.project_room.limits.get('turn_max_seconds') or STAGE_SECONDS
        waiting = len(self.owed(d)) or 1
        return per_turn * math.ceil(waiting / max(1, self.runtime.parallel_capacity()))

    def arm(self, d):
        old = self.timers.pop(d['id'], None)
        if old:
            old.cancel()
        if d['stage'] not in OPEN or d.get('interrupted'):
            d.pop('deadline', None)
            return
        seconds = self.deadline_seconds(d)
        d['deadline'] = time.time() + seconds
        token = self.token(d)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self.timers[d['id']] = loop.call_later(seconds, lambda: loop.create_task(self.expire(d['id'], token)))

    async def expire(self, identifier, token):
        async with self.lock:
            d = next((x for x in self.items if x['id'] == identifier), None)
            if not d or d.get('interrupted') or token != self.token(d):
                return
            self.timers.pop(identifier, None)
            self.advance(d)
            self.save()
        await self.publish(d)

    def prompt(self, d, name):
        stage = d['stage']
        head = (f'Team decision {d["id"]}, stage {stage}. Call room_decision(action="get", decision_id="{d["id"]}") FIRST '
                'for the question, criteria and the contributions you may see. Submit with room_decision(action="submit", '
                f'decision_id="{d["id"]}", stage="{stage}", revision={d["revision"]}, epoch={d["epoch"]}, payload=...). '
                'Only a submission counts; chat text is not part of the record. This discussion grants no authority to '
                'deploy, restart, change settings or read credentials. Do not message or mention other agents: '
                'the flow queues each turn.\n')
        if d.get('interrupted_by'):
            head += f'New direction from the user: chat message #{d["interrupted_by"]}. Take it into account.\n'
        body = {
            'positions': 'Give your initial position. Other positions stay hidden until everyone has submitted, so answer '
                         'on your own evidence. payload={"text": "...", "evidence": ["file:line, command output or source"]}.',
            'critique': 'One round. Challenge the other positions with evidence and concede where they are right. '
                        'payload={"challenges": [{"responds_to": ["<position id>"], "text": "...", "evidence": []}], '
                        '"concessions": [{"responds_to": ["<position id>"], "text": "..."}]}. At least one entry.',
            'proposal': 'You are the lead. Write the joint proposal. Keep disagreement explicit; never manufacture consensus. '
                        'payload={"text": "...", "agreed": [], "dissent": [{"point": "...", "by": ["handle"]}], '
                        '"unresolved": [], "would_change": ["evidence that would change the outcome"], '
                        '"answers": [{"challenge_id": "...", "status": "accepted|rejected|open", "note": "..."}], '
                        '"tasks": [{"title": "...", "details": "..."}]}. Answer every challenge id. Tasks are created '
                        'unowned only if the user records the decision.',
            'verdicts': 'Give your verdict on the current proposal. Disagreement is a valid result. '
                        'payload={"proposal_id": "<current_proposal>", "verdict": "agree|dissent", "reason": "..."}.',
        }[stage]
        if stage == 'proposal' and d.get('return_question'):
            body += f'\nThe user returned the proposal with this question: {d["return_question"]}'
        return head + body + '\nQuestion preview: ' + d['question'][:500]

    def queue(self, d, names=None):
        names = self.owed(d) if names is None else names
        turns = d.setdefault('turns', {})
        for name in names:
            queued = name in self.runtime.states and self.runtime.enqueue(
                name, d['channel'], prompt=self.prompt(d, name), chain=d['chain'],
                routing={'human': False, 'decision': self.token(d)})
            turns[name] = {'state': 'queued' if queued else 'no_response', 'time': time.time(),
                           **({} if queued else {'detail': 'Could not be queued: the agent is stopped, blocked or busy.'})}
        self.arm(d)

    def enter(self, d, stage):
        d.update(stage=stage, turns={}, chain=self.runtime.project_room.new_chain(d['channel']))
        if stage == 'positions':
            d['position_chains'] = [d['chain']]
            d.setdefault('chain_rounds', {})[d['chain']] = d.get('round', 1)
        if stage in OPEN:
            self.queue(d)
        else:
            self.arm(d)
        if stage == 'review' and d.get('in_place'):
            self.post_conclusion(d)

    def attachments(self, value):
        """Mockups and markup for a position: this room's own /uploads files only, at most MAX_ATTACHMENTS."""
        if value is None:
            return []
        if not isinstance(value, list) or len(value) > MAX_ATTACHMENTS:
            raise ValueError(f'attachments must be a list of at most {MAX_ATTACHMENTS} {{url, name}} objects')
        folder = Path(self.runtime.host.store.upload_dir).resolve()
        result = []
        for row in value:
            url = row.get('url') if isinstance(row, dict) else None
            if not isinstance(url, str) or not UPLOAD_URL.match(url) or not (folder / url.split('/', 2)[2]).is_file():
                raise ValueError('Each attachment must be an existing /uploads/<file> from this room')
            result.append({'url': url, 'name': text(row.get('name') or url.rsplit('/', 1)[1], 'Attachment name', 120)})
        return result

    def post_conclusion(self, d):
        """The synthesiser's conclusion, posted once per round when it reaches review with its real outcome.
        A proposal before review is only a candidate; an interrupted round posts nothing (frontend 8407ede8 F2)."""
        posted = d.setdefault('conclusions_posted', [])
        if d.get('interrupted') or d.get('round', 1) in posted:
            return
        current, outcome, coverage = self.proposal(d), self.outcome(d), self.coverage(d)
        lines = [f'Team decision {d["id"]}, round {d.get("round", 1)}: {d["question"]}',
                 f'Outcome: {outcome}. Positions from {len(coverage["submitted"])} of {len(coverage["invited"])}'
                 + (f'; missing: {", ".join(coverage["missing"])}' if coverage['missing'] else '') + '.']
        if current:
            lines.append(current['text'])
            for key, label in (('agreed', 'Agreed'), ('unresolved', 'Unresolved'), ('would_change', 'Would change it')):
                if current.get(key):
                    lines.append(f'{label}: ' + '; '.join(current[key]))
            for row in current.get('dissent', []):
                lines.append(f'Dissent ({", ".join(row["by"])}): {row["point"]}')
        else:
            lines.append('No proposal was written; nothing is concluded.')
        lines.append('Waiting for the user to record, reopen or edit it. It grants no deploy, settings or credential authority.')
        self.runtime.host.store.add(d['lead'] if current else 'system', '\n'.join(lines), channel=d['channel'],
                                    metadata={'no_route': True, 'decision': d['id'], 'round': d.get('round', 1)})
        posted.append(d.get('round', 1))

    def advance(self, d):
        """Close the open stage: anyone still owing is recorded as missing, then the next stage starts."""
        for name in self.owed(d):
            self.contribute(d, name, 'missing', text=f'No response from {name}')
        stage = d['stage']
        if stage == 'positions' and len([c for c in self.here(d) if c['kind'] == 'position']) < 2:
            self.enter(d, 'review')
        elif stage == 'proposal' and not self.proposal(d):
            self.enter(d, 'review')
        else:
            self.enter(d, NEXT[stage])

    def settle(self, d):
        if d['stage'] in OPEN and not d.get('interrupted') and not self.owed(d):
            self.advance(d)

    def pause(self, d, reason, message_id=None):
        # Submissions already made in the open stage stay visible but no longer count: Continue
        # re-queues everyone at the new epoch with the new direction in context.
        d.setdefault('superseded', []).extend(c['id'] for c in self.here(d) if c['kind'] != 'missing')
        d['epoch'] += 1
        d['interrupted'] = reason
        if message_id is not None:
            d['interrupted_by'] = message_id
        d['turns'] = {}
        self.arm(d)

    def contribute(self, d, author, kind, **fields):
        item = {'id': uuid.uuid4().hex[:10], 'author': author, 'stage': d['stage'], 'kind': kind,
                'round': d.get('round', 1), 'revision': d['revision'], 'epoch': d['epoch'], 'created': time.time(), **fields}
        d['contributions'].append(item)
        d['updated'] = time.time()
        return item

    # ---- agent tool -------------------------------------------------------------------------

    def get(self, actor, decision_id):
        d = self.find(decision_id)
        if actor not in d['participants']:
            raise ValueError('You are not a participant in this discussion')
        return self.view(d, reader=actor)

    def submit(self, actor, decision_id, stage, revision, epoch, payload, fresh=True):
        d = self.find(decision_id)
        if d.get('interrupted'):
            raise ValueError('The discussion is paused for new direction from the user; nothing was recorded')
        if (stage, revision, epoch) != (d['stage'], d['revision'], d['epoch']):
            raise ValueError(f'Submission is stale: the discussion is at stage {d["stage"]}, revision {d["revision"]}, '
                             f'epoch {d["epoch"]}. Call get and follow the current stage; nothing was recorded')
        if actor not in self.owed(d):
            raise ValueError('You do not owe a submission for this stage; nothing was recorded')
        if not isinstance(payload, dict):
            raise ValueError('payload must be an object')
        if len(json.dumps(payload)) > 64000:
            raise ValueError('payload is too large; shorten it or cite files instead of quoting them')
        context = 'fresh' if fresh else 'prior'
        ids = lambda kind: {c['id'] for c in d['contributions'] if c['kind'] == kind}
        if stage == 'positions':
            self.contribute(d, actor, 'position', text=text(payload.get('text'), 'Position', 8000),
                            evidence=evidence(payload.get('evidence')),
                            attachments=self.attachments(payload.get('attachments')), context=context)
        elif stage == 'critique':
            positions = ids('position')
            entries = []
            for kind, key in (('challenge', 'challenges'), ('concession', 'concessions')):
                rows = payload.get(key) or []
                if not isinstance(rows, list) or len(rows) > 8:
                    raise ValueError(f'{key} must be a list of at most 8 entries')
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError(f'Each of {key} must be an object')
                    targets = row.get('responds_to')
                    if not isinstance(targets, list) or not targets or any(t not in positions for t in targets):
                        raise ValueError('responds_to must list position ids from this discussion')
                    entries.append((kind, dict(text=text(row.get('text'), kind.capitalize(), 4000),
                                               responds_to=list(dict.fromkeys(targets)),
                                               evidence=evidence(row.get('evidence')), context=context)))
            if not entries:
                raise ValueError('Submit at least one challenge or concession')
            for kind, fields in entries:
                self.contribute(d, actor, kind, **fields)
        elif stage == 'proposal':
            challenges = ids('challenge')
            answers = []
            for row in payload.get('answers') or []:
                if not isinstance(row, dict) or row.get('challenge_id') not in challenges \
                        or row.get('status') not in ('accepted', 'rejected', 'open'):
                    raise ValueError('Each answer needs a challenge_id from this discussion and status accepted, rejected or open')
                answers.append({'challenge_id': row['challenge_id'], 'status': row['status'],
                                'note': text(row.get('note', ''), 'Answer note', 1000, False)})
            dissent = []
            for row in payload.get('dissent') or []:
                if not isinstance(row, dict) or not isinstance(row.get('by'), list) \
                        or any(b not in d['participants'] for b in row['by']):
                    raise ValueError('Each dissent entry needs a point and by: [participant handles]')
                dissent.append({'point': text(row.get('point'), 'Dissent point', 1000), 'by': row['by']})
            tasks = []
            for row in payload.get('tasks') or []:
                if not isinstance(row, dict) or len(tasks) >= 8:
                    raise ValueError('tasks must be at most 8 objects with title and details')
                tasks.append({'title': text(row.get('title'), 'Task title', 120),
                              'details': text(row.get('details', ''), 'Task details', 2000, False)})
            self.contribute(d, actor, 'proposal', text=text(payload.get('text'), 'Proposal', 8000),
                            agreed=items(payload.get('agreed'), 'Agreed point', 12, 1000), dissent=dissent,
                            unresolved=items(payload.get('unresolved'), 'Unresolved question', 12, 1000),
                            would_change=items(payload.get('would_change'), 'Evidence that would change it', 8, 1000),
                            answers=answers, tasks=tasks, context=context)
        else:
            current = self.proposal(d)
            if not current or payload.get('proposal_id') != current['id']:
                raise ValueError('proposal_id is not the current proposal; call get and answer the current one')
            if payload.get('verdict') not in ('agree', 'dissent'):
                raise ValueError('verdict must be agree or dissent')
            self.contribute(d, actor, 'verdict', proposal_id=current['id'], verdict=payload['verdict'],
                            reason=text(payload.get('reason'), 'Reason', 4000), context=context)
        d.setdefault('turns', {})[actor] = {'state': 'submitted', 'time': time.time()}
        self.settle(d)
        self.save()
        return self.view(d, reader=actor)

    def turn_finished(self, name, entry):
        """A participant whose turn ended (or was never admitted) without submitting shows 'No response yet'."""
        changed = []
        for request in entry.get('requests', []):
            token = request.get('decision')
            d = token and next((x for x in self.items if x['id'] == token.get('id')), None)
            if not d or token != self.token(d) or d.get('interrupted') or name not in self.owed(d):
                continue
            d.setdefault('turns', {})[name] = {'state': 'no_response', 'time': time.time(),
                                               'detail': 'Turn ended without a submission.'}
            changed.append(d)
        if changed:
            self.save()
            for d in changed:
                self.publish_soon(d)

    def interrupt(self, name=''):
        """Room stopped (no name) pauses every open discussion; a stopped agent only stops owing a turn."""
        changed = False
        for d in self.items:
            if d['stage'] not in OPEN or d.get('interrupted'):
                continue
            if not name:
                self.pause(d, 'The room was stopped.')
                changed = True
            elif name in self.owed(d):
                d.setdefault('turns', {})[name] = {'state': 'no_response', 'time': time.time(), 'detail': 'Agent stopped.'}
                changed = True
        if changed:
            self.save()

    # ---- chat integration ---------------------------------------------------------------------

    def human_message(self, channel, message_id):
        """A human message in a discussion chat is new direction: stale submissions stop counting and the
        user chooses Continue or Return. Returns True when the chat belongs to a discussion, so normal
        @mention routing must not wake agents outside the flow."""
        d = self.dedicated(channel)  # an in-place decision is reopened by its lead, never by every message
        if not d:
            return False
        if d['stage'] in OPEN and not d.get('interrupted'):
            self.pause(d, 'New direction from you.', message_id)
            self.save()
            self.publish_soon(d)
        return True

    def routes(self, channel):
        """Agent messages in a discussion's own chat never route; the flow alone queues turns. A chat that
        only hosts in-place decisions routes normally."""
        return self.dedicated(channel) is None

    def hidden(self, message, reader):
        """While positions are open, chat text from position turns is hidden from other participants."""
        chain = (message.get('metadata') or {}).get('chain')
        if not chain or message.get('sender') == reader:
            return False
        for d in self.items:
            if d.get('channel') != message.get('channel') or d['stage'] in CLOSED + ('draft',):
                continue
            rounds = d.get('chain_rounds') or {}
            if chain in rounds and rounds[chain] in self.sealed_rounds(d):
                return True  # a round still sealed, the current one or one reopened before its reveal
            if d['stage'] == 'positions' and chain in d.get('position_chains', [d['chain']]):
                return True  # records from before chain_rounds
        return False

    # ---- user actions (UI token only) -------------------------------------------------------

    def available(self):
        return [a['id'] for a in self.runtime.team_builder.agents()]

    def chat_slots(self):
        # Starting a discussion opens a chat; the UI shows this before Start (design 810e6c69).
        import project_registry
        folder = project_registry.active_folder(self.runtime)
        used = len(project_registry.sessions(folder)) if folder is not None else 0
        return {'used': used, 'limit': project_registry.SESSIONS_PER_PROJECT}

    def create(self, body):
        question = text(body.get('question'), 'Question', 2000)
        criteria = items(body.get('criteria'), 'Decision criterion', 8, 300)
        participants = body.get('participants')
        if not isinstance(participants, list) or not 2 <= len(set(participants)) <= MAX_PARTICIPANTS:
            raise ValueError(f'Choose between 2 and {MAX_PARTICIPANTS} participants')
        participants = list(dict.fromkeys(participants))
        unknown = [p for p in participants if p not in self.runtime.states]
        if unknown:
            raise ValueError(f'Not team members: {", ".join(unknown)}')
        lead = body.get('lead') or participants[0]
        if lead not in participants:
            raise ValueError('The lead must be one of the participants')
        d = dict(id=uuid.uuid4().hex[:12], question=question, criteria=criteria, participants=participants,
                 lead=lead, channel='', chain='', stage='draft', revision=1, epoch=1, returns_used=0,
                 edits_used=0, contributions=[], turns={}, interrupted='', record={},
                 created=time.time(), updated=time.time())
        self.items.append(d)
        self.save()
        return self.view(d)

    # ---- in-place decisions: opened from the conversation (Zero #12523, docs/decisions-in-chat-contract.md) --

    def is_human(self, message):
        sender = message.get('sender', '')
        registry = getattr(self.runtime.host, 'registry', None)
        return bool(sender) and sender != 'system' and sender not in self.runtime.states and not (
            registry and registry.is_agent_family(sender))

    def origin(self, channel, message_id):
        """The user's message a decision answers. Never an agent's message (security 365efc14 D4)."""
        if isinstance(message_id, bool) or not isinstance(message_id, int):
            raise ValueError('message_id must be the id of the user message the decision answers')
        message = self.runtime.host.store.get_by_id(message_id)
        if not message or message.get('channel') != channel:
            raise ValueError('That message is not in this chat')
        if not self.is_human(message):
            raise ValueError('A decision opens only on a message from the user, not from an agent')
        return message

    def may_open(self, actor, channel, message_id):
        """The user, the agent whose response claim answers that message, or the room's lead or backup."""
        if actor == 'user':
            return True
        claims = self.runtime.response_claims
        claim = claims.claims.get(f'{channel}:{message_id}') or {}
        return claim.get('owner') == actor or actor in claims.responders()

    def human_inputs(self, channel, value):
        """The user's ideas, quoted word for word from their own messages (security D3): a requirement is
        binding, a suggestion can lose with a stated reason. The user can reclassify either later."""
        if value is None:
            return []
        if not isinstance(value, list) or len(value) > MAX_INPUTS:
            raise ValueError(f'inputs must be a list of at most {MAX_INPUTS} entries')
        result = []
        for row in value:
            if not isinstance(row, dict) or row.get('kind') not in ('requirement', 'suggestion'):
                raise ValueError('Each input needs kind requirement or suggestion, a quote and the message_id it quotes')
            quote = text(row.get('quote'), 'Quote', 1000)
            source = self.origin(channel, row.get('message_id'))
            if quote not in source.get('text', ''):
                raise ValueError(f'Quote must be copied word for word from message #{source["id"]}')
            result.append({'quote': quote, 'kind': row['kind'], 'message_id': source['id'], 'author': source['sender']})
        return result

    def open(self, actor, body):
        """Create-or-get the decision on one user message, in that chat. A backup or a retry gets the same record."""
        channel, message_id = body.get('channel'), body.get('message_id')
        if not isinstance(channel, str) or not self.runtime.owns_channel(channel):
            raise ValueError('Open the decision in a chat of the current project')
        existing = next((d for d in self.items if d.get('origin') == [channel, message_id]), None)
        if existing:
            return existing, False
        self.origin(channel, message_id)
        if not self.may_open(actor, channel, message_id):
            raise ValueError('Only the user, or the lead or backup answering that message, can open a decision on it')
        if self.runtime.paused:
            raise ValueError('Resume the room before opening a decision')
        question = text(body.get('question'), 'Question', 2000)
        criteria = items(body.get('criteria'), 'Decision criterion', 8, 300)
        participants = body.get('participants')
        if not isinstance(participants, list) or any(not isinstance(p, str) for p in participants):
            raise ValueError('participants must be a list of agent ids')
        lead = actor if actor != 'user' else (body.get('lead') or (participants or [''])[0])
        participants = list(dict.fromkeys([lead] + participants))
        if not 2 <= len(participants) <= MAX_PARTICIPANTS:
            raise ValueError(f'Choose between 2 and {MAX_PARTICIPANTS} participants, the lead included')
        unavailable = [p for p in participants if p not in self.available()
                       or not self.runtime.response_claims.available(p)]
        if unavailable:
            raise ValueError(f'Not available to take part: {", ".join(unavailable)}')
        task_id = body.get('task_id') or ''
        if task_id and not any(t['id'] == task_id for t in self.runtime.project_room.state['tasks']):
            raise ValueError('task_id must be an existing task on the board; a decision never creates one')
        d = dict(id=uuid.uuid4().hex[:12], question=question, criteria=criteria, participants=participants,
                 lead=lead, channel=channel, chain='', stage='draft', revision=1, epoch=1, returns_used=0,
                 edits_used=0, contributions=[], turns={}, interrupted='', record={}, created=time.time(),
                 updated=time.time(), in_place=True, origin=[channel, message_id], opened_by=actor,
                 inputs=self.human_inputs(channel, body.get('inputs')), task_id=task_id, round=1, rounds=[])
        self.items.append(d)
        self.runtime.host.store.add(
            'system', f'Team decision {d["id"]} on #{message_id}, opened by {actor}: {question}\n'
            f'Participants: {", ".join(participants)}. Lead: {lead}. Positions stay sealed until everyone has submitted.',
            channel=channel, metadata={'no_route': True, 'decision': d['id']})
        self.enter(d, 'positions')
        return d, True

    def reopen(self, actor, d, reason):
        """Another round, started by the lead or the user when the user asks to reconsider. The finished
        round keeps its own reveal and conclusion status; nothing from it counts toward the new round."""
        if not d.get('in_place'):
            raise ValueError('Only an in-chat decision starts another round')
        if actor not in ('user', d['lead']):
            raise ValueError('Only the lead or the user can start another round')
        if d['stage'] in CLOSED + ('draft',):
            raise ValueError('This decision is not open')
        reason = text(reason, 'Reason for another round', 1000)
        current = self.proposal(d)
        concluded = d.get('round', 1) in d.get('conclusions_posted', [])
        d.setdefault('rounds', []).append({
            'round': d.get('round', 1), 'revealed': d['stage'] != 'positions', 'concluded': concluded,
            'outcome': self.outcome(d) if concluded else '', 'proposal_id': current['id'] if current else '',
            'coverage': self.coverage(d), 'reason': reason, 'by': actor, 'ended': time.time()})
        d['round'] = d.get('round', 1) + 1
        d['revision'] += 1
        d['epoch'] += 1
        d.update(interrupted='', return_question='', turns={})
        self.runtime.host.store.add('system', f'Team decision {d["id"]}: round {d["round"]} started by {actor}. {reason}',
                                    channel=d['channel'], metadata={'no_route': True, 'decision': d['id']})
        self.enter(d, 'positions')

    async def start(self, d):
        if d['stage'] != 'draft':
            raise ValueError('This discussion has already started')
        if self.runtime.paused:
            raise ValueError('Resume the room before starting a discussion')
        unavailable = [p for p in d['participants'] if p not in self.available()]
        if unavailable:
            raise ValueError(f'Resume or sign in these participants first: {", ".join(unavailable)}')
        import project_registry
        host = self.runtime.host
        session = project_registry.create_session(self.runtime, f'Decision {d["id"][:4]}: {d["question"]}'[:40].strip())
        host._save_settings()
        await host.broadcast_settings()
        await host.broadcast_session_created(session)
        d['channel'] = session['id']
        criteria = ''.join(f'\n- {c}' for c in d['criteria'])
        host.store.add('system', f'Team decision {d["id"]}: {d["question"]}{criteria}\nParticipants: '
                       + ', '.join(d['participants']) + f'. Lead: {d["lead"]}. Positions are hidden until everyone submits.',
                       channel=d['channel'], metadata={'no_route': True})
        self.enter(d, 'positions')

    def act(self, d, action, body):
        stage = d['stage']
        if action not in self.actions(d):
            raise ValueError(f'{action} is not available at stage {stage}')
        if action == 'stop':
            d['epoch'] += 1
            d.update(stage='stopped', turns={})
            self.arm(d)
        elif action == 'continue':
            d.update(interrupted='', turns={}, chain=self.runtime.project_room.new_chain(d['channel']))
            if stage == 'positions':
                # Chat from the paused round stays hidden too, until positions close.
                d.setdefault('position_chains', []).append(d['chain'])
                d.setdefault('chain_rounds', {})[d['chain']] = d.get('round', 1)
            self.queue(d)
        elif action == 'retry':
            name = body.get('participant')
            if name not in self.owed(d) or d.get('turns', {}).get(name, {}).get('state') == 'queued':
                raise ValueError('Retry a participant who still owes this stage and is not already queued')
            self.queue(d, [name])
        elif action == 'close_stage':
            self.advance(d)
        elif action == 'edit':
            current = self.proposal(d)
            d.update(revision=d['revision'] + 1, edits_used=d['edits_used'] + 1, interrupted='')
            d['epoch'] += 1
            d['stage'] = 'verdicts'
            self.contribute(d, 'user', 'edit', text=text(body.get('text'), 'Edited proposal', 8000),
                            edits=current['id'], tasks=current.get('tasks', []))
            self.enter(d, 'verdicts')
        elif action == 'return':
            question = text(body.get('question'), 'Question for the lead', 2000)
            d.update(revision=d['revision'] + 1, returns_used=d['returns_used'] + 1, return_question=question,
                     interrupted='')
            d['epoch'] += 1
            self.enter(d, 'proposal')
        elif action == 'record':
            self.record(d)
        elif action == 'reopen':
            self.reopen('user', d, body.get('reason') or 'The user asked for another round.')
        elif action == 'input_kind':
            index, kind = body.get('index'), body.get('kind')
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(d.get('inputs', [])) \
                    or kind not in ('requirement', 'suggestion'):
                raise ValueError('Choose one of your inputs and requirement or suggestion')
            d['inputs'][index].update(kind=kind, set_by='user')
        d['updated'] = time.time()
        return self.view(d)

    def record(self, d):
        current, outcome = self.proposal(d), self.outcome(d)
        key = f'{d["id"]}:{current["id"] if current else "-"}:{outcome}'
        record = d.get('record') or {}
        if record.get('key') not in (None, key):
            raise ValueError('This discussion was already recorded with a different outcome')
        room = self.runtime.project_room
        if not record.get('note_id'):
            votes = {c['author']: c for c in self.verdicts(d)}
            lines = [f'Team decision {d["id"]} recorded by the user ({outcome}): {d["question"]}']
            if current:
                lines.append(f'Proposal {current["id"]} (revision {d["revision"]}, by {current["author"]}): {current["text"]}')
                for key_name, label in (('agreed', 'Agreed'), ('unresolved', 'Unresolved')):
                    if current.get(key_name):
                        lines.append(f'{label}: ' + '; '.join(current[key_name]))
            superseded = set(d.get('superseded', []))
            labels = {'fresh': 'Fresh session', 'prior': 'Prior debate in context'}
            for name in d['participants']:
                vote = votes.get(name)
                # Reused context is disclosed, never called independent; skips are stated, never read as agreement.
                position = next((c for c in reversed(d['contributions']) if c['kind'] == 'position'
                                 and c['author'] == name and c['id'] not in superseded), None)
                skipped = list(dict.fromkeys(c['stage'] for c in d['contributions'] if c['kind'] == 'missing'
                                             and c['author'] == name))
                facts = [f'first answer: {labels.get(position.get("context"), "unknown context")}' if position
                         else 'no first answer']
                if skipped:
                    facts.append('skipped: ' + ', '.join(skipped))
                lines.append(f'{name} ({"; ".join(facts)}): '
                             + (f'{vote["verdict"]}, {vote["reason"]}' if vote else 'no verdict'))
            lines.append('Grants no deploy, restart, settings or credential authority.')
            note = '\n'.join(lines)
            if len(note) > NOTE_LIMIT:
                # Derived text: the discussion keeps the full record, so the note is shortened visibly.
                note = note[:NOTE_LIMIT - 60] + f' [...] (full record: decision {d["id"]})'
            record = {'key': key, 'note_id': room.note('user', 'decision', note)['id'], 'tasks': []}
            record['tasks'] = [{'title': t['title'], 'details': t['details']} for t in (current or {}).get('tasks', [])]
            d['record'] = record
        for item in record['tasks']:
            if item.get('id'):
                continue
            try:
                details = (item['details'] + '\n\n' if item['details'] else '') + f'From team decision {d["id"]}.'
                item['id'] = room.task('user', 'create', title=item['title'], details=details)['id']
                item.pop('error', None)
            except ValueError as exc:
                item['error'] = str(exc)
        d['stage'] = 'recorded' if all(t.get('id') for t in record['tasks']) else 'review'

    # ---- events -----------------------------------------------------------------------------

    async def publish(self, d):
        await self.runtime.emit('decision', self.view(d))

    def publish_soon(self, d):
        try:
            asyncio.get_running_loop().create_task(self.publish(d))
        except RuntimeError:
            pass


def install(host, runtime):
    flow = runtime.decisions

    @host.app.get('/api/decisions')
    async def listing():
        return {'decisions': [flow.summary(d) for d in reversed(flow.items)], 'available': flow.available(),
                'chat_slots': flow.chat_slots()}

    @host.app.get('/api/decisions/{identifier}')
    async def detail(identifier: str):
        try:
            return flow.view(flow.find(identifier))
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, 400)

    async def change(identifier, action, request):
        try:
            raw = await request.body()
            body = json.loads(raw) if raw.strip() else {}
            if not isinstance(body, dict):
                raise ValueError('Expected a JSON object')
            async with flow.lock:
                if action == 'create' and 'message_id' in body:
                    # The user opens an in-place decision on one of their messages (create-or-get).
                    result = flow.view(flow.open('user', body)[0])
                elif action == 'create':
                    result = flow.create(body)
                else:
                    d = flow.find(identifier)
                    if action == 'start':
                        await flow.start(d)
                        result = flow.view(d)
                    else:
                        result = flow.act(d, action, body)
                flow.save()
            await runtime.emit('decision', result)
            return result
        except (ValueError, TypeError, KeyError) as exc:  # json.JSONDecodeError is a ValueError
            return JSONResponse({'error': str(exc)}, 400)

    @host.app.post('/api/decisions')
    async def create(request: Request):
        return await change('', 'create', request)

    @host.app.post('/api/decisions/{identifier}/{action}')
    async def action(identifier: str, action: str, request: Request):
        return await change(identifier, action, request)
