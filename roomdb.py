"""room.db: a project's coordination records in SQLite (task d6139872; addendum (e) r4 e1 to e3; plan r2 fe1a97be).

coordination.json was rewritten whole on every save: 57 ms for the live room's 11 MB, on the event loop, from 83 call
sites. room.db keeps the same state, one row per record, and a save writes only the rows that changed, in one
transaction (synchronous=EXTRA). ProjectRoom still holds the whole state in memory, so every reader in the room is
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

Rollback by deployment (harness c8d41869). A release before room.db reads only coordination.json. So before writes
close for any deployment, export_all() writes every project's coordination.json beside its room.db, and room.db
records that file's sha256 (meta pending_export before publishing, last_export after). On the next open, an export
nobody changed is retired and room.db stays current. One that an older release rewrote is newer, so it is imported as
a new generation and the old room.db is kept as room.db.superseded-<generation>; the next export imports it first too.

Durability (systems ab210df2, 4a3510ad). Saves ask SQLite for synchronous=EXTRA, read back. On POSIX that syncs the
directory after the rollback journal is deleted; SQLite's Windows VFS ignores that directory sync, so on Windows a
committed save's survival of power loss is not established. Only process kills were tested. Every rename that
publishes or retires a file goes through durable_replace() (MoveFileExW with MOVEFILE_WRITE_THROUGH on Windows, a
directory fsync elsewhere), and a file is published before its source is retired. A folder that has lost both live
files but keeps their evidence is refused, never opened as an empty board.
"""
import hashlib
import json
import logging
import os
import sqlite3
import sys
import threading
import time
import traceback
import uuid
from contextlib import closing
from pathlib import Path

DB_NAME = 'room.db'
JSON_NAME = 'coordination.json'
PRE_ROOMDB_NAME = 'coordination.pre-roomdb.json'
EXPORT_LEFT_NAME = 'coordination.last-export.json'  # an untouched deployment export, once room.db is opened again
IMPORTING_SUFFIX = '.importing'
RETIRED_SUFFIX = '.exported'
SUPERSEDED_SUFFIX = '.superseded'
LAST_EXPORT_KEY = 'last_export'  # meta: sha256 of the coordination.json export_for_deploy() wrote
PENDING_EXPORT_KEY = 'pending_export'  # meta: sha256 of an export about to be published (systems 4a3510ad)
SYNCHRONOUS_EXTRA = 3            # PRAGMA synchronous value for EXTRA
MOVEFILE_REPLACE_EXISTING = 0x1
MOVEFILE_WRITE_THROUGH = 0x8     # MoveFileExW returns only once the rename is on disk
SCHEMA_VERSION = 1           # PRAGMA user_version; a reader refuses a file newer than it knows
BUSY_TIMEOUT_MS = 5000       # a reader in this process waits this long for a writer's lock
# A touched save is a full comparison when this long has passed since the last one, so a record a caller forgot
# to name is written within this window (harness 4b789e62). The room's shutdown save is always full.
FULL_DIFF_SECONDS = 30
# Tests set ROOM_DB_VERIFY=1: every touched save is checked against a full comparison and fails loudly if it missed.
VERIFY = os.environ.get('ROOM_DB_VERIFY') == '1'
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


# One reused compact encoder. ASCII output is the C fast path: 27 ms for the live room's 3,508 records, against 34 ms
# with ensure_ascii=False. json.loads reads either form back to the same value.
ENCODER = json.JSONEncoder(separators=(',', ':'))


def dump(value):
    return ENCODER.encode(value)


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


