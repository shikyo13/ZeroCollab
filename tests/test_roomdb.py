"""room.db: import, saves that write only what changed, touched saves and their verifier, generations and the
rollback export (task d6139872, plan roomdb-plan/plan.md)."""
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import roomdb


def sample():
    return {'spec': 'build it', 'tasks': [{'id': f't{i}', 'title': f'task {i}', 'extra': {'unknown': i}} for i in range(5)],
            'notes': [{'kind': 'progress', 'text': 'no id'}],  # an older project's notes: one value row
            'followups': [{'id': 'f1', 'status': 'open'}], 'continuity': {'claims': {'a': 1}}, 'archived_tasks': 3}


class RoomDb(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)  # fails on Windows if any handle were left open
        self.folder = Path(tmp.name)

    def imported(self, state=None):
        state = sample() if state is None else state
        (self.folder / roomdb.JSON_NAME).write_text(json.dumps(state), 'utf-8')
        return roomdb.Store.open(self.folder)

    def rows(self, sql, *params):
        with closing(sqlite3.connect(self.folder / roomdb.DB_NAME)) as conn:
            return conn.execute(sql, params).fetchall()

    def test_import_reads_back_equal_and_retires_the_json(self):
        store, state = self.imported()
        self.assertEqual(state, sample())
        self.assertFalse((self.folder / roomdb.JSON_NAME).exists())
        self.assertTrue((self.folder / roomdb.PRE_ROOMDB_NAME).exists())
        self.assertEqual(roomdb.read_state(self.folder), sample())
        self.assertEqual(self.rows("SELECT kind FROM section WHERE name = 'notes'"), [('value',)])
        self.assertEqual(len(self.rows("SELECT id FROM rec WHERE section = 'tasks'")), 5)

    def test_a_new_project_is_room_db_from_the_start(self):
        store, state = roomdb.Store.open(self.folder)
        self.assertEqual(state, roomdb.EMPTY_STATE)
        self.assertTrue(roomdb.has_state(self.folder))
        self.assertFalse((self.folder / roomdb.PRE_ROOMDB_NAME).exists())

    def test_a_save_writes_only_what_changed(self):
        store, state = self.imported()
        self.assertEqual(store.save(state), 0)
        state['tasks'][2]['title'] = 'renamed'
        self.assertEqual(store.save(state), 1)
        state['tasks'].pop(0)  # every later task moves up one place
        state['tasks'].append({'id': 't9'})
        store.save(state)
        del state['continuity']
        state['notes'].append({'id': 'n1'})  # still one value row: the older notes have no id
        store.save(state)
        self.assertEqual(roomdb.Store.open(self.folder)[1], state)

    def test_touched_saves_and_the_verifier(self):
        store, state = self.imported()
        state['tasks'][1]['title'] = 'changed'
        state['followups'].append({'id': 'f2', 'status': 'open'})  # appended: found without being named
        state['continuity']['claims']['b'] = 2  # a value section: compared without being named
        self.assertEqual(store.save(state, touched=[('tasks', 't1')]), 3)
        self.assertEqual(roomdb.Store.open(self.folder)[1], state)
        state['tasks'][3]['title'] = 'missed'
        with patch.object(roomdb, 'VERIFY', True):
            with self.assertRaisesRegex(roomdb.RoomDbError, 'missed.*tasks/t3'):
                store.save(state, touched=[('tasks', 't1')])

    def test_a_missed_mark_is_written_by_the_next_full_comparison(self):
        store, state = self.imported()
        state['tasks'][3]['title'] = 'missed'
        with patch.object(roomdb, 'VERIFY', False):  # production behaviour: no verifier
            store.save(state, touched=[])
        self.assertNotEqual(roomdb.read_state(self.folder), state)
        with patch.object(roomdb, 'FULL_DIFF_SECONDS', 0):
            store.save(state, touched=[])
        self.assertEqual(roomdb.read_state(self.folder), state)

    def test_the_maintenance_block_writes_a_missed_mark_or_refuses(self):
        from types import SimpleNamespace
        from release_maintenance import ReleaseMaintenance
        project = self.folder / 'projects' / 'p'
        project.mkdir(parents=True)
        (project / roomdb.JSON_NAME).write_text(json.dumps(sample()), 'utf-8')
        store, state = roomdb.Store.open(project)
        state['tasks'][3]['title'] = 'missed'
        with patch.object(roomdb, 'VERIFY', False):
            store.save(state, touched=[])
        room = SimpleNamespace(store=store, save=lambda: store.save(state))
        maintenance = object.__new__(ReleaseMaintenance)
        maintenance.runtime = SimpleNamespace(project_room=room, data=self.folder)
        maintenance.save_board()  # before writes close, so the install snapshot holds it
        self.assertEqual(roomdb.read_state(project), state)
        # ...with the deployment export a rollback reads, which room.db knows is an untouched copy
        self.assertEqual(json.loads((project / roomdb.JSON_NAME).read_text('utf-8')), state)
        self.assertEqual(roomdb.current_form(project), 'db')
        room.save = lambda: (_ for _ in ()).throw(sqlite3.OperationalError('disk I/O error'))
        with self.assertRaisesRegex(OSError, 'block was refused'):
            maintenance.save_board()

    def test_a_board_written_by_an_older_release_after_a_rollback_is_imported(self):
        store, state = self.imported()
        generation = store.generation()[0]
        roomdb.export_for_deploy(self.folder)
        older = json.loads((self.folder / roomdb.JSON_NAME).read_text('utf-8'))  # what a release before room.db reads
        self.assertEqual(older, state)
        older['tasks'].append({'id': 'rollback', 'title': 'written on the older release'})
        (self.folder / roomdb.JSON_NAME).write_text(json.dumps(older, indent=2), 'utf-8')
        self.assertEqual(roomdb.read_state(self.folder), older)  # an inactive project shows it too
        again, reopened = roomdb.Store.open(self.folder)
        self.assertEqual(reopened, older)
        renewed = again.generation()[0]
        self.assertNotEqual(renewed, generation)
        self.assertTrue((self.folder / f'{roomdb.DB_NAME}{roomdb.SUPERSEDED_SUFFIX}-{generation}').is_file())
        self.assertTrue((self.folder / roomdb.PRE_ROOMDB_NAME).is_file())  # the first evidence is never overwritten
        self.assertTrue((self.folder / f'coordination.pre-roomdb-{renewed}.json').is_file())

    def test_an_untouched_export_leaves_room_db_current(self):
        store, state = self.imported()
        roomdb.export_for_deploy(self.folder)
        state['tasks'][0]['title'] = 'written after an aborted deployment'
        store.save(state)
        committed = store.generation()
        again, reopened = roomdb.Store.open(self.folder)
        self.assertEqual(reopened, state)
        self.assertEqual(again.generation(), committed)
        self.assertFalse((self.folder / roomdb.JSON_NAME).exists())
        self.assertTrue((self.folder / roomdb.EXPORT_LEFT_NAME).is_file())

    def test_a_folder_that_lost_its_live_files_is_refused_not_emptied(self):
        self.imported()
        (self.folder / roomdb.DB_NAME).unlink()
        with self.assertRaisesRegex(roomdb.RoomDbError, 'Nothing was created'):
            roomdb.Store.open(self.folder)
        self.assertFalse((self.folder / roomdb.DB_NAME).exists())

    def test_saves_run_with_synchronous_extra(self):
        store, _ = self.imported()
        with closing(roomdb.connect(store.path)) as conn:
            self.assertEqual(conn.execute('PRAGMA synchronous').fetchone()[0], roomdb.SYNCHRONOUS_EXTRA)

    def test_after_a_failed_commit_the_next_save_compares_everything(self):
        store, state = self.imported()  # systems ab210df2 note: a touched save after a failure missed this task
        state['tasks'][1]['title'] = 'pending'
        real, failed = store.commit, []

        def commit_once_failing(ops):
            if not failed:
                failed.append(True)
                raise sqlite3.OperationalError('disk I/O error')
            return real(ops)
        with patch.object(store, 'commit', commit_once_failing), patch.object(roomdb, 'VERIFY', False):
            with self.assertRaises(sqlite3.OperationalError):
                store.save(state)
            state['spec'] = 'changed after the failure'
            store.save(state, touched=())
        self.assertEqual(roomdb.read_state(self.folder), state)

    def test_generation_and_commit_seq(self):
        store, state = self.imported()
        generation, seq = store.generation()
        self.assertEqual(seq, 0)
        state['spec'] = 'one'
        store.save(state)
        backup = self.folder / 'backup'
        backup.mkdir()
        shutil.copy2(store.path, backup / roomdb.DB_NAME)
        state['spec'] = 'two'
        store.save(state)
        self.assertEqual(store.generation(), (generation, 2))
        reopened, _ = roomdb.Store.open(self.folder)
        self.assertEqual(reopened.generation(), (generation, 2))  # an ordinary restart keeps both
        restored, _ = roomdb.Store.open(backup)  # a file copy: same generation, lower commit_seq
        self.assertEqual(restored.generation(), (generation, 1))

    def test_a_failed_save_commits_nothing_and_the_next_save_retries(self):
        store, state = self.imported()
        state['spec'] = 'lost?'
        with patch.object(roomdb, 'UPSERT_REC', 'INSERT INTO nowhere VALUES(?, ?, ?, ?)'):
            with self.assertRaises(sqlite3.OperationalError):
                store.save(state)
        self.assertEqual(roomdb.read_state(self.folder)['spec'], 'build it')
        self.assertEqual(store.generation()[1], 0)
        store.save(state)
        self.assertEqual(roomdb.read_state(self.folder)['spec'], 'lost?')

    def test_an_interrupted_import_is_redone(self):
        (self.folder / (roomdb.DB_NAME + roomdb.IMPORTING_SUFFIX)).write_bytes(b'half written')
        store, state = self.imported()
        self.assertEqual(state, sample())
        self.assertFalse((self.folder / (roomdb.DB_NAME + roomdb.IMPORTING_SUFFIX)).exists())

    def test_rollback_export_round_trip(self):
        store, state = self.imported()
        state['tasks'][0]['title'] = 'after cutover'
        store.save(state)
        generation = store.generation()[0]
        target = roomdb.export_json(self.folder)
        self.assertEqual(json.loads(target.read_text('utf-8')), state)
        self.assertFalse((self.folder / roomdb.DB_NAME).exists())
        self.assertTrue((self.folder / f'{roomdb.DB_NAME}{roomdb.RETIRED_SUFFIX}-{generation}').exists())
        again, reimported = roomdb.Store.open(self.folder)  # the next upgrade imports it again
        self.assertEqual(reimported, state)
        self.assertNotEqual(again.generation()[0], generation)

    def test_export_refuses_an_unknown_record_kind(self):
        self.imported()
        with closing(sqlite3.connect(self.folder / roomdb.DB_NAME)) as conn:
            conn.execute('PRAGMA ignore_check_constraints = ON')
            conn.execute("INSERT INTO section(name, kind, ord) VALUES('future', 'blob', 99)")
            conn.commit()
        with self.assertRaisesRegex(roomdb.RoomDbError, 'does not know'):
            roomdb.export_json(self.folder)
        self.assertTrue((self.folder / roomdb.DB_NAME).exists())
        self.assertFalse((self.folder / roomdb.JSON_NAME).exists())

    def test_a_newer_schema_is_refused(self):
        self.imported()
        with closing(sqlite3.connect(self.folder / roomdb.DB_NAME)) as conn:
            conn.execute(f'PRAGMA user_version = {roomdb.SCHEMA_VERSION + 1}')
        with self.assertRaisesRegex(roomdb.RoomDbError, 'newer ZeroCollab'):
            roomdb.read_state(self.folder)


if __name__ == '__main__':
    unittest.main()
