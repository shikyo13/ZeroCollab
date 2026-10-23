"""Forward same-channel input once; provider acknowledgment owns delivery."""
import asyncio

from zerocollab.coordination.coordination_queue import current_request, receipt_key


class SteeringDelivery:
    def __init__(self, run):
        self.run = run
        self.sender = None
        self.pending = []
        self.task = None
        self.closed = False

    def install(self, sender):
        self.sender = sender
        self.kick()

    def offer(self, request):
        if self.closed or request.get('goal_token'):
            return
        # Ordinary peer conversation retains normal handoff/budget admission.
        if not request.get('human') and not request.get('ref'):
            return
        key = receipt_key(request)
        if any(r is request or (key and receipt_key(r) == key) for r in self.pending):
            return
        request['_steering'] = 'queued'
        self.pending.append(request)
        self.kick()

    def kick(self):
        if not self.closed and self.sender and (self.task is None or self.task.done()):
            self.task = asyncio.create_task(self.dispatch())

    async def status(self, request, status):
        request['_steering'] = status
        await self.run.manager.emit('runtime_steering', {
            'run': self.run.id, 'agent': self.run.name, 'channel': self.run.channel,
            'message_id': request.get('message_id'), 'ref': request.get('ref'),
            'status': status,
        }, persist=True)

    async def dispatch(self):
        for request in self.pending:
            if self.closed:
                return
            if request.get('_steering') != 'queued':
                continue
            manager = self.run.manager
            if manager.paused or manager.states[self.run.name].get('paused'):
                return
            if not current_request(request, self.run.name, manager.project_room.state, self.run.receipts):
                await self.status(request, 'superseded')
                continue
            await self.status(request, 'sending')
            try:
                text = ('New human direction (takes precedence over earlier instructions):\n'
                        if request.get('human') else 'New collaboration update:\n') + request['prompt']
                sent = await asyncio.wait_for(self.sender(text, request), timeout=10)
                if request.get('_steering') != 'accepted':
                    await self.status(request, 'sent' if sent else 'queued_for_next_turn')
            except asyncio.CancelledError:
                raise
            except Exception:
                # A transport error is not acceptance. Original remains queued.
                await self.status(request, 'queued_for_next_turn')

    async def delivered(self, request):
        if self.closed or request.get('_steering') == 'accepted':
            return
        if not any(r is request for r in self.pending):
            return
        request['_steer_delivered'] = True
        key = receipt_key(request)
        if key:
            self.run.receipts.add(key)
        await self.status(request, 'accepted')

    async def close(self, outcome):
        if self.closed:
            return
        self.closed = True
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        for request in self.pending:
            if request.get('_steering') not in ('accepted', 'superseded'):
                await self.status(request, 'cancelled' if outcome == 'cancelled' else
                                  'not_delivered_failed' if outcome == 'failed' else 'queued_for_next_turn')
