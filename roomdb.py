"""room.db: a project's coordination records in SQLite (task d6139872; addendum (e) r4 e1 to e3; plan r2 fe1a97be).

coordination.json was rewritten whole on every save: 57 ms for the live room's 11 MB, on the event loop, from 83 call
sites. room.db keeps the same state, one row per record, and a save writes only the rows that changed, in one
transaction (synchronous=FULL). ProjectRoom still holds the whole state in memory, so every reader in the room is
unchanged; only how it is persisted changes.

Each save opens room.db, commits and closes it again, with a rollback journal rather than WAL (plan r3). Measured on
the live room's 11 MB: 4.2 ms per commit this way, 0.8 ms on a connection kept open, 57 ms for today's whole-file
rewrite. In exchange no handle stays open between saves, and room.db alone holds every committed transaction, so the
install snapshot needs no WAL checkpoint and no -wal or -shm files.

Layout. A section whose value is a list of objects with unique string ids (tasks, notes, followups, reviews,
chain_records) is stored one row per record, ordered by `ord`. Any other section (spec, cursors, receipts, a list of
notes without ids in an older project) is one row with id ''. Every row's `doc` is the exact JSON of the value, so
unknown keys survive (e2). `section` keeps each section's kind and its key order.

Generations (contract v0.2.1 hello, addendum r4 row 12). `room_generation` is a random id written when room.db is
created or imported. `commit_seq` rises by one in every save transaction, so a restored copy shows the same
generation with a lower commit_seq.

Cutover (e3). The first time a project is opened, its coordination.json is imported into a new file, read back and
compared with the original, and only then renamed into place as room.db. coordination.json is then renamed
coordination.pre-roomdb.json, as evidence only. A project never opened since the upgrade keeps its coordination.json,
and read_state() reads either form. Rollback: `python roomdb.py export <project folder>` writes the current room.db
back to coordination.json in today's format, then retires room.db, so the previous release reads it and a later
upgrade imports it again. Run it only while the room is stopped.
"""
import hashlib
import json
import logging
import os
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path

DB_NAME = 'room.db'
JSON_NAME = 'coordination.json'
PRE_ROOMDB_NAME = 'coordination.pre-roomdb.json'
IMPORTING_SUFFIX = '.importing'
RETIRED_SUFFIX = '.exported'
SCHEMA_VERSION = 1           # PRAGMA user_version; a reader refuses a file newer than it knows
BUSY_TIMEOUT_MS = 5000       # a reader in this process waits this long for a writer's lock
DIGEST_BYTES = 16
EMPTY_STATE = {'spec': '', 'tasks': [], 'notes': []}
LIST, VALUE = 'list', 'value'

LOGGER = logging.getLogger(__name__)

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS section(name TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('list', 'value')),"
    " ord INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS rec(section TEXT NOT NULL, id TEXT NOT NULL, ord INTEGER NOT NULL, doc TEXT NOT NULL,"
    " PRIMARY KEY(section, id))",
)


class RoomDbError(RuntimeError):
    """room.db can't be opened, imported or exported. The message names the file and what to do."""


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def digest(doc):
    return hashlib.blake2b(doc.encode('utf-8'), digest_size=DIGEST_BYTES).digest()


def is_record_list(value):
    """A list stored one row per record: every element an object with a unique, non-empty string id."""
    if not isinstance(value, list):
        return False
    ids = set()
    for record in value:
        if not isinstance(record, dict):
            return False
        rid = record.get('id')
        if not isinstance(rid, str) or not rid or rid in ids:
            return False
        ids.add(rid)
    return True


def connect(path, *, readonly=False):
    """A connection for one save or one read. Callers close it (closing())."""
    if readonly:
        conn = sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro', uri=True, timeout=BUSY_TIMEOUT_MS / 1000,
                               isolation_level=None)
    else:
        conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        conn.execute('PRAGMA journal_mode=DELETE')
        conn.execute('PRAGMA synchronous=FULL')
    conn.execute(f'PRAGMA busy_timeout={BUSY_TIMEOUT_MS}')
    version = conn.execute('PRAGMA user_version').fetchone()[0]
    if version > SCHEMA_VERSION:
        conn.close()
        raise RoomDbError(f'{path} was written by a newer ZeroCollab (room.db schema {version}; this release reads up '
                          f'to {SCHEMA_VERSION}). Install that release again, or export it with its own roomdb.py.')
    return conn


def read_rows(conn):
    """(state, cache) from an open room.db. cache maps each section to what a later save compares against."""
    state, cache = {}, {}
    rows = {}
    for section, rid, ord_, doc in conn.execute('SELECT section, id, ord, doc FROM rec ORDER BY section, ord'):
        rows.setdefault(section, []).append((rid, ord_, doc))
    for ord_, (name, kind) in enumerate(conn.execute('SELECT name, kind FROM section ORDER BY ord')):
        found = rows.get(name, [])
        if kind == VALUE:
            if len(found) != 1 or found[0][0] != '':
                raise RoomDbError(f'room.db section {name!r} is damaged: expected one value row, found {len(found)}')
            doc = found[0][2]
            state[name] = json.loads(doc)
            cache[name] = (VALUE, ord_, digest(doc))
        else:
            state[name] = [json.loads(doc) for _, _, doc in found]
            cache[name] = (LIST, ord_, {rid: (index, digest(doc)) for index, (rid, _, doc) in enumerate(found)})
    return state, cache


