"""A page that connects mid-turn gets each run's latest speech (task 3d4f7209; contract with frontend e96b10f2):
the message being written, else the last finished one, as a raw bounded tail, with sealed positions redacted."""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from room_runtime import Run, SPEECH_TAIL_CHARS  # noqa: E402


class FakeRun(SimpleNamespace):
    """Run's own speech(), delta-free finish_message() and properties, on stubbed collaborators."""
    speech = Run.speech
    delta = Run.delta
    finish_message = Run.finish_message
    begin_message = Run.begin_message
    sealed = property(lambda self: self.sealing)
    task_id = property(lambda self: None if self.sealing else self.focus)

    def answers_human(self):
        return False

    def touch(self):
        pass


def fake_run():
    async def note(*args, **kwargs):
        return None
    events = []
    async def emit(kind, data):
        events.append((kind, data))
    manager = SimpleNamespace(activity_note=note, emit=emit, events=events)
    return FakeRun(id='r1', name='backend', channel='c', focus='t1', sealing=False, manager=manager, text='',
                   message_id=None, message_phase=None, completed_messages=set(), last_commentary='',
                   last_speech=None)


def write(run, message, text):
    """What delta() leaves behind for a message in progress."""
    run.message_id, run.message_phase, run.text = message, 'commentary', run.text + text


class RunSpeechTest(unittest.TestCase):
    def test_nothing_said_yet_is_empty_with_the_focus(self):
        speech = fake_run().speech()
        self.assertEqual((speech['text'], speech['message'], speech['draft'], speech['task_id']), ('', None, False, 't1'))

    def test_the_message_being_written_then_the_last_finished_one(self):
        run = fake_run()
        write(run, 'm1', 'Checking  the\nboard')
        self.assertEqual(run.speech()['text'], 'Checking  the\nboard')  # raw, whitespace as sent
        self.assertEqual((run.speech()['message'], run.speech()['draft']), ('m1', True))
        asyncio.run(run.finish_message())
        speech = run.speech()  # between messages the finished one is still what the views show
        self.assertEqual((speech['message'], speech['draft'], speech['text']), ('m1', False, 'Checking  the\nboard'))
        run.message_id = 'm2'  # begun but no text yet: still the last finished message
        self.assertEqual(run.speech()['message'], 'm1')
        write(run, 'm2', 'Next')
        self.assertEqual((run.speech()['message'], run.speech()['text'], run.speech()['draft']), ('m2', 'Next', True))

    def test_the_tail_is_bounded(self):
        run = fake_run()
        write(run, 'm1', 'x' * SPEECH_TAIL_CHARS + 'END')
        self.assertEqual(len(run.speech()['text']), SPEECH_TAIL_CHARS)
        self.assertTrue(run.speech()['text'].endswith('END'))

    def test_end_tells_which_chunks_a_reply_already_covers(self):
        # frontend 3fa5cffa: a reply 'ha' and a later chunk 'ha' are told apart by end, not by text overlap.
        run = fake_run()
        asyncio.run(run.delta('ha'))
        reply = run.speech()
        asyncio.run(run.delta('ha'))
        asyncio.run(run.delta('!'))
        drafts = [data for kind, data in run.manager.events if kind == 'runtime_draft']
        self.assertEqual([d['end'] for d in drafts], [2, 4, 5])
        self.assertEqual((reply['text'], reply['end']), ('ha', 2))
        rebuilt = reply['text'] + ''.join(d['text'] for d in drafts
                                          if d['message'] == reply['message'] and d['end'] > reply['end'])
        self.assertEqual(rebuilt, run.text)  # 'haha!': nothing duplicated, nothing lost
        asyncio.run(run.finish_message())
        self.assertEqual((run.speech()['end'], run.speech()['draft']), (5, False))  # the finished message keeps its end

    def test_a_sealed_position_returns_no_text_now_or_after_it_finished(self):
        run = fake_run()
        run.sealing = True
        write(run, 'm1', 'my sealed position')
        self.assertEqual((run.speech()['text'], run.speech()['sealed'], run.speech()['task_id']), ('', True, None))
        asyncio.run(run.finish_message())
        run.sealing = False  # sealing ends; the message written while sealed stays redacted
        self.assertEqual((run.speech()['text'], run.speech()['sealed'], run.speech()['message']), ('', True, 'm1'))


if __name__ == '__main__':
    unittest.main()