def durable_replace(source, target):
    """os.replace that returns only once the rename is on disk: MoveFileExW with MOVEFILE_WRITE_THROUGH on Windows,
    an fsync of the directory elsewhere. Flushing a file's data does not make its new name durable (systems ab210df2)."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        kernel.MoveFileExW.restype = wintypes.BOOL
        if not kernel.MoveFileExW(str(source), str(target), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH):
            raise ctypes.WinError(ctypes.get_last_error())
        return
    os.replace(source, target)
    directory = os.open(Path(target).parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def durable_write_text(path, text):
    """Write text to a sibling temp file, flush it to disk, then durably rename it into place. Returns the sha256 of
    the bytes written."""
    path = Path(path)
    data = text.encode('utf-8')
    temp = path.with_name(f'{path.name}.{uuid.uuid4().hex[:8]}.tmp')
    try:
        with open(temp, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        durable_replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return hashlib.sha256(data).hexdigest()


def connect(path, *, readonly=False):
    """A connection for one save or one read. Callers close it (closing())."""
    if readonly:
        conn = sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro', uri=True, timeout=BUSY_TIMEOUT_MS / 1000,
                               isolation_level=None)
    else:
        conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        conn.execute('PRAGMA journal_mode=DELETE')
        conn.execute('PRAGMA synchronous=EXTRA')
        if conn.execute('PRAGMA synchronous').fetchone()[0] != SYNCHRONOUS_EXTRA:
            conn.close()
            raise RoomDbError(f'{path}: this SQLite did not accept synchronous=EXTRA, so a commit could be lost on power '
                              'loss; nothing was written. Report the Python and SQLite versions.')
    conn.execute(f'PRAGMA busy_timeout={BUSY_TIMEOUT_MS}')
    version = conn.execute('PRAGMA user_version').fetchone()[0]
    if version > SCHEMA_VERSION:
        conn.close()
        raise RoomDbError(f'{path} was written by a newer ZeroCollab (room.db schema {version}; this release reads up '
                          f'to {SCHEMA_VERSION}). Install that release again, or export it with its own roomdb.py.')
    return conn


def read_rows(conn):
    """(state, cache) from an open room.db. cache maps each section to what a later save compares against: each
    record's committed JSON text. Comparing the text directly costs about 11 MB of memory on the live room and saves
    the 10 ms a digest of every record took."""
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
            cache[name] = (VALUE, ord_, doc)
        else:
            state[name] = [json.loads(doc) for _, _, doc in found]
            cache[name] = (LIST, ord_, {rid: (index, doc) for index, (rid, _, doc) in enumerate(found)})
    return state, cache


def meta(conn):
    return dict(conn.execute('SELECT key, value FROM meta'))


def read_db(db):
    """(state, meta) from room.db, read-only. A crash can leave a hot journal that a read-only connection can't roll
    back (SQLITE_READONLY_ROLLBACK), so that case reopens read-write, which recovers it (harness c8d41869)."""
    try:
        with closing(connect(db, readonly=True)) as conn:
            return read_rows(conn)[0], meta(conn)
    except sqlite3.OperationalError as exc:
        LOGGER.warning('%s could not be read read-only (%s); reopening it read-write', db, exc)
        with closing(connect(db)) as conn:
            return read_rows(conn)[0], meta(conn)


def evidence(folder):
    """Files an import, export or supersede left behind: proof the project had records if both live files are gone."""
    folder = Path(folder)
    return sorted(p.name for p in [folder / PRE_ROOMDB_NAME, *folder.glob(f'{DB_NAME}.*')]
                  if p.is_file() and not p.name.endswith((IMPORTING_SUFFIX, '-journal')))


def current_form(folder):
    """Which file holds the project's current records: 'db', 'json', 'new' (nothing there yet) or 'missing' (both
    live files gone, evidence kept). With both present the JSON is current, unless it is an untouched export that
    export_for_deploy() wrote (its digest is recorded as last_export, or as pending_export when the recording after
    publication failed) or equals room.db (a crash between an import and retiring the JSON)."""
    folder = Path(folder)
    db, source = folder / DB_NAME, folder / JSON_NAME
    if db.is_file() and source.is_file():
        state, values = read_db(db)
        if hashlib.sha256(source.read_bytes()).hexdigest() in {values.get(LAST_EXPORT_KEY), values.get(PENDING_EXPORT_KEY)}:
            return 'db'
        return 'db' if json.loads(source.read_text('utf-8')) == state else 'json'
    if db.is_file():
        return 'db'
    if source.is_file():
        return 'json'
    return 'missing' if evidence(folder) else 'new'


def read_state(folder):
    """A project's coordination state, read-only, from whichever file is current (current_form), otherwise {}. Never
    imports or migrates; only ProjectRoom loading a project does."""
    folder = Path(folder)
    try:
        form = current_form(folder)
        if form == 'db':
            return read_db(folder / DB_NAME)[0]
        if form == 'json':
            return json.loads((folder / JSON_NAME).read_text('utf-8'))
    except (OSError, ValueError, sqlite3.Error):
        LOGGER.exception('Could not read the records in %s', folder)
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
        self.last_full = time.monotonic()  # open() reads the whole file, which counts as the first comparison
        self.full_due = False  # set by a failed save: what it meant to write is retried by a full comparison

    @classmethod
    def open(cls, folder):
        """(store, state) for a project folder: open room.db, or import a current coordination.json first, or create
        an empty room.db for a new project. Refuses a folder whose live files are gone but whose evidence remains."""
        store = cls(folder)
        store.folder.mkdir(parents=True, exist_ok=True)
        source = store.folder / JSON_NAME
        form = current_form(store.folder)
        if form == 'missing':
            raise RoomDbError(f'{store.folder} has neither {DB_NAME} nor {JSON_NAME}, but holds '
                              f'{", ".join(evidence(store.folder))}. Nothing was created: restore the newest of those as '
                              f'{DB_NAME} or {JSON_NAME}, then open the project again.')
        if form == 'json':
            store.create(json.loads(source.read_text('utf-8')), imported_from=source)
        elif form == 'new':
            store.create(dict(EMPTY_STATE))
        with closing(connect(store.path)) as conn:
            state, store.cache = read_rows(conn)
            generation = meta(conn).get('room_generation', 'unknown')
        store.retire_json(imported=form == 'json', generation=generation)
        return store, state

    def create(self, state, imported_from=None):
        """Write state to a new file beside room.db, read it back and compare, then move it into place. An existing
        room.db is first kept as room.db.superseded-<generation>. A crash before the move leaves the .importing file,
        which the next open replaces, and coordination.json, which it imports again."""
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
        if self.path.is_file():
            old = read_db(self.path)[1].get('room_generation', 'unknown')
            durable_replace(self.path, self.path.with_name(f'{DB_NAME}{SUPERSEDED_SUFFIX}-{old}'))
        durable_replace(temp, self.path)
        if imported_from is not None:
            LOGGER.info('Imported %s into %s (%s)', imported_from, self.path,
                        ', '.join(f'{k} {v}' for k, v in cutover['sections'].items()))

    def retire_json(self, imported, generation):
        """Once room.db is in place, coordination.json is evidence only: renamed so nothing reads it as current. An
        imported file takes coordination.pre-roomdb.json, or a per-generation name once that exists, so no evidence
        is overwritten. An untouched deployment export is only a copy of room.db."""
        source = self.folder / JSON_NAME
        if not source.exists():
            return
        if not imported:
            target = self.folder / EXPORT_LEFT_NAME
        elif (self.folder / PRE_ROOMDB_NAME).exists():
            target = self.folder / f'coordination.pre-roomdb-{generation}.json'
        else:
            target = self.folder / PRE_ROOMDB_NAME
        durable_replace(source, target)

    def save(self, state, touched=None):
        """Write what differs from the last commit, in one transaction, and return the number of statements; 0 means
        nothing changed and nothing was committed. touched=None compares everything (always correct, about 35 ms on
        the live room). touched names everything the caller mutated since the last save (plan_touched), and is
        still a full comparison once FULL_DIFF_SECONDS have passed since the last one."""
        with self.lock:
            now = time.monotonic()
            full = touched is None or self.full_due or now - self.last_full >= FULL_DIFF_SECONDS
            if full:
                ops, cache = plan_full(state, self.cache)
            else:
                ops, cache = plan_touched(state, self.cache, touched)
                if VERIFY:
                    self.verify(state, cache, touched)
            if ops:
                try:
                    self.commit(ops)
                except BaseException:
                    self.full_due = True  # the next save compares everything, so nothing this one held is skipped
                    raise
                self.cache = cache  # only after the commit
            if full:
                self.last_full, self.full_due = now, False
            return len(ops)

    def commit(self, ops):
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

    @staticmethod
    def verify(state, cache, touched):
        """ROOM_DB_VERIFY: a touched save must leave nothing for a full comparison to find. Names what was missed and
        where the save came from, so the call site can name it."""
        missed, _ = plan_full(state, cache)
        if missed:
            rows = sorted({f'{params[0]}/{params[1]}' if statement == UPSERT_REC else str(params[0])
                           for statement, params in missed})
            caller = traceback.extract_stack(limit=4)[0]
            raise RoomDbError(f'save(touched={sorted(map(str, touched))[:8]}) from {caller.filename}:{caller.lineno} '
                              f'missed {rows[:12]}: name them in touched, or call save() with no argument')

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


def plan_section(name, ord_, value, old, ops):
    """Append the statements that make one section equal value, given its last committed entry (or None).
    Returns the section's new cache entry."""
    if is_record_list(value):
        rows = old[2] if old and old[0] == LIST else {}
        if old is None or old[0] != LIST or old[1] != ord_:
            if old is not None and old[0] != LIST:
                ops.append((DELETE_SECTION_RECS, (name,)))
            ops.append((UPSERT_SECTION, (name, LIST, ord_)))
        new_rows = {}
        for index, record in enumerate(value):
            doc = dump(record)
            entry = (index, doc)
            if rows.get(record['id']) != entry:
                ops.append((UPSERT_REC, (name, record['id'], index, doc)))
            new_rows[record['id']] = entry
        ops.extend((DELETE_REC, (name, rid)) for rid in rows.keys() - new_rows.keys())
        return LIST, ord_, new_rows
    doc = dump(value)
    entry = (VALUE, ord_, doc)
    if old != entry:
        if old is None or old[0] != VALUE or old[1] != ord_:
            if old is not None and old[0] != VALUE:
                ops.append((DELETE_SECTION_RECS, (name,)))
            ops.append((UPSERT_SECTION, (name, VALUE, ord_)))
        ops.append((UPSERT_REC, (name, '', 0, doc)))
    return entry