def meta(conn):
    return dict(conn.execute('SELECT key, value FROM meta'))


def read_state(folder):
    """A project's coordination state, read-only, whichever form it is in: room.db when present, otherwise
    coordination.json, otherwise {}. Never imports or migrates; only ProjectRoom loading a project does."""
    folder = Path(folder)
    db = folder / DB_NAME
    if db.is_file():
        with closing(connect(db, readonly=True)) as conn:
            return read_rows(conn)[0]
    try:
        return json.loads((folder / JSON_NAME).read_text('utf-8'))
    except (OSError, ValueError):
        return {}


def has_state(folder):
    """True when the folder holds a project's coordination records in either form."""
    folder = Path(folder)
    return (folder / DB_NAME).is_file() or (folder / JSON_NAME).is_file()


class Store:
    """One project's room.db and what was last committed to it. save() is called from the event loop and
    occasionally from worker threads, so saves are serialised by a lock."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.path = self.folder / DB_NAME
        self.cache = {}
        self.lock = threading.Lock()

    @classmethod
    def open(cls, folder):
        """(store, state) for a project folder: open room.db, or import coordination.json first, or create an
        empty room.db for a new project."""
        store = cls(folder)
        store.folder.mkdir(parents=True, exist_ok=True)
        source = store.folder / JSON_NAME
        if not store.path.exists():
            initial = json.loads(source.read_text('utf-8')) if source.exists() else dict(EMPTY_STATE)
            store.create(initial, imported_from=source if source.exists() else None)
        with closing(connect(store.path)) as conn:
            state, store.cache = read_rows(conn)
        store.retire_json()
        return store, state

    def create(self, state, imported_from=None):
        """Write state to a new file beside room.db, read it back and compare, then move it into place. A crash
        before the move leaves only the .importing file, which the next open replaces."""
        temp = self.path.with_name(DB_NAME + IMPORTING_SUFFIX)
        for leftover in (temp, Path(f'{temp}-journal')):
            leftover.unlink(missing_ok=True)
        conn = connect(temp)
        try:
            for statement in SCHEMA:
                conn.execute(statement)
            conn.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            cutover = {'time': time.time(), 'schema': SCHEMA_VERSION}
            if imported_from is not None:
                cutover['source'] = str(imported_from.name)
                cutover['source_sha256'] = hashlib.sha256(imported_from.read_bytes()).hexdigest()
                cutover['sections'] = {k: len(v) if isinstance(v, list) else 1 for k, v in state.items()}
            conn.execute('BEGIN IMMEDIATE')
            write_all(conn, state)
            conn.executemany('INSERT INTO meta(key, value) VALUES(?, ?)', [
                ('room_generation', uuid.uuid4().hex), ('commit_seq', '0'), ('cutover', dump(cutover))])
            conn.execute('COMMIT')
            readback = read_rows(conn)[0]
            if readback != state:
                differing = sorted(k for k in set(state) | set(readback) if state.get(k) != readback.get(k))
                raise RoomDbError(f'{temp} did not read back equal to {imported_from or "the new state"} '
                                  f'(sections {differing}); nothing was replaced. Report this with that file.')
        except BaseException:
            conn.close()
            for leftover in (temp, Path(f'{temp}-journal')):
                leftover.unlink(missing_ok=True)
            raise
        conn.close()
        os.replace(temp, self.path)
        if imported_from is not None:
            LOGGER.info('Imported %s into %s (%s)', imported_from, self.path,
                        ', '.join(f'{k} {v}' for k, v in cutover['sections'].items()))

    def retire_json(self):
        """Once room.db is in place, coordination.json is evidence only: renamed so nothing reads it as current."""
        source = self.folder / JSON_NAME
        if source.exists():
            os.replace(source, self.folder / PRE_ROOMDB_NAME)

    def save(self, state):
        """Write every section and record that differs from the last commit, in one transaction. Returns the
        number of rows written; 0 means nothing changed and nothing was committed."""
        with self.lock:
            ops, cache = plan_full(state, self.cache)
            if not ops:
                return 0
            with closing(connect(self.path)) as conn:
                conn.execute('BEGIN IMMEDIATE')
                try:
                    for statement, params in ops:
                        conn.execute(statement, params)
                    conn.execute("UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key = 'commit_seq'")
                    conn.execute('COMMIT')
                except BaseException:
                    if conn.in_transaction:  # SQLite may already have rolled back (an I/O error does)
                        conn.execute('ROLLBACK')
                    raise
            self.cache = cache  # only after the commit: a failed save is retried in full by the next one
            return len(ops)

    def generation(self):
        """(room_generation, commit_seq) as committed."""
        with closing(connect(self.path, readonly=True)) as conn:
            values = meta(conn)
        return values.get('room_generation', ''), int(values.get('commit_seq', 0))


def write_all(conn, state):
    for ord_, (name, value) in enumerate(state.items()):
        if is_record_list(value):
            conn.execute('INSERT INTO section(name, kind, ord) VALUES(?, ?, ?)', (name, LIST, ord_))
            conn.executemany('INSERT INTO rec(section, id, ord, doc) VALUES(?, ?, ?, ?)',
                             [(name, record['id'], index, dump(record)) for index, record in enumerate(value)])
        else:
            conn.execute('INSERT INTO section(name, kind, ord) VALUES(?, ?, ?)', (name, VALUE, ord_))
            conn.execute('INSERT INTO rec(section, id, ord, doc) VALUES(?, ?, ?, ?)', (name, '', 0, dump(value)))


UPSERT_SECTION = ('INSERT INTO section(name, kind, ord) VALUES(?, ?, ?) '
                  'ON CONFLICT(name) DO UPDATE SET kind = excluded.kind, ord = excluded.ord')
UPSERT_REC = ('INSERT INTO rec(section, id, ord, doc) VALUES(?, ?, ?, ?) '
              'ON CONFLICT(section, id) DO UPDATE SET ord = excluded.ord, doc = excluded.doc')
DELETE_SECTION_RECS = 'DELETE FROM rec WHERE section = ?'
DELETE_REC = 'DELETE FROM rec WHERE section = ? AND id = ?'
DELETE_SECTION = 'DELETE FROM section WHERE name = ?'


def plan_full(state, cache):
    """(ops, new cache): every statement needed to make room.db equal state, given what was last committed."""
    ops, fresh = [], {}
    for ord_, (name, value) in enumerate(state.items()):
        old = cache.get(name)
        if is_record_list(value):
            rows = old[2] if old and old[0] == LIST else {}
            if old is None or old[0] != LIST or old[1] != ord_:
                if old is not None and old[0] != LIST:
                    ops.append((DELETE_SECTION_RECS, (name,)))
                ops.append((UPSERT_SECTION, (name, LIST, ord_)))
            new_rows = {}
            for index, record in enumerate(value):
                doc = dump(record)
                entry = (index, digest(doc))
                if rows.get(record['id']) != entry:
                    ops.append((UPSERT_REC, (name, record['id'], index, doc)))
                new_rows[record['id']] = entry
            ops.extend((DELETE_REC, (name, rid)) for rid in rows.keys() - new_rows.keys())
            fresh[name] = (LIST, ord_, new_rows)
        else:
            doc = dump(value)
            entry = (VALUE, ord_, digest(doc))
            if old != entry:
                if old is None or old[0] != VALUE:
                    ops.append((DELETE_SECTION_RECS, (name,)))
                ops.append((UPSERT_SECTION, (name, VALUE, ord_)))
                ops.append((UPSERT_REC, (name, '', 0, doc)))
            fresh[name] = entry
    for name in cache.keys() - state.keys():
        ops.append((DELETE_SECTION_RECS, (name,)))
        ops.append((DELETE_SECTION, (name,)))
    return ops, fresh


def export_json(folder):
    """Rollback (e3): write the current room.db back to coordination.json as today's release writes it, then retire
    room.db so the previous release reads the JSON and a later upgrade imports it again. Run only while the room is
    stopped. Returns the path written."""
    from durable import atomic_write
    folder = Path(folder)
    db = folder / DB_NAME
    if not db.is_file():
        raise RoomDbError(f'{db} does not exist; {folder} is already in coordination.json form')
    conn = connect(db)
    try:
        kinds = {kind for (kind,) in conn.execute('SELECT DISTINCT kind FROM section')}
        unsupported = kinds - {LIST, VALUE}
        if unsupported:
            raise RoomDbError(f'{db} holds record kinds this export does not know ({sorted(unsupported)}); '
                              'nothing was changed. Export it with the release that wrote it.')
        state = read_rows(conn)[0]
        generation = meta(conn).get('room_generation', 'unknown')
    finally:
        conn.close()
    content = json.dumps(state, ensure_ascii=False, indent=2)
    target = folder / JSON_NAME
    atomic_write(target, lambda stream: stream.write(content))
    os.replace(db, folder / f'{DB_NAME}{RETIRED_SUFFIX}-{generation}')
    return target


def main(argv):
    if len(argv) != 3 or argv[1] != 'export':
        print('usage: python roomdb.py export <project folder>   (only while the room is stopped)')
        return 2
    try:
        print(f'Wrote {export_json(argv[2])}')
    except RoomDbError as exc:
        print(f'Error: {exc}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
