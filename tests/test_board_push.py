"""The board push (task eaba007f): deltas against the last push, resets when a delta can't express the change, and a
client that applies them as the contract says ends on the same board as a fresh GET."""
import asyncio
import copy
import json
import random
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from board_push import BoardPush, COLLECTIONS, ENVELOPE, detached


def board():
    return {'spec': 'build it', 'goal': None, 'limits': {'agent_turns': 0},
            'tasks': [{'id': f't{i}', 'title': f'task {i}', 'owner': 'a', 'observed_at': 0.0} for i in range(4)],
            'reviews': [{'id': 'r1', 'status': 'pending'}], 'followups': [], 'notes': [{'id': 'n1', 'time': 1}]}


class Room:
    def __init__(self):
        self.state, self.identity, self.clock = board(), 'p1', 0.0

    def snapshot(self):
        """Copies as ProjectRoom.snapshot does: new top-level dict, new task and review views, but follow-ups, notes
        and every nested value shared with the live state."""
        self.clock += 1  # task views are rebuilt with a new observed_at on every snapshot
        return {**self.state, 'tasks': [{**t, 'observed_at': self.clock} for t in self.state['tasks']],
                'reviews': [dict(r) for r in self.state['reviews']]}


def apply(client, kind, message):
    """The client side of the contract (follow-up 10a8687b), as a reference."""
    if kind == 'project_state':
        return copy.deepcopy(message)
    assert (message['project_id'], message['stream_id'], message['base']) == \
        (client['project_id'], client['stream_id'], client['rev'])
    client = copy.deepcopy(client)
    for collection, ids in message.get('remove', {}).items():
        client[collection] = [r for r in client[collection] if r['id'] not in ids]
    for collection, records in message.get('upsert', {}).items():
        index = {r['id']: n for n, r in enumerate(client[collection])}
        for record in records:
            if record['id'] in index:
                client[collection][index[record['id']]] = record
            else:
                client[collection].append(record)
    for key, value in message.get('values', {}).items():
        client[key] = copy.deepcopy(value)  # null is a value: a cleared or removed key reads as null
    client['rev'] = message['rev']
    return client


def without_time(state):
    """For comparison: observed_at dropped, and a missing top-level key the same as null (the agreed convention)."""
    state = {key: value for key, value in copy.deepcopy(state).items() if value is not None}
    for task in state.get('tasks', []):
        task.pop('observed_at', None)
    return state


