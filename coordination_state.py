"""Task execution facts. Waiting is derived from records, never from elapsed time."""


def wait_record(task, state):
    wait = task.get('wait') or {}
    collection = {'followup': 'followups', 'review': 'reviews'}.get(wait.get('kind'))
    if not collection:
        return None
    return next((item for item in state.get(collection, [])
                 if item.get('id') == wait.get('record_id') and item.get('task_id') == task['id']), None)


def pending_human_wait(task, state):
    item = wait_record(task, state)
    if not item:
        return False
    if task['wait']['kind'] == 'followup':
        return item.get('assigned_to') == 'user' and item.get('status') == 'open'
    return item.get('reviewer') == 'user' and item.get('status') == 'pending'


def update_execution(task, state, *, next_action=None, priority=None, wait=None):
    """Validate an update atomically; callers enforce task ownership and persist."""
    result = dict(task)
    if next_action is not None:
        if not isinstance(next_action, str) or len(next_action.strip()) > 2400:
            raise ValueError('Next action must be text of at most 2400 characters')
        result['next_action'] = next_action.strip()
    if priority is not None:
        if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 5:
            raise ValueError('Priority must be an integer from 1 (highest) to 5')
        result['priority'] = priority
    if wait is not None:
        if not isinstance(wait, dict):
            raise ValueError('Wait must be an object; use {} to clear it')
        if not wait:
            result.pop('wait', None)
        else:
            if set(wait) != {'kind', 'record_id'} or wait.get('kind') not in ('followup', 'review'):
                raise ValueError('Human wait needs kind followup/review and record_id')
            result['wait'] = dict(wait)
            if not pending_human_wait(result, state):
                raise ValueError('Human wait must link an open request to the user on this task')
            if not result.get('next_action'):
                raise ValueError('Record the next action to take after the user responds')
    return result


def execution_state(task, state, *, running=False, queued=False):
    """Report observed execution separately from the task's manually selected status."""
    if task.get('status') == 'done':
        return 'done'
    if running:
        return 'executing'
    if queued:
        return 'queued'
    if pending_human_wait(task, state):
        return 'waiting_on_human'
    if task.get('wait'):
        # A reply/cancellation/missing record requires an owner check, never implied approval.
        return 'needs_attention'
    if task.get('stall'):
        # Ready work that isn't moving (work_continuity C4): the lead has an item for it; it clears when it moves.
        return 'needs_attention'
    if task.get('status') == 'blocked':
        return 'blocked'
    dependencies = set(task.get('depends_on', []))
    done = {item['id'] for item in state.get('tasks', []) if item.get('status') == 'done'}
    if dependencies - done:
        return 'waiting_on_dependency'
    if task.get('status') == 'review':
        return 'in_review'
    return 'idle' if task.get('owner') else 'unassigned'
