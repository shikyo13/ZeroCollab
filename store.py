"""JSONL message persistence for the chat room with observer callbacks."""

from contextlib import contextmanager
import json
import os
import time
import threading
import uuid
from pathlib import Path

from durable import atomic_write


class MessageStore:
    def __init__(self, path: str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._messages: list[dict] = []
        self._next_id: int = 0  # monotonically increasing, survives deletions
        self._lock = threading.RLock()
        self._callbacks: list = []  # called on each new message
        self._delete_callbacks: list = []  # called on message deletion
        self.upload_dir = self._path.parent.parent / "uploads"  # Default fallback
        self._load()
        counter = self._path.with_suffix(self._path.suffix + ".next-id")
        if counter.exists():
            self._next_id = max(self._next_id, int(counter.read_text(encoding="utf-8")))

    def _load(self):
        if not self._path.exists():
            return
        max_id = -1
        with open(self._path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    # Preserve persisted ID; fall back to line number for legacy data
                    if "id" not in msg:
                        msg["id"] = i
                    if msg["id"] > max_id:
                        max_id = msg["id"]
                    self._messages.append(msg)
                except json.JSONDecodeError:
                    continue
        self._next_id = max_id + 1

    def on_message(self, callback):
        """Register a callback(msg) called whenever a message is added."""
        self._callbacks.append(callback)

    def add(self, sender: str, text: str, msg_type: str = "chat",
            attachments: list | None = None, reply_to: int | None = None,
            channel: str = "general",
            metadata: dict | None = None,
            uid: str | None = None,
            timestamp: float | None = None,
            time_str: str | None = None,
            _bulk: bool = False) -> dict:
        with self._lock:
            ts = timestamp if timestamp is not None else time.time()
            msg = {
                "id": self._next_id,
                "uid": uid or str(uuid.uuid4()),
                "sender": sender,
                "text": text,
                "type": msg_type,
                "timestamp": ts,
                "time": time_str or time.strftime("%H:%M:%S"),
                "attachments": attachments or [],
                "channel": channel,
            }
            if reply_to is not None:
                msg["reply_to"] = reply_to
            if metadata:
                msg["metadata"] = metadata
            self._next_id += 1
            if not _bulk:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            self._messages.append(msg)

        # Fire callbacks outside the lock (skip during bulk import)
        if not _bulk:
            for cb in self._callbacks:
                try:
                    cb(msg)
                except Exception:
                    pass

        return msg

    @contextmanager
    def import_batch(self):
        """Stage bulk additions under the store lock; publish only after persistence.

        Published message dicts must not be mutated. The reentrant lock allows
        add(_bulk=True) to reuse normal record construction inside this scope.
        """
        with self._lock:
            previous = self._messages
            self._messages = list(previous)
            try:
                yield
                if self._messages != previous:
                    self._rewrite()
            except BaseException:
                self._messages = previous
                # Allocated IDs may be skipped, never reused after a failed write.
                raise

    def resolve_import_replies(self, pending):
        """Resolve staged reply UIDs without mutating previously published dicts."""
        with self._lock:
            uid_to_id = {m['uid']: m['id'] for m in self._messages if m.get('uid')}
            targets = {local_id: uid_to_id[uid] for local_id, uid in pending if uid in uid_to_id}
            self._messages = [{**m, 'reply_to': targets[m['id']]} if m['id'] in targets else m
                              for m in self._messages]

    def flush_bulk(self):
        """Write all in-memory messages to disk. Call after bulk add operations."""
        with self._lock:
            self._rewrite()

    def update_reply_to(self, msg_id: int, reply_to: int):
        """Set reply_to on an existing message (used by import to rebuild links)."""
        self.update_message(msg_id, {"reply_to": reply_to})

    def _rewrite(self, messages=None):
        """Commit a replacement log, then publish its in-memory view.

        Persist the high-water mark first: if log replacement fails, skipping
        IDs is safe; reusing IDs referenced by saved cursors is not.
        Caller holds _lock. Bulk imports may already have staged memory edits.
        """
        candidate = self._messages if messages is None else messages
        counter = self._path.with_suffix(self._path.suffix + ".next-id")
        atomic_write(counter, lambda stream: stream.write(str(self._next_id)))

        def write_messages(stream):
            for message in candidate:
                stream.write(json.dumps(message, ensure_ascii=False) + "\n")

        atomic_write(self._path, write_messages)
        self._messages = candidate

    def get_by_id(self, msg_id: int) -> dict | None:
        with self._lock:
            for m in self._messages:
                if m["id"] == msg_id:
                    return m
            return None

    def get_recent(self, count: int = 50, channel: str | None = None) -> list[dict]:
        with self._lock:
            msgs = self._messages
            if channel:
                msgs = [m for m in msgs if m.get("channel", "general") == channel]
            return list(msgs[-count:])

    def get_since(self, since_id: int = 0, channel: str | None = None) -> list[dict]:
        with self._lock:
            msgs = [m for m in self._messages if m["id"] > since_id]
            if channel:
                msgs = [m for m in msgs if m.get("channel", "general") == channel]
            return msgs

    def search(self, query: str, channel_allowed=None, limit: int = 20) -> list[dict]:
        """Newest first: messages whose text or an attachment's name contains query, ignoring case. All messages
        are already in memory, so a scan is enough (about 20k messages in tens of milliseconds); limit stops it."""
        needle = query.casefold()
        found = []
        with self._lock:
            for message in reversed(self._messages):
                if channel_allowed is not None and not channel_allowed(message.get("channel", "general")):
                    continue
                names = [a.get("name") or "" for a in message.get("attachments") or [] if isinstance(a, dict)]
                if needle in (message.get("text") or "").casefold() or any(needle in n.casefold() for n in names):
                    found.append(message)
                    if len(found) >= limit:
                        break
        return found

    def delete(self, msg_ids: list[int]) -> list[int]:
        """Delete messages, retaining uploads shared by chat and board records."""
        with self._lock:
            existing = {m["id"] for m in self._messages}
            deleted = list(dict.fromkeys(mid for mid in msg_ids if mid in existing))
            if deleted:
                removed = set(deleted)
                self._rewrite([m for m in self._messages if m["id"] not in removed])

        # Upload lifetime is independent of a message: task evidence and other
        # messages can retain the same URL. Physical cleanup needs all references.
        for cb in self._delete_callbacks:
            try:
                cb(deleted)
            except Exception:
                pass
        return deleted

    def on_delete(self, callback):
        """Register a callback(ids) called when messages are deleted."""
        self._delete_callbacks.append(callback)

    def update_message(self, msg_id: int, updates: dict) -> dict | None:
        """Commit updated fields and return the updated message, or None."""
        with self._lock:
            for i, message in enumerate(self._messages):
                if message["id"] == msg_id:
                    updated = {**message, **updates}
                    candidate = list(self._messages)
                    candidate[i] = updated
                    self._rewrite(candidate)
                    return dict(updated)
            return None

    def clear(self, channel: str | None = None):
        """Clear all messages, or only messages in the specified channel."""
        with self._lock:
            candidate = [m for m in self._messages
                         if channel and m.get("channel", "general") != channel]
            self._rewrite(candidate)

    def rename_channel(self, old_name: str, new_name: str):
        """Migrate all messages from old_name to new_name."""
        with self._lock:
            if any(m.get("channel") == old_name for m in self._messages):
                self._rewrite([{**m, "channel": new_name} if m.get("channel") == old_name
                               else m for m in self._messages])

    def rename_sender(self, old_name: str, new_name: str) -> int:
        """Rename sender on all matching messages. Returns count updated."""
        with self._lock:
            count = sum(m.get("sender") == old_name for m in self._messages)
            if count:
                self._rewrite([{**m, "sender": new_name} if m.get("sender") == old_name
                               else m for m in self._messages])
        return count

    def delete_channel(self, name: str):
        """Remove all messages belonging to a deleted channel."""
        with self._lock:
            candidate = [m for m in self._messages if m.get("channel") != name]
            if len(candidate) != len(self._messages):
                self._rewrite(candidate)

    @property
    def last_id(self) -> int:
        with self._lock:
            return self._messages[-1]["id"] if self._messages else -1
