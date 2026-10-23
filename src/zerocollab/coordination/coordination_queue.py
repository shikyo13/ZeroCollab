"""Coalesce pending wake-ups; resolve record references immediately before delivery."""
import asyncio

# Admission budget per channel batch; prevents one busy chat bypassing slot limits.
MAX_BATCH_REQUESTS = 128
MAX_BATCH_PROMPT_BYTES = 1024 * 1024  # Retained prompt payload budget per admitted batch.


def receipt_key(request):
    ref = request.get('ref')
    if ref:
        return f"{ref['collection']}:{ref['id']}:{ref['updated']}"
    if request.get('release_key'):
        # A release completion (<deployment>:<recipient>): merged once per batch and, once its turn has
        # completed, never delivered again, whichever restore path replays it (Astra c61b1280 r3).
        return f"release:{request['release_key']}"
    if request.get('message_id') is not None:
        return f"message:{request['message_id']}"
    return ''


class WakeQueue(asyncio.Queue):
    """One pending batch per channel. Human instructions remain in arrival order."""
    @staticmethod
    def merge(batch, incoming):
        staged = {**batch, 'requests': list(batch['requests'])}
        WakeQueue._merge(staged, incoming)
        WakeQueue._check_budget(staged)
        batch.update(staged)

    @staticmethod
    def _merge(batch, incoming):
        for request in incoming['requests']:
            ref = request.get('ref')
            if ref:
                batch['requests'][:] = [r for r in batch['requests']
                    if not (r.get('ref', {}).get('collection') == ref['collection']
                            and r.get('ref', {}).get('id') == ref['id'])]
            key = receipt_key(request)
            if not key or all(receipt_key(r) != key for r in batch['requests']):
                batch['requests'].append(request)
        batch['chain'] = incoming['chain']

    @staticmethod
    def _check_budget(batch):
        if len(batch['requests']) > MAX_BATCH_REQUESTS:
            raise asyncio.QueueFull
        remaining = MAX_BATCH_PROMPT_BYTES
        for request in batch['requests']:
            prompt = request.get('prompt', '')
            # UTF-8 needs at least one byte per character; reject huge strings
            # before allocating an encoded copy merely to measure them.
            if len(prompt) > remaining:
                raise asyncio.QueueFull
            remaining -= len(prompt.encode('utf-8'))
            if remaining < 0:
                raise asyncio.QueueFull

    def put_nowait(self, item):
        for pending in reversed(self._queue):
            if pending['channel'] == item['channel']:
                self.merge(pending, item)
                return
        self._check_budget(item)
        super().put_nowait(item)

    def restore(self, item):
        """Replay already-admitted work verbatim, including pre-limit checkpoints."""
        self._put(item)
        self._unfinished_tasks += 1
        self._finished.clear()
        self._wakeup_next(self._getters)

    def retry(self, item):
        """Put a failed batch back ahead of newer work, so later same-channel requests merge after it."""
        self._queue.appendleft(item)
        self._unfinished_tasks += 1
        self._finished.clear()
        self._wakeup_next(self._getters)

    def merge_waiting(self, batch):
        for pending in list(self._queue):
            if pending['channel'] == batch['channel']:
                try:
                    self.merge(batch, pending)
                except asyncio.QueueFull:
                    break  # Keep this and later batches in arrival order for the next turn.
                self._queue.remove(pending)
                self._wakeup_next(self._putters)


def current_request(request, actor, state, receipts):
    """Reading an actionable request is not completing it; record state wins."""
    ref = request.get('ref')
    if not ref:
        return request.get('human', False) or receipt_key(request) not in receipts
    item = next((r for r in state.get(ref['collection'], []) if r['id'] == ref['id']), None)
    if not item or item.get('updated') != ref['updated']:
        return False
    if ref['collection'] == 'reviews':
        if item['status'] in ('superseded', 'cancelled'):
            return False
        if ref['action'] == 'request':
            return item['status'] == 'pending' and item['reviewer'] == actor
        task = next((t for t in state['tasks'] if t['id'] == item['task_id']), {})
        # A verdict is for the task's owner and for the agent whose work it reviewed (task e14159ba).
        if task.get('status') == 'done' or (task.get('owner') != actor and item.get('requested_by') != actor):
            return False
    elif ref['collection'] == 'followups':
        if ref['action'] == 'create':
            return item['status'] == 'open' and item['assigned_to'] == actor
        if item['status'] == 'cancelled':
            return False
    elif ref['collection'] == 'tasks':
        if ref.get('action') == 'delegate' and (item.get('transfer') or {}).get('to') == actor:
            return True  # pending transfer: the recipient still has to accept it
        if ref.get('action') == 'ready':
            # Re-check at delivery: an upstream task can reopen while the wake waits for capacity.
            done = {t['id'] for t in state['tasks'] if t['status'] == 'done'}
            return item['owner'] == actor and item['status'] != 'done' and set(item.get('depends_on', [])) <= done
        return item['owner'] == actor and item['status'] == 'open'
    return receipt_key(request) not in receipts