def plan_removed(state, cache, ops):
    for name in cache.keys() - state.keys():
        ops.append((DELETE_SECTION_RECS, (name,)))
        ops.append((DELETE_SECTION, (name,)))


def plan_full(state, cache):
    """(ops, new cache): every statement needed to make room.db equal state, given what was last committed."""
    ops, fresh = [], {}
    for ord_, (name, value) in enumerate(state.items()):
        fresh[name] = plan_section(name, ord_, value, cache.get(name), ops)
    plan_removed(state, cache, ops)
    return ops, fresh


def plan_touched(state, cache, touched):
    """(ops, new cache) when the caller names the list records it mutated since the last save: (section, id) for
    a record, or a section name for a whole section. Value sections (cursors, sessions, receipts, continuity and
    the rest, about 350 KB on the live room) are always compared, because today any save flushes every pending
    mutation and code relies on that. Only existing records in list sections that nobody named are skipped: an
    append writes only the new records, any other change to a section's ids rewrites that section in full. A
    missed name is caught by ROOM_DB_VERIFY in tests and by the next full diff (FULL_DIFF_SECONDS) in production."""
    whole, records = set(), {}
    for item in touched:
        if isinstance(item, str):
            whole.add(item)
        else:
            records.setdefault(item[0], set()).add(item[1])
    ops, fresh = [], {}
    for ord_, (name, value) in enumerate(state.items()):
        old = cache.get(name)
        if name in whole or old is None or old[0] == VALUE or old[1] != ord_ or not isinstance(value, list):
            fresh[name] = plan_section(name, ord_, value, old, ops)
        else:
            fresh[name] = plan_list_touched(name, ord_, value, old, records.get(name, set()), ops)
    plan_removed(state, cache, ops)
    return ops, fresh


