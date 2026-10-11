"""The lead answers untagged human messages (Zero #12950; first built for @all, Zero #12480).

A human message that names nobody wakes one responder, the lead, who answers, delegates and wakes
owners of actionable tasks; everyone else sees the message as ordinary chat context on their next
turn. @all wakes the whole team and an @agent wakes that agent, without a claim. The room records a
response claim before the wake and checks it again before each post, so only a current answer
reaches the chat:

- The claim is keyed by channel and message id and carries a generation and the channel's scope
  revision. Stop or a newer human broadcast in the channel raises the scope revision, which makes
  every older claim in that channel stale: a reply written for it is held back (Activity only).
- If the lead is not running this claim's turn when BACKUP_SECONDS pass without an answer, exactly
  one backup takes the claim (generation 2). The lead's late reply then belongs to an old
  generation and is held back too. A backup that also fails ends the claim; nobody else is woken.
- Publication is idempotent on (claim, generation): a run that has not published yet is refused
  once a message for that generation exists in the chat store, so a restart or a replayed wake
  never posts the answer twice.

Only the lead's (or backup's) posts for that message go through the claim. Named @mentions, @all,
Stop and steering, review, follow-up, goal and task wakes reach agents exactly as before. A human
@all also raises the scope, so a lead answer still being written for an older message is held.
"""
import time

BACKUP_SECONDS = 120    # time the lead has to start or finish the answer before one backup takes over
EXTEND_SECONDS = 30     # re-check interval while the claim's owner is actively running its turn
BUSY_LIMIT_SECONDS = 600  # a lead busy with other work keeps a queued @all at most this long
KEEP_CLAIMS = 100       # finished claims kept for the record; open ones are never pruned
STORE_SCAN = 400        # recent chat messages checked for an already published answer


