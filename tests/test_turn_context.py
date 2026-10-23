"""Turn context: messages addressed to the reader are delivered whole, or say exactly where they continue."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zerocollab.coordination.project_room import ProjectRoom
from zerocollab.storage.store import MessageStore


class TurnContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        data = Path(self.tmp.name)
        self.store = MessageStore(str(data / 'messages.jsonl'))
        self.r = SimpleNamespace(data=data, cfg={'server': {'port': 1}}, host=SimpleNamespace(store=self.store),
                                 project=None, identity='p',
                                 states={'opus': {'label': 'Opus', 'provider': 'claude'}, 'astra': {'label': 'Astra', 'provider': 'codex'}},
                                 diffs=lambda: {}, channel_title=lambda c: '#' + c)
        self.room = ProjectRoom(self.r)
        self.room.use_project('p')

    def run_for(self, *messages):
        requests = [{'prompt': f"{m['sender']}: {m['text']}", 'message_id': m['id'], 'human': True} for m in messages]
        return SimpleNamespace(name='opus', channel='general', previous_session={}, receipts=set(), worktree='W:/scratch/opus',
                               entry={'chain': 'c', 'requests': requests})

    def test_all_mention_asks_to_check_teammates_before_replying(self):
        m = self.store.add('user', '@all catch up on the board', channel='general')
        run = self.run_for(m)
        prompt = self.room.context(run)
        self.assertIn(f"[Sent to every agent. Before replying, read messages after #{m['id']} with room_read", prompt)
        self.assertIn(f"message:{m['id']}", run.receipts)
        direct = self.store.add('user', '@opus only you', channel='general')
        self.assertNotIn('Sent to every agent', self.room.context(self.run_for(direct)).split('Current message or task:')[1])

    def test_long_mention_is_whole_and_appears_once(self):
        text = '@opus ' + 'step. ' * 700 + 'THE END'
        m = self.store.add('user', text, channel='general')
        run = self.run_for(m)
        prompt = self.room.context(run)
        self.assertIn('THE END', prompt)
        self.assertEqual(prompt.count('step. '), 700)
        self.assertIn(f"#{m['id']} user: [Addressed to you; full text under Current message below.]", prompt)
        self.assertIn(f"message:{m['id']}", run.receipts)

    def test_too_long_mention_names_where_it_continues(self):
        text = '@opus ' + 'x' * 20000 + 'THE END'
        m = self.store.add('user', text, channel='general')
        run = self.run_for(m)
        prompt = self.room.context(run)
        self.assertNotIn('THE END', prompt)
        self.assertRegex(prompt, rf'Message #{m["id"]} from user continues for \d+ more characters\. Read the rest before acting: '
                                 rf'room_read\(section="messages", after_id={m["id"] - 1}, offset=\d+\)')
        self.assertNotIn(f"message:{m['id']}", run.receipts)
        # The named offset really continues the message.
        offset = int(prompt.split('offset=')[-1].split(')')[0])
        page = self.room.read(run, section='messages', after_id=m['id'] - 1, offset=offset, limit=6000)
        shown = prompt.split('Current message or task:\n')[1].split('\n[Message #')[0]
        self.assertTrue((shown + page['text']).startswith(f"#{m['id']} user:\n@opus xxx"))
        self.assertEqual(len(shown), offset)

    def test_older_mentions_are_not_cut_and_dropped_ones_are_named(self):
        early = self.store.add('astra', '@opus please review ' + 'detail ' * 300 + 'END-A', channel='general')
        for i in range(14):
            self.store.add('user', f'chatter {i}', channel='general')
        other = self.store.add('astra', '@opus second ask ' + 'more ' * 100 + 'END-B', channel='general')
        now = self.store.add('user', '@opus go', channel='general')
        prompt = self.room.context(self.run_for(now))
        self.assertIn('END-B', prompt)
        self.assertIn(f"Also addressed to you and not shown here: #{early['id']} astra", prompt)
        self.assertNotIn(f"#{other['id']} astra", prompt.split('Also addressed')[1].split(']')[0])

    def test_overflowing_coalesced_requests_are_named(self):
        old = self.store.add('astra', '@opus ' + 'a' * 5000, channel='general')
        new = self.store.add('user', '@opus ' + 'b' * 5000, channel='general')
        run = self.run_for(old, new)
        prompt = self.room.context(run)
        self.assertIn(f"1 earlier request(s) in this turn are not shown here: #{old['id']}", prompt)
        self.assertIn('b' * 4900, prompt)


class ExecutionContextTests(TurnContextTests):
    def test_conversation_project_names_scratch_folder_and_pending_attach(self):
        (self.r.data / 'projects' / 'p').mkdir(parents=True, exist_ok=True)
        (self.r.data / 'projects' / 'p' / 'project.json').write_text(
            '{"name": "DuneQoL", "repo_request": {"by": "astra", "path": "D:/dune", "create": false, "time": 1}}', 'utf-8')
        run = self.run_for(self.store.add('user', '@opus hi', channel='general'))
        prompt = self.room.context(run)
        block = prompt.split('Execution context:\n')[1].split('Current conversation')[0]
        self.assertIn('- Project: DuneQoL (id p). The board, chats and records below belong to it', block)
        self.assertIn('- Repository: none attached (conversation project).', block)
        self.assertRegex(block, r"- Repository request: astra asked to attach D:/dune \S+ ago; waiting for the user's approval in Projects\.")
        self.assertIn('W:/scratch/opus, a private scratch folder. It starts empty; that does not mean the project has no source.', block)
        self.assertIn('If any is missing from your tool list, say so in the chat as a harness fault', block)
        # Next turn in the same native session: the unchanged block is not resent.
        run2 = self.run_for(self.store.add('user', '@opus again', channel='general'))
        run2.previous_session = {'context': run.context_delivery}
        self.assertIn('Execution context: [Unchanged; already in your native session.', self.room.context(run2))

    def turn(self, text, compacted=False):
        """One turn in the same native session, saved by complete() as a real turn end is."""
        run = self.run_for(self.store.add('user', text, channel='general'))
        run.session_key, run.settings, run.instruction_hash, run.provider_session = 'opus:general', {}, 'h', 'sess'
        run.previous_session = self.room.previous_session(run)
        prompt = self.room.context(run)
        run.compacted = compacted
        self.room.complete(run)
        return prompt

    def test_after_a_compaction_the_next_turn_sends_every_block_in_full(self):
        self.turn('@opus first look')
        self.assertIn('Execution context: [Unchanged;', self.turn('@opus second', compacted=True))
        after = self.turn('@opus third')  # task 437f53ee: the session's summary may have dropped the blocks
        self.assertNotIn('[Unchanged;', after)
        self.assertNotIn('@opus second', after)  # the message cursor is kept: no chat replay
        self.assertIn('[Your session was compacted since your last turn', after)  # and it says where older chat is
        fourth = self.turn('@opus fourth')
        self.assertIn('Execution context: [Unchanged;', fourth)  # delivery resumes
        self.assertNotIn('session was compacted', fourth)

    def test_a_compacted_turn_that_did_not_complete_also_resets_delivery(self):
        self.turn('@opus first look')
        run = self.run_for(self.store.add('user', '@opus long job', channel='general'))
        run.session_key, run.settings, run.instruction_hash, run.provider_session = 'opus:general', {}, 'h', 'sess'
        run.previous_session = self.room.previous_session(run)
        self.room.context(run)
        run.compacted = True  # compacted mid-turn, then stopped or timed out: complete() never runs (harness 0a48a156)
        self.room.forget_context(run)
        self.assertNotIn('[Unchanged;', self.turn('@opus after the stop'))

    def test_repository_project_names_worktree_and_branch(self):
        self.r.project = 'D:/dune'
        self.r.states['opus'].update(workspace_kind='repository', branch='chatroom/p/opus-1')
        prompt = self.room.context(self.run_for(self.store.add('user', '@opus hi', channel='general')))
        self.assertIn('- Repository: attached at D:/dune.', prompt)
        self.assertIn('your own git worktree of the repository on branch chatroom/p/opus-1', prompt)


class ActivityTaskTests(unittest.TestCase):
    def test_task_ids_come_from_requests_not_from_running_alone(self):
        from zerocollab.coordination.coordination_queue import WakeQueue, current_request
        from zerocollab.runtime.room_runtime import RoomRuntime
        queue = WakeQueue(maxsize=4)
        queue.put_nowait({'channel': 'g', 'chain': 'c', 'requests': [{'prompt': 'start', 'task_id': 't2'}]})
        run = SimpleNamespace(channel='g', task_ids={'t1'})
        r = SimpleNamespace(running={'astra': run}, queues={'astra': queue},
                            deploy_held={'astra': {'requests': [{'ref': {'collection': 'tasks', 'id': 't3'}}]}})
        self.assertEqual(RoomRuntime.activity(r, 'astra'), {'running': True, 'channel': 'g', 'queued': 2,
                         'running_task_ids': ['t1'], 'queued_task_ids': ['t2', 't3']})
        # A pending transfer is current for its recipient though the owner has not changed yet.
        state = {'tasks': [{'id': 't1', 'owner': 'opus', 'status': 'working', 'updated': 5,
                            'transfer': {'from': 'opus', 'to': 'astra'}}]}
        ref = {'ref': {'collection': 'tasks', 'id': 't1', 'updated': 5, 'action': 'delegate'}}
        self.assertTrue(current_request(ref, 'astra', state, set()))
        self.assertFalse(current_request(ref, 'native', state, set()))


class LedgerTests(TurnContextTests):
    def test_ledger_shows_execution_state_and_only_current_decisions(self):
        self.r.states['astra'].update(state='ready')
        self.r.activity = lambda name: {'running': False, 'channel': None, 'queued': 0, 'running_task_ids': [], 'queued_task_ids': []}
        work = self.room.task('opus', 'create', title='Fix routing', details='d')
        self.room.task('opus', 'claim', task_id=work['id'])
        self.room.task('opus', 'update', task_id=work['id'], next_action='Run the dry run', priority=1)
        self.room.task('opus', 'delegate', task_id=work['id'], owner='astra')
        old = self.room.note('opus', 'decision', 'No source edits yet')
        self.room.note('opus', 'decision', 'Source edits allowed after review', supersedes=old['id'])
        prompt = self.room.context(self.run_for(self.store.add('user', '@opus go', channel='general')))
        self.assertIn(f"{work['id']} [working; idle] priority=1 owner=opus", prompt)
        self.assertIn('transfer proposed to astra; opus owns it until astra accepts', prompt)
        self.assertIn('next action: Run the dry run', prompt)
        self.assertNotIn('No source edits yet', prompt)
        self.assertIn('Source edits allowed after review', prompt)

    def test_active_references_stay_in_context_past_progress_notes(self):
        cache = self.room.note('astra', 'reference', 'RE cache: research/cache.json')
        moved = self.room.note('astra', 'reference', 'Helper: tools/dump.py')
        self.room.note('astra', 'reference', 'Helper moved: tools/v2/dump.py', supersedes=moved['id'])
        for i in range(10):
            self.room.note('astra', 'progress', f'step {i}')
        prompt = self.room.context(self.run_for(self.store.add('user', '@opus go', channel='general')))
        self.assertIn(f"reference {cache['id']} astra: RE cache: research/cache.json", prompt)
        self.assertIn('Helper moved: tools/v2/dump.py', prompt)
        self.assertNotIn('Helper: tools/dump.py', prompt)


class RoomToolsTests(unittest.TestCase):
    def test_unobserved_room_tools_are_unconfirmed_not_failed(self):
        from zerocollab.runtime.room_runtime import RoomRuntime
        posted = []
        r = SimpleNamespace(states={'opus': {'provider': 'claude'}, 'gem': {'provider': 'gemini'}},
                            host=SimpleNamespace(store=SimpleNamespace(add=lambda *a, **k: posted.append(a[1]))))
        turn = lambda name, listed=False, calls=0: RoomRuntime.check_room_tools(
            r, SimpleNamespace(name=name, channel='g', tools_listed=listed, coordination_calls=calls))
        # The listing report is best effort: a turn whose report never arrived and that made no calls
        # is unconfirmed, never stated as a failure.
        turn('opus')
        turn('opus')
        self.assertEqual(len(posted), 1)
        self.assertIn("No room-tool listing or room-tool calls were observed from opus", posted[0])
        self.assertIn("can't confirm its tools loaded", posted[0])
        self.assertNotIn('did not load', posted[0])
        self.assertEqual(r.states['opus']['room_tools'], 'unconfirmed')
        turn('opus', calls=2)
        self.assertEqual(posted[-1], "opus's room tools were observed again.")
        turn('opus', listed=True)
        self.assertEqual(len(posted), 2)
        turn('gem')  # a provider without room tools is not a fault
        self.assertEqual(len(posted), 2)

    def test_execution_context_says_when_tools_are_not_available(self):
        room = ProjectRoom(SimpleNamespace(data=Path(tempfile.mkdtemp()), project=None, identity='p',
                                           states={'gem': {'provider': 'gemini'}, 'opus': {'provider': 'claude', 'room_tools': 'unconfirmed'}}))
        block = room.execution_context(SimpleNamespace(name='gem', worktree='W:/g'))
        self.assertIn('Room tools are not available to your provider here.', block)
        block = room.execution_context(SimpleNamespace(name='opus', worktree='W:/o'))
        self.assertIn('The room did not observe your room tools loading or being used last turn.', block)


class DependencyReadyTests(unittest.TestCase):
    def test_owner_is_woken_only_when_every_dependency_is_done(self):
        from zerocollab.runtime.room_runtime import RoomRuntime
        calls = []
        tasks = [{'id': 'a', 'status': 'done', 'owner': 'opus', 'depends_on': [], 'updated': 1, 'title': 'A'},
                 {'id': 'b', 'status': 'working', 'owner': 'opus', 'depends_on': [], 'updated': 1, 'title': 'B'},
                 {'id': 'c', 'status': 'open', 'owner': 'astra', 'depends_on': ['a'], 'updated': 2, 'title': 'C', 'next_action': 'install'},
                 {'id': 'd', 'status': 'open', 'owner': 'astra', 'depends_on': ['a', 'b'], 'updated': 3, 'title': 'D'},
                 {'id': 'e', 'status': 'open', 'owner': 'opus', 'depends_on': ['a'], 'updated': 4, 'title': 'E'}]
        r = SimpleNamespace(project_room=SimpleNamespace(state={'tasks': tasks}), states={'astra': {}, 'opus': {}},
                            notify_participant=lambda *args: calls.append(args) or {'status': 'queued'})
        tasks += [{'id': 'f', 'status': 'working', 'owner': 'astra', 'depends_on': ['a'], 'updated': 5, 'title': 'F',
                   'wait': {'kind': 'followup', 'record_id': 'q1'}},
                  {'id': 'g', 'status': 'blocked', 'owner': 'astra', 'depends_on': ['a'], 'updated': 6, 'title': 'G'}]
        r.project_room.state['followups'] = [{'id': 'q1', 'task_id': 'f', 'assigned_to': 'user', 'status': 'open'}]
        result = RoomRuntime.dependency_ready(r, 'a', 'general', 'c', actor='opus')
        # f still waits on the user, so it is not told to continue.
        self.assertEqual(list(result), ['c', 'g'])
        self.assertIn('Next action on record: install.', calls[0][3])
        self.assertIn('Continue it now', calls[0][3])
        self.assertIn('It is marked blocked: check whether these dependencies were the block', calls[1][3])
        self.assertEqual(calls[0][4], {'collection': 'tasks', 'id': 'c', 'updated': 2, 'action': 'ready', 'by': 'opus'})
        # The wake is re-checked at delivery: if an upstream reopened meanwhile, it is no longer current.
        from zerocollab.coordination.coordination_queue import current_request
        ref = {'ref': calls[0][4]}
        self.assertTrue(current_request(ref, 'astra', r.project_room.state, set()))
        tasks[0]['status'] = 'working'
        self.assertFalse(current_request(ref, 'astra', r.project_room.state, set()))


class DeliveryResultTests(unittest.TestCase):
    def setUp(self):
        from zerocollab.coordination.coordination_queue import WakeQueue
        from zerocollab.runtime.room_runtime import RoomRuntime
        self.queue = WakeQueue(maxsize=4)
        self.r = SimpleNamespace(paused=False, running={}, deploy_held={}, queues={'astra': self.queue},
                                 states={'astra': {'state': 'ready'}}, parallel_capacity=lambda: 2)
        self.r.activity = lambda name: RoomRuntime.activity(self.r, name)
        def enqueue(name, channel, **kwargs):
            self.queue.put_nowait({'channel': channel, 'chain': 'c', 'requests': [{'ref': kwargs['ref']}]})
            return True
        def enqueue_other(name, channel, **kwargs):
            self.queue.put_nowait({'channel': 'other', 'chain': 'c', 'requests': [{'ref': kwargs['ref']}]})
            return True
        self.r.enqueue = enqueue
        self.enqueue_other = enqueue_other
        self.notify = lambda: RoomRuntime.notify_participant(self.r, 'astra', 'general', 'c', 'p',
                                                             {'collection': 'reviews', 'id': 'r1', 'updated': 1})

    def test_states_say_whether_and_when_a_turn_starts(self):
        self.assertEqual(self.notify()['reason'], 'A turn for astra starts now.')
        self.assertEqual(self.r.activity('astra'), {'running': False, 'channel': None, 'queued': 1,
                                                     'running_task_ids': [], 'queued_task_ids': []})
        self.assertEqual(self.notify()['reason'], 'Added to the turn already waiting to start for astra in this chat.')
        self.queue._queue.clear()
        self.enqueue_other('astra', 'other', ref={'collection': 'reviews', 'id': 'r0', 'updated': 1})
        self.assertIn('after 1 turn(s) already waiting', self.notify()['reason'])
        self.queue._queue.clear()
        self.r.running = {'astra': SimpleNamespace(channel='other', task_ids=set(), steering=SimpleNamespace(sender=None, closed=False))}
        result = self.notify()
        self.assertEqual((result['status'], result['reason']), ('queued', 'A turn for astra starts after its current turn in another chat.'))
        self.r.running = {'astra': SimpleNamespace(channel='general', task_ids=set(), steering=SimpleNamespace(sender=object(), closed=False))}
        self.assertIn('sent into its running turn at its next step', self.notify()['reason'])
        self.r.running, self.r.deploy_held = {'x': 1, 'y': 2}, {}
        self.queue._queue.clear()
        self.assertIn('when one of the 2 parallel turn slots frees', self.notify()['reason'])

    def test_not_woken_is_deferred_or_dropped_never_queued(self):
        self.r.states['astra']['paused'] = True
        self.assertEqual(self.notify()['status'], 'deferred')
        self.r.states['astra']['paused'] = False
        self.r.enqueue = lambda *a, **k: False
        result = self.notify()
        self.assertEqual((result['status'], result['queued']), ('dropped', False))
        self.assertEqual(RoomRuntime_notify(self.r, 'ghost')['status'], 'dropped')


def RoomRuntime_notify(r, name):
    from zerocollab.runtime.room_runtime import RoomRuntime
    return RoomRuntime.notify_participant(r, name, 'general', 'c', 'p', {'collection': 'reviews', 'id': 'r1', 'updated': 1})


if __name__ == '__main__':
    unittest.main()