def plan_list_touched(name, ord_, value, old, named, ops):
    rows = old[2]
    ids = [record.get('id') if isinstance(record, dict) else None for record in value]
    known = len(rows)
    if ids[:known] != list(rows) or not is_record_list(value):
        return plan_section(name, ord_, value, old, ops)  # removed, reordered or reshaped: the whole section
    new_rows = dict(rows)
    positions = {rid: rows[rid][0] for rid in named if rid in rows}
    positions.update((rid, index) for index, rid in enumerate(ids[known:], known))  # appended records
    for rid, index in positions.items():
        doc = dump(value[index])
        entry = (index, doc)
        if rows.get(rid) != entry:
            ops.append((UPSERT_REC, (name, rid, index, doc)))
        new_rows[rid] = entry
    return LIST, ord_, new_rows


def exportable(conn, db):
    """(state, meta) to export, refused with nothing changed if room.db holds a record kind this release can't write."""
    kinds = {kind for (kind,) in conn.execute('SELECT DISTINCT kind FROM section')}
    unsupported = kinds - {LIST, VALUE}
    if unsupported:
        raise RoomDbError(f'{db} holds record kinds this export does not know ({sorted(unsupported)}); '
                          'nothing was changed. Export it with the release that wrote it.')
    return read_rows(conn)[0], meta(conn)


