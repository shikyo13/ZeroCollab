"""Export/import archive for project history (chat messages)."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import io
import json
import math
import re
import threading
import time
import uuid
import zipfile
import zlib

SCHEMA_VERSION = 1
MAX_IMPORT_SIZE = 50 * 1024 * 1024  # 50MB uncompressed

_import_lock = threading.Lock()
_export_lock = threading.Lock()


class ExportBusyError(RuntimeError):
    pass


def _fingerprint(record: dict) -> str:
    """Deterministic fingerprint for legacy records without uid."""
    # Field list is fixed so fingerprints of older exports stay stable.
    parts = [
        record.get("sender", "") or record.get("created_by", ""),
        record.get("text", "") or record.get("title", ""),
        str(record.get("timestamp", "") or record.get("created_at", "")),
        record.get("channel", ""),
        record.get("body", ""),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _ensure_uid(record: dict) -> str:
    """Return existing uid or generate a fingerprint-based one."""
    if record.get("uid"):
        return record["uid"]
    return "fp-" + _fingerprint(record)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def build_export(store, app_version: str = "") -> bytes:
    """Allow one memory-intensive export builder, including cancelled requests."""
    if not _export_lock.acquire(blocking=False):
        raise ExportBusyError("export already running")
    try:
        return _build_export(store, app_version)
    finally:
        _export_lock.release()


def _build_export(store, app_version):
    """Build a zip archive of the message store. Returns zip bytes."""
    buf = io.BytesIO()

    # Collect data
    messages = store.get_recent(count=999_999_999)
    uid_by_id = {msg['id']: _ensure_uid(msg) for msg in messages}

    # Build manifest
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "archive_id": str(uuid.uuid4()),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "product": "zerocollab",
        "app_version": app_version,
        "counts": {
            "messages": len(messages),
        },
        "attachments_included": False,
    }

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        # Serialize one record at a time instead of retaining all JSONL lines
        # and a second joined copy alongside the compressed archive.
        with zf.open("messages.jsonl", "w", force_zip64=True) as stream:
            for msg in messages:
                exported = dict(msg)
                exported["uid"] = uid_by_id[msg["id"]]
                if "reply_to" in exported and exported["reply_to"] in uid_by_id:
                    exported["reply_to_uid"] = uid_by_id[exported["reply_to"]]
                stream.write((json.dumps(exported, ensure_ascii=False) + "\n").encode("utf-8"))

    return buf.getvalue()


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def import_archive(zip_bytes: bytes, store, channel_list: list[str],
                   max_channels: int = 8) -> dict:
    """Import a zip archive, merging its messages into the store.

    Returns a report dict with counts and warnings.
    """
    # Issue #5: import lock prevents concurrent imports
    if not _import_lock.acquire(blocking=False):
        return {"ok": False, "error": "import already running"}

    try:
        return _do_import(zip_bytes, store, channel_list, max_channels)
    finally:
        _import_lock.release()


@asynccontextmanager
async def prepared_import(zip_bytes):
    """Hold admission through worker preparation and the caller's synchronous apply."""
    if not _import_lock.acquire(blocking=False):
        yield {"ok": False, "error": "import already running"}
        return
    worker = None
    deferred_release = False
    try:
        worker = asyncio.create_task(asyncio.to_thread(_prepare_import, zip_bytes))
        try:
            prepared = await asyncio.shield(worker)
        except asyncio.CancelledError:
            # The pure worker cannot be cancelled by cancelling the request.
            # Keep admission until it exits; never apply its abandoned result.
            def finished(task):
                try:
                    if not task.cancelled():
                        task.exception()
                finally:
                    _import_lock.release()
            worker.add_done_callback(finished)
            deferred_release = True
            raise
        yield prepared
    finally:
        if not deferred_release:
            _import_lock.release()


def _message_lines(zf):
    # Preserve read-before-mutation checksum validation without retaining the
    # entire uncompressed entry. The second pass retains one line at a time.
    with zf.open("messages.jsonl") as stream:
        total = 0
        while chunk := stream.read(64 * 1024):
            total += len(chunk)
            if total > MAX_IMPORT_SIZE:
                raise ValueError("messages.jsonl exceeds import size limit")
    with zf.open("messages.jsonl") as stream:
        with io.TextIOWrapper(stream, encoding="utf-8", errors="replace", newline="\n") as text:
            yield from text


def _prepare_import(zip_bytes):
    """Validate and parse without holding the store lock or changing live state."""
    # Validate zip
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            return _parse_import(zf)
    except (zipfile.BadZipFile, EOFError, zlib.error):
        return {"ok": False, "error": "invalid or corrupt zip archive"}
    except NotImplementedError:
        return {"ok": False, "error": "unsupported zip compression"}


def _valid_message(msg):
    """Check fields consumed by fingerprinting, merge and the chat renderer."""
    if not isinstance(msg, dict):
        return False
    for key in ("sender", "text", "type", "channel", "created_by", "title", "body"):
        if key in msg and not isinstance(msg[key], str):
            return False
    for key in ("uid", "reply_to_uid", "time"):
        if msg.get(key) is not None and not isinstance(msg[key], str):
            return False
    ts = msg.get("timestamp")
    if ts is not None and (type(ts) not in (int, float) or
                           (isinstance(ts, float) and not math.isfinite(ts))):
        return False
    if msg.get("metadata") is not None and not isinstance(msg["metadata"], dict):
        return False
    attachments = msg.get("attachments")
    if attachments is not None and (
        not isinstance(attachments, list) or
        any(not isinstance(item, dict) for item in attachments)
    ):
        return False
    return True


