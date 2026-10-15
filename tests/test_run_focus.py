"""Which task a turn's speech belongs on (task b5526ef8): seeded from the requests that woke it, moved only by a
deliberate start, and falling back when the focused task ends. The Board and Live place speech by it."""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from room_runtime import RoomRuntime, request_focus


def task(task_id, owner='desktop', status='working'):
    return {'id': task_id, 'owner': owner, 'status': status}


class Runtime:
    """RoomRuntime's focus methods on a plain board, recording the events they send."""
    run_focus = RoomRuntime.run_focus
    refocus = RoomRuntime.refocus

    def __init__(self, tasks):
        self.project_room = SimpleNamespace(state={'tasks': tasks})
        self.events = []

    async def emit(self, kind, data, persist=False):
        self.events.append((kind, data))


class Run(SimpleNamespace):
    task_id = property(lambda self: self.focus)  # Run.task_id, outside a sealed decision position


def run_for(runtime, requests, name='desktop'):
    seed = request_focus({'requests': requests})
    run = Run(id='r1', name=name, channel='c', focus_seed=seed, focus_history=[seed] if seed else [], focus=None)
    run.focus = runtime.run_focus(run)  # as the turn starts
    return run


def change(runtime, run, arguments, result):
    asyncio.run(runtime.refocus(run, arguments, result))
    return run.focus


class RequestFocusTest(unittest.TestCase):
    def test_newest_human_request_wins_then_newest_named_then_none(self):
        wake = {'task_id': 'a', 'human': False}
        delegated = {'ref': {'collection': 'tasks', 'id': 'b'}, 'human': False}
        human = {'task_id': 'c', 'human': True}
        chat = {'prompt': 'hello', 'human': True}
        self.assertEqual(request_focus({'requests': [wake, human, delegated, chat]}), 'c')
        self.assertEqual(request_focus({'requests': [wake, delegated, chat]}), 'b')
        self.assertIsNone(request_focus({'requests': [chat, {'ref': {'collection': 'reviews', 'id': 'x'}}]}))


class RefocusTest(unittest.TestCase):
    def test_two_running_cards_place_speech_on_the_task_it_was_woken_for(self):
        runtime = Runtime([task('c148'), task('01e0', status='review')])
        run = run_for(runtime, [{'task_id': '01e0', 'human': False}])
        self.assertEqual(run.focus, '01e0')
        # A check-in on a task that stays in review is bookkeeping: it does not move the focus.
        self.assertEqual(change(runtime, run, {'action': 'update', 'task_id': '01e0'}, task('01e0', status='review')), '01e0')
        self.assertEqual(runtime.events, [])

    def test_a_deliberate_start_moves_it_and_deferring_or_closing_another_task_does_not(self):
        board = [task('a'), task('b', status='open'), task('c')]
        runtime = Runtime(board)
        run = run_for(runtime, [{'task_id': 'a', 'human': False}])
        board[1]['status'] = 'working'
        self.assertEqual(change(runtime, run, {'action': 'claim', 'task_id': 'b'}, board[1]), 'b')
        self.assertEqual(runtime.events[-1], ('runtime_focus', {'run': 'r1', 'agent': 'desktop', 'channel': 'c', 'task_id': 'b'}))
        self.assertEqual(change(runtime, run, {'action': 'update', 'task_id': 'c', 'next_action_state': 'waiting'}, board[2]), 'b')
        self.assertEqual(change(runtime, run, {'action': 'update', 'task_id': 'c', 'wait_on': [{'task': 'a'}]}, board[2]), 'b')
        self.assertEqual(change(runtime, run, {'action': 'update', 'task_id': 'c', 'update': 'progress'}, board[2]), 'c')

    def test_ending_the_focused_task_falls_back_then_clears(self):
        board = [task('a'), task('b')]
        runtime = Runtime(board)
        run = run_for(runtime, [{'task_id': 'a', 'human': False}])
        change(runtime, run, {'action': 'claim', 'task_id': 'b'}, board[1])
        board[1]['status'] = 'done'
        self.assertEqual(change(runtime, run, {'action': 'update', 'task_id': 'b', 'status': 'done'}, board[1]), 'a')
        board[0]['owner'] = 'qa'  # the seed stays valid for anyone's card: it is the task this turn was woken for
        self.assertEqual(change(runtime, run, {'action': 'release', 'task_id': 'a'}, board[0]), 'a')
        board[0]['status'] = 'done'
        self.assertIsNone(change(runtime, run, {'action': 'update', 'task_id': 'a'}, board[0]))
        self.assertEqual(runtime.events[-1][1]['task_id'], None)

    def test_a_task_claimed_and_then_handed_away_is_no_longer_the_focus(self):
        board = [task('a', status='open')]
        runtime = Runtime(board)
        run = run_for(runtime, [{'prompt': 'chat', 'human': True}])
        self.assertIsNone(run.focus)
        self.assertEqual(change(runtime, run, {'action': 'claim', 'task_id': 'a'}, board[0]), 'a')
        board[0]['owner'] = 'qa'
        self.assertIsNone(change(runtime, run, {'action': 'delegate', 'task_id': 'a', 'owner': 'qa'}, board[0]))


if __name__ == '__main__':
    unittest.main()