def as_json(state):
    return json.dumps(state, ensure_ascii=False, indent=2)  # coordination.json exactly as a release before room.db writes it


def export_json(folder):
    """Rollback by hand (e3): write the current room.db back to coordination.json as today's release writes it, then
    retire room.db so the previous release reads the JSON and a later upgrade imports it again. Run only while the
    room is stopped. Returns the path written."""
    folder = Path(folder)
    db = folder / DB_NAME
    if not db.is_file():
        raise RoomDbError(f'{db} does not exist; {folder} is already in coordination.json form')
    with closing(connect(db)) as conn:
        state, values = exportable(conn, db)
    target = folder / JSON_NAME
    durable_write_text(target, as_json(state))  # published, and on disk, before room.db is retired
    durable_replace(db, folder / f'{DB_NAME}{RETIRED_SUFFIX}-{values.get("room_generation", "unknown")}')
    return target


def set_meta(conn, values):
    """Commit meta changes in one transaction: {key: text}, or None to delete the key."""
    conn.execute('BEGIN IMMEDIATE')
    for key, value in values.items():
        if value is None:
            conn.execute('DELETE FROM meta WHERE key = ?', (key,))
        else:
            conn.execute('INSERT INTO meta(key, value) VALUES(?, ?) '
                         'ON CONFLICT(key) DO UPDATE SET value = excluded.value', (key, value))
    conn.execute('COMMIT')


def export_for_deploy(folder):
    """Before writes close for a deployment: coordination.json beside room.db, so a release before room.db (a
    rollback) reads the current board. room.db stays current and records the export's sha256, so the next open tells
    an untouched export from one an older release rewrote (current_form).

    Two rules keep it from losing work (systems 4a3510ad). A coordination.json an older release rewrote is current,
    so it is imported before anything is exported over it; that covers a project nobody opened since a rollback. And
    the digest is committed as pending_export before the file is published, so whatever fails afterwards, a published
    export is still recognised as a copy and never re-imported over later saves."""
    folder = Path(folder)
    if current_form(folder) == 'json':
        Store.open(folder)  # imports the newer JSON as room.db and retires it
    db, target = folder / DB_NAME, folder / JSON_NAME
    with closing(connect(db)) as conn:
        state, _ = exportable(conn, db)
        text = as_json(state)
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        # Whatever coordination.json is on disk now is a recognised copy (current_form said 'db'). Its digest stays
        # recorded, as last_export, in the same commit that names the new file, so a retry after a failure never
        # forgets the file it would leave behind (systems 13f5dc08).
        on_disk = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
        set_meta(conn, {PENDING_EXPORT_KEY: digest, **({LAST_EXPORT_KEY: on_disk} if on_disk else {})})
        durable_write_text(target, text)
        record_export(conn, digest)


def record_export(conn, digest):
    set_meta(conn, {LAST_EXPORT_KEY: digest, PENDING_EXPORT_KEY: None})


def export_all(projects):
    """export_for_deploy() for every project folder that holds room.db, active or not. Returns how many."""
    projects = Path(projects)
    folders = [f for f in sorted(projects.iterdir()) if (f / DB_NAME).is_file()] if projects.is_dir() else []
    for folder in folders:
        export_for_deploy(folder)
    return len(folders)


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