class BoardPushTest(unittest.TestCase):
    def setUp(self):
        self.room = Room()
        self.push = BoardPush(SimpleNamespace(project_room=self.room, identity='p1'))
        self.push.runtime.project_room = self.room

    def advance(self):
        self.push.runtime.identity = self.room.identity
        return self.push.advance()

    def test_first_push_is_a_reset_and_an_unchanged_board_pushes_nothing(self):
        snapshot, (kind, message) = self.advance()
        self.assertEqual(kind, 'project_state')
        self.assertEqual((snapshot['project_id'], snapshot['rev']), ('p1', 1))
        self.assertTrue(snapshot['stream_id'])
        again, message = self.advance()  # only observed_at moved
        self.assertIsNone(message)
        self.assertEqual(again['rev'], 1)

    def test_a_change_is_a_delta_of_only_what_changed(self):
        self.advance()
        self.room.state['tasks'][2]['title'] = 'renamed'
        self.room.state['followups'].append({'id': 'f1', 'status': 'open'})
        self.room.state['tasks'].pop(0)
        self.room.state['spec'] = 'changed'
        del self.room.state['goal']
        snapshot, (kind, delta) = self.advance()
        self.assertEqual(kind, 'project_delta')
        self.assertEqual((delta['base'], delta['rev'], snapshot['rev']), (1, 2, 2))
        self.assertEqual([t['id'] for t in delta['upsert']['tasks']], ['t2'])
        self.assertEqual(delta['upsert']['followups'], [{'id': 'f1', 'status': 'open'}])
        self.assertEqual(delta['remove'], {'tasks': ['t0']})
        self.assertEqual(delta['values'], {'spec': 'changed', 'goal': None})
        self.assertNotIn('reviews', delta['upsert'])

    def test_a_reorder_or_a_new_project_is_a_reset(self):
        first, _ = self.advance()
        self.room.state['notes'].insert(0, {'id': 'n0', 'time': 0})  # add_note keeps notes sorted by time
        snapshot, (kind, _) = self.advance()
        self.assertEqual((kind, snapshot['rev'], snapshot['stream_id']), ('project_state', 2, first['stream_id']))
        self.room.identity = 'p2'
        switched, (kind, _) = self.advance()
        self.assertEqual((kind, switched['project_id'], switched['rev']), ('project_state', 'p2', 1))
        self.assertNotEqual(switched['stream_id'], first['stream_id'])

    def test_records_without_ids_are_pushed_whole_only_when_they_change(self):
        self.room.state['notes'] = [{'kind': 'progress', 'text': 'an older project'}]
        self.advance()
        self.assertIsNone(self.advance()[1])
        self.room.state['spec'] = 'changed'
        self.assertEqual(self.advance()[1][0], 'project_state')

    def test_the_first_comparable_board_after_a_whole_push_is_a_reset(self):
        self.room.state['notes'] = [{'kind': 'progress', 'text': 'no id'}]  # frontend f7e96ca6, critical 1
        self.advance()
        self.room.state['tasks'] = []
        self.room.state['notes'] = [{'id': 'n1', 'time': 1}]
        snapshot, (kind, message) = self.advance()
        self.assertEqual(kind, 'project_state')  # upserts against an empty baseline could never remove t0 to t3
        self.assertEqual(message['tasks'], [])
        self.room.state['spec'] = 'next'
        self.assertEqual(self.advance()[1][0], 'project_delta')

    def test_a_change_during_the_push_await_never_reaches_that_rev(self):
        from room_runtime import RoomRuntime  # frontend f7e96ca6, critical 2
        self.advance()
        followup = {'id': 'f1', 'status': 'open'}
        self.room.state['followups'].append(followup)  # the live list: snapshots share it
        runtime = SimpleNamespace(board_push=self.push, emitted=[])

        async def emit(kind, data):
            followup['status'] = 'resolved'  # another handler runs while the broadcast awaits
            runtime.emitted.append(data)
        runtime.emit = emit
        response = asyncio.run(RoomRuntime.board_response(runtime, {'last_human_message': ''}))
        body = json.loads(response.body)
        self.assertEqual(body['followups'], [{'id': 'f1', 'status': 'open'}])
        self.assertEqual(runtime.emitted[0]['upsert']['followups'], [{'id': 'f1', 'status': 'open'}])
        self.assertEqual(body['rev'], runtime.emitted[0]['rev'])
        self.assertNotIn('last_human_message', runtime.emitted[0])
        _, (kind, later) = self.advance()  # the resolve goes out in the next rev
        self.assertEqual(later['upsert']['followups'], [{'id': 'f1', 'status': 'resolved'}])

    def test_a_client_applying_every_push_matches_a_fresh_snapshot(self):
        rng = random.Random(7)
        client, (kind, message) = None, self.advance()[1]
        client = apply(client, kind, message)
        for step in range(300):
            state = self.room.state
            collection = rng.choice(COLLECTIONS)
            items, roll = state[collection], rng.random()
            if roll < 0.35 or not items:
                items.append({'id': f'{collection[0]}{step}', 'value': step, 'owner': rng.choice('abc')})
            elif roll < 0.5:
                items.pop(rng.randrange(len(items)))
            elif roll < 0.6:
                items.insert(0, items.pop())  # occasional reorder: must reset, never mis-apply
            elif roll < 0.85:
                rng.choice(items)['value'] = step
            elif roll < 0.92:
                state['goal'] = None if state.get('goal') else {'text': f'goal {step}'}  # to and from a real null
            else:
                state['spec'] = f'spec {step}'
            snapshot, pushed = self.advance()
            if pushed:
                client = apply(client, *detached(pushed))
            self.assertEqual(without_time(client), without_time(snapshot), f'step {step}')
            self.assertTrue(all(client[key] == snapshot[key] for key in ENVELOPE))


if __name__ == '__main__':
    unittest.main()