class ResponseClaims:
    def __init__(self, runtime):
        self.runtime = runtime

    # ----- durable state (the project's coordination.json) -----
    @property
    def state(self):
        return self.runtime.project_room.state

    @property
    def claims(self):
        return self.state.setdefault('response_claims', {})

    def scope(self, channel):
        return self.state.setdefault('response_scope', {}).get(channel, 0)

    def bump_scope(self, channel=None):
        """A scope change: Stop (every channel) or a newer human broadcast (its channel)."""
        scopes = self.state.setdefault('response_scope', {})
        for key, claim in self.claims.items():
            if claim['status'] == 'open' and channel in (None, claim['channel']):
                claim.update(status='superseded', ended=time.time())
        for name in ([channel] if channel else {c['channel'] for c in self.claims.values()} | set(scopes)):
            scopes[name] = scopes.get(name, 0) + 1

    def save(self):
        done = [k for k, c in self.claims.items() if c['status'] != 'open']
        for key in sorted(done, key=lambda k: self.claims[k].get('created', 0))[:-KEEP_CLAIMS or None]:
            del self.claims[key]
        self.runtime.project_room.save()

    # ----- who answers -----
    def available(self, name):
        state = self.runtime.states.get(name)
        return bool(state) and not (state.get('paused') or state.get('archived') or state.get('desktop_unavailable')
                                    or state.get('state') in ('blocked', 'login_required'))

    def responders(self):
        """(lead, backup), the first two available in one order: the chosen lead, the chosen backup, the
        active goal lead, the configured manager, then roster order. So an unavailable chosen lead hands
        the answer to the chosen backup (QA 2d8e271c), never to whoever sorts first. Never the same agent."""
        r = self.runtime
        chosen = self.state.get('responders') or {}
        goal = getattr(r.goals, 'goal', None) or {}
        manager = (getattr(getattr(r, 'manager_wakes', None), 'record', None) or {}).get('manager')
        order = [chosen.get('lead'), chosen.get('backup'), goal.get('owner') if goal.get('status') == 'active' else None,
                 manager, *sorted(r.states)]
        picked = list(dict.fromkeys(n for n in order if n and self.available(n)))
        return (picked + [None, None])[0], (picked + [None, None])[1]

    def set_responders(self, lead, backup):
        """Human-only setting (the HTTP route checks the UI token); empty values return to the defaults."""
        for name in (lead, backup):
            if name and name not in self.runtime.states:
                raise ValueError('Choose registered participants for the lead and backup')
        if lead and lead == backup:
            raise ValueError('The backup must be a different participant from the lead')
        self.state['responders'] = {'lead': lead or '', 'backup': backup or ''}
        self.runtime.project_room.save()
        return self.state['responders']

    # ----- the claim lifecycle -----
    def open(self, channel, message_id):
        """Record the claim for an untagged human message before anyone is woken. Returns it, or None when
        no participant can answer. A newer message supersedes every open claim in the channel."""
        self.bump_scope(channel)
        lead, backup = self.responders()
        if not lead:
            self.save()
            self.runtime.host.store.add('system', 'No one is available to answer this message. Resume an agent, mention '
                                        'one directly, or use @all.', channel=channel, metadata={'no_route': True})
            return None
        key = f'{channel}:{message_id}'
        now = time.time()
        self.claims[key] = {'key': key, 'channel': channel, 'message_id': message_id, 'owner': lead, 'backup': backup,
                            'generation': 1, 'scope': self.scope(channel), 'status': 'open', 'created': now,
                            'deadline': now + BACKUP_SECONDS, 'published': []}
        self.save()
        return self.claims[key]

    @staticmethod
    def grant(run):
        """The (claim key, generation) this run answers, or None: the latest claim request the provider
        accepted as steering mid-turn, else the one the turn was started for."""
        steered = [q for q in getattr(run.steering, 'pending', []) if q.get('response_claim') and q.get('_steer_delivered')]
        started = [q for q in run.entry.get('requests', []) if q.get('response_claim')]
        request = (steered or started or [None])[-1]
        return (request['response_claim'], request.get('generation', 1)) if request else None

    def check(self, run):
        """'' when this run may publish to the claim it was woken for, else the reason it may not. Called
        just before each post; the same run may post more than once (text before tool calls, then its
        final message)."""
        grant = self.grant(run)
        if not grant:
            return ''
        key, generation = grant
        claim = self.claims.get(key)
        if not claim:
            return 'the answer claim no longer exists'
        if claim['owner'] != run.name or claim['generation'] != generation:
            return 'another participant now owns the answer'
        if claim['status'] not in ('open', 'answered'):
            return f"the question was {claim['status']}"
        if claim['scope'] != self.scope(claim['channel']):
            return 'the scope changed since the question was asked'
        others = [p for p in claim['published'] if p.get('generation') == generation and p.get('run') != run.id]
        if others or self.published_elsewhere(claim, generation, run.id):
            return 'this answer was already published'
        return ''

    def published_elsewhere(self, claim, generation, run_id):
        """The chat store is the authority: a message for this (claim, generation) from another run
        means the answer is out, even if the claim record missed it (crash between add and save)."""
        for message in self.runtime.host.store.get_recent(STORE_SCAN, channel=claim['channel']):
            meta = message.get('metadata') or {}
            if (meta.get('response_claim') == claim['key'] and meta.get('generation') == generation
                    and meta.get('run_id') != run_id):
                return True
        return False

    def metadata(self, run):
        grant = self.grant(run)
        return {'response_claim': grant[0], 'generation': grant[1]} if grant else {}

    def published(self, run, message_id):
        grant = self.grant(run)
        claim = self.claims.get(grant[0]) if grant else None
        if claim:
            claim['published'].append({'run': run.id, 'generation': grant[1], 'message': message_id, 'time': time.time()})
            claim['status'] = 'answered'
            self.save()

    def tick(self):
        """Heartbeat: give an unanswered claim to exactly one backup; never wake the roster."""
        now, changed = time.time(), False
        for claim in list(self.claims.values()):
            if claim['status'] != 'open' or now < claim['deadline']:
                continue
            owner = claim['owner']
            run = self.runtime.running.get(owner)
            if run and (self.grant(run) or (None,))[0] == claim['key']:
                claim['deadline'] = now + EXTEND_SECONDS  # actively answering: wait for it
                changed = True
                continue
            if run and self.waiting_for(owner, run, claim['key']) and now - claim['created'] < BUSY_LIMIT_SECONDS:
                # The owner is mid-turn on other work and the @all waits in its queue or steering: it
                # gives the better-informed answer once that turn ends, so don't hand it on yet.
                claim['deadline'] = now + EXTEND_SECONDS
                changed = True
                continue
            backup = claim.get('backup')
            if claim['generation'] == 1 and backup and self.available(backup):
                claim.update(owner=backup, generation=2, deadline=now + BACKUP_SECONDS)
                changed = True
                self.save()
                self.wake(claim, backup)
            else:
                claim.update(status='expired', ended=now)
                changed = True
                self.runtime.host.store.add('system', 'Nobody answered this message in time. Mention the agent you need '
                                            'directly, or use @all to wake the whole team.', channel=claim['channel'],
                                            metadata={'no_route': True, 'response_claim': claim['key']})
        if changed:
            self.save()

    def waiting_for(self, owner, run, key):
        """True when owner still holds this claim's request: queued behind its current turn, or offered
        to that turn as steering the provider has not accepted yet."""
        queue = self.runtime.queues.get(owner)
        queued = [r for batch in (list(queue._queue) if queue is not None else []) for r in batch.get('requests', [])]
        steering = [r for r in getattr(run.steering, 'pending', []) if not r.get('_steer_delivered')]
        return any(r.get('response_claim') == key for r in queued + steering)

    def wake(self, claim, name):
        message = self.runtime.host.store.get_by_id(claim['message_id']) or {}
        text = f"{message.get('sender', 'user')}: {message.get('text', '')}"
        routing = {'message_id': claim['message_id'], 'human': True, 'response_claim': claim['key'],
                   'generation': claim['generation']}
        if claim['generation'] > 1:
            text = ("You are the backup for this message: the lead did not answer in time and nobody else was "
                    "woken. Give the one answer. If it means work should move, check the open tasks listed in your "
                    "context (room_task get for detail) and mention each agent with an unblocked next step, one "
                    "line each with that step. Mention nobody who has nothing to do.\n\n" + text)
        self.runtime.enqueue(name, claim['channel'], text, chain=None, routing=routing)
