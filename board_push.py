"""The board push: what changed since the last one, not the whole board (task eaba007f; contract agreed with frontend
in follow-up 10a8687b).

Every board change used to push the whole project_state to every UI client: 10.1 MB on the live board, 42 ms to
serialise on the event loop. advance() builds the snapshot as before and compares each record with what was last
pushed, so a typical change sends a few KB.

Envelope. Every snapshot (GET /api/runtime/project-state and a reset push) and every delta carries project_id,
stream_id and rev. stream_id is opaque and changes when the room restarts or another project becomes active; rev rises
by one per change within a stream. A rev names one exact board: GET returns the snapshot at the rev it reports, and
a change GET finds is pushed to every client before GET returns, so no client misses a transition.

project_delta: {project_id, stream_id, rev, base, upsert, remove, values}.
- upsert and remove hold only the collections (tasks, reviews, followups, notes) that changed. An upsert replaces a
  record by id in place; a new id appends in payload order. remove lists ids.
- values holds each other top-level key that changed, replaced whole; a key that disappeared is sent as null.
- A change these can't express (records reordered, or a collection that isn't a list of records with unique ids) is
  pushed as a full project_state reset with the new envelope instead.

A task's observed_at is the time its view was built, so it is left out of the comparison.
"""
import json
import uuid

COLLECTIONS = ('tasks', 'reviews', 'followups', 'notes')
ENVELOPE = ('project_id', 'stream_id', 'rev')
UNCOMPARED = {'tasks': ('observed_at',)}  # per-collection fields that change on every snapshot
ENCODER = json.JSONEncoder(separators=(',', ':'))


def record_ids(items):
    """The ids of a list of records, or None if it isn't one (every item an object with a unique string id)."""
    if not isinstance(items, list):
        return None
    ids = [item.get('id') if isinstance(item, dict) else None for item in items]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        return None
    return ids


def compared(collection, record):
    skip = UNCOMPARED.get(collection, ())
    return ENCODER.encode({k: v for k, v in record.items() if k not in skip} if skip else record)


class BoardPush:
    """One stream of board revisions for the active project. Not thread-safe: called on the event loop only."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.project_id = None
        self.stream_id = ''
        self.rev = 0
        self.records = {}  # collection -> {id: compared text}, in pushed order
        self.values = {}   # top-level key -> text
        self.whole = None  # the whole board's text, while a collection can't be compared record by record

    def advance(self):
        """(snapshot, message) for the board now. snapshot carries the envelope at its rev. message is the event that
        brings clients from the previous rev to it: ('project_delta', delta), ('project_state', snapshot) for a reset,
        or None when nothing changed. The caller emits message before handing snapshot to anyone."""
        snapshot = self.runtime.project_room.snapshot()
        for key in ENVELOPE:
            snapshot.pop(key, None)
        project_id = self.runtime.identity
        records, values = self.texts(snapshot)
        same_stream = project_id == self.project_id and bool(self.stream_id)
        if records is None:
            # Pushed whole, but only when it changed (an older project's notes may have no ids).
            whole = ENCODER.encode({k: [compared(k, r) if isinstance(r, dict) else r for r in v]
                                    if k in COLLECTIONS and isinstance(v, list) else v for k, v in snapshot.items()})
            if same_stream and whole == self.whole:
                return self.envelope(snapshot), None
            message = self.reset(snapshot, project_id, None, {})
            self.whole = whole
            return message
        self.whole = None
        if not same_stream:
            return self.reset(snapshot, project_id, records, values)
        delta = {'upsert': {}, 'remove': {}, 'values': {}}
        for collection, texts in records.items():
            pushed = self.records.get(collection, {})
            kept = [i for i in pushed if i in texts]
            ids = list(texts)
            if ids[:len(kept)] != kept:
                return self.reset(snapshot, project_id, records, values)  # reordered: not expressible as a delta
            changed = [record for record in snapshot[collection] if pushed.get(record['id']) != texts[record['id']]]
            removed = [i for i in pushed if i not in texts]
            if changed:
                delta['upsert'][collection] = changed
            if removed:
                delta['remove'][collection] = removed
        for key in self.values.keys() - values.keys():
            delta['values'][key] = None
        for key, text in values.items():
            if self.values.get(key) != text:
                delta['values'][key] = snapshot[key]
        self.records, self.values = records, values
        if not any(delta.values()):
            return self.envelope(snapshot), None
        self.rev += 1
        message = {'project_id': self.project_id, 'stream_id': self.stream_id, 'rev': self.rev, 'base': self.rev - 1,
                   **{part: body for part, body in delta.items() if body}}
        return self.envelope(snapshot), ('project_delta', message)

    def texts(self, snapshot):
        """({collection: {id: text}}, {key: text}), or (None, values) when a collection isn't a list of records."""
        records = {}
        for collection in COLLECTIONS:
            items = snapshot.get(collection, [])
            ids = record_ids(items)
            if ids is None:
                return None, {}
            records[collection] = {record['id']: compared(collection, record) for record in items}
        values = {key: ENCODER.encode(value) for key, value in snapshot.items() if key not in COLLECTIONS}
        return records, values

    def reset(self, snapshot, project_id, records, values):
        """A full project_state push. A new project, or a restart, starts a new stream."""
        if project_id != self.project_id or not self.stream_id:
            self.project_id, self.stream_id, self.rev = project_id, uuid.uuid4().hex, 0
        self.rev += 1
        # A board that can't be compared record by record is pushed whole until it can be.
        self.records, self.values = (records, values) if records is not None else ({}, {})
        snapshot = self.envelope(snapshot)
        return snapshot, ('project_state', snapshot)

    def envelope(self, snapshot):
        snapshot.update(project_id=self.project_id, stream_id=self.stream_id, rev=self.rev)
        return snapshot