def _parse_import(zf):
    warnings = []
    # Check total uncompressed size
    total_size = sum(info.file_size for info in zf.infolist())
    if total_size > MAX_IMPORT_SIZE:
        return {"ok": False, "error": f"archive too large ({total_size // 1024 // 1024}MB, limit {MAX_IMPORT_SIZE // 1024 // 1024}MB)"}
    if any(info.flag_bits & 1 for info in zf.infolist()):
        return {"ok": False, "error": "encrypted zip archives are not supported"}

    # Read manifest
    if "manifest.json" not in zf.namelist():
        return {"ok": False, "error": "missing manifest.json"}

    try:
        manifest = json.loads(zf.read("manifest.json"))
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError):
        return {"ok": False, "error": "corrupt manifest.json"}

    if not isinstance(manifest, dict) or type(manifest.get("schema_version", 0)) is not int:
        return {"ok": False, "error": "invalid manifest.json: expected an object with an integer schema_version"}
    if manifest.get("schema_version", 0) > SCHEMA_VERSION:
        return {"ok": False, "error": f"unsupported archive schema_version: {manifest.get('schema_version')}. Update zerocollab to import this archive."}

    archive_info = {
        "archive_id": manifest.get("archive_id", ""),
        "schema_version": manifest.get("schema_version"),
        "created_at": manifest.get("created_at", ""),
    }

    report = {
        "ok": True,
        "mode": "merge",
        "archive": archive_info,
        "sections": {},
        "channels": {"created": [], "remapped": [], "skipped": []},
        "warnings": warnings,
    }

    # Parsed records are retained for the short apply phase. The archive input
    # is capped, but Python objects cost more than their serialized bytes.
    records = []
    skipped = 0
    if "messages.jsonl" in zf.namelist():
        for line in _message_lines(zf):
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not _valid_message(msg):
                skipped += 1
                continue
            msg_uid = msg.get("uid") or "fp-" + _fingerprint(msg)
            records.append((msg, msg_uid))

    if skipped:
        warnings.append(f"skipped {skipped} invalid message record(s)")

    # Archives from older versions may carry sections for features that no
    # longer exist; they are reported and skipped rather than failing the import.
    legacy = [n for n in ("jobs.json", "rules.json", "summaries.json") if n in zf.namelist()]
    if legacy:
        warnings.append("skipped sections for removed features: " + ", ".join(legacy))

    return report, records, skipped


def _do_import(zip_bytes, store, channel_list, max_channels):
    return apply_import(_prepare_import(zip_bytes), store, channel_list, max_channels)


def apply_import(prepared, store, channel_list, max_channels=8):
    """Apply prepared data without yielding; caller holds import admission."""
    if isinstance(prepared, dict):
        return prepared
    report, records, skipped = prepared
    # Collect existing UIDs for dedup
    existing_msg_uids = set()
    for m in store.get_recent(count=999_999_999):
        existing_msg_uids.add(_ensure_uid(m))

    # Helper: resolve channel
    def resolve_channel(ch: str) -> str:
        if not ch:
            return "general"
        if ch in channel_list:
            return ch
        # Try to auto-create
        if len(channel_list) < max_channels:
            if re.match(r'^[a-z0-9][a-z0-9-]{0,29}$', ch):
                channel_list.append(ch)
                report["channels"]["created"].append(ch)
                return ch
        # Remap to general
        report["channels"]["remapped"].append({"from": ch, "to": "general", "reason": "invalid_or_full"})
        return "general"

    # --- Import messages (preserve archive uid, timestamp, time, reply links) ---
    msg_report = {"created": 0, "duplicates": 0, "conflicts": 0, "skipped": skipped}
    # Two-pass: first import all messages, then rebuild reply_to links
    pending_replies = []  # (new_local_id, reply_to_uid)
    if records:
        with store.import_batch():
            for msg, msg_uid in records:
                if msg_uid in existing_msg_uids:
                    msg_report["duplicates"] += 1
                    continue
                channel = resolve_channel(msg.get("channel", "general"))
                new_msg = store.add(
                    sender=msg.get("sender", "unknown"),
                    text=msg.get("text", ""),
                    msg_type=msg.get("type", "chat"),
                    attachments=msg.get("attachments"),
                    channel=channel,
                    metadata=msg.get("metadata"),
                    uid=msg_uid,
                    timestamp=msg.get("timestamp"),
                    time_str=msg.get("time"),
                    _bulk=True,
                )
                # Track reply links for second pass
                reply_to_uid = msg.get("reply_to_uid")
                if reply_to_uid:
                    pending_replies.append((new_msg["id"], reply_to_uid))
                existing_msg_uids.add(msg_uid)
                msg_report["created"] += 1
            if pending_replies:
                store.resolve_import_replies(pending_replies)
    report["sections"]["messages"] = msg_report

    return report
