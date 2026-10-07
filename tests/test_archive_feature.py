import asyncio
import io
import json
import sys
import tempfile
import threading
import unittest
import zipfile
from unittest.mock import AsyncMock, patch
from pathlib import Path

from fastapi import UploadFile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app
import archive
from store import MessageStore


def make_store(root: Path):
    return MessageStore(str(root / "messages.jsonl"))


def make_archive(manifest, records=()):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', zipfile.ZIP_STORED) as zf:
        zf.writestr('manifest.json', json.dumps(manifest))
        zf.writestr('messages.jsonl', ''.join(json.dumps(row) + '\n' for row in records))
    return data.getvalue()


def seed_history(store: MessageStore):
    root = store.add(
        "user",
        "Need a tighter review loop.",
        channel="planning",
        uid="msg-root",
        timestamp=100.0,
        time_str="00:01:40",
    )
    store.add(
        "codex",
        "Start with archive round-trip coverage.",
        channel="planning",
        reply_to=root["id"],
        uid="msg-reply",
        timestamp=101.0,
        time_str="00:01:41",
        metadata={"source": "test"},
    )


class ArchiveRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source_store = make_store(self.root / "source")
        self.target_store = make_store(self.root / "target")

    def test_preparation_finishes_before_store_transaction(self):
        seed_history(self.source_store)
        blob = archive.build_export(self.source_store)
        original = archive._message_lines
        parsing = False
        def lines(zf):
            nonlocal parsing
            parsing = True
            try:
                yield from original(zf)
            finally:
                parsing = False
        original_batch = self.target_store.import_batch
        def batch():
            self.assertFalse(parsing)
            return original_batch()
        with patch('archive._message_lines', side_effect=lines), patch.object(
            self.target_store, 'import_batch', side_effect=batch
        ):
            report = archive.import_archive(blob, self.target_store, ['general'])
        self.assertEqual(report['sections']['messages']['created'], 2)

    def test_failed_import_commit_does_not_publish_staged_messages(self):
        seed_history(self.source_store)
        self.target_store.add('user', 'existing', uid='existing')
        before = self.target_store.get_recent()
        disk = self.target_store._path.read_bytes()
        blob = archive.build_export(self.source_store)
        with patch('store.atomic_write', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                archive.import_archive(blob, self.target_store, ['general'])
        self.assertEqual(self.target_store.get_recent(), before)
        self.assertEqual(self.target_store._path.read_bytes(), disk)

    def test_reply_resolution_keeps_published_dictionary_unchanged(self):
        parent = self.target_store.add('user', 'parent', uid='parent')
        reply = self.target_store.add('user', 'reply', uid='reply')
        with self.target_store.import_batch():
            self.target_store.resolve_import_replies([(reply['id'], 'parent')])
        self.assertNotIn('reply_to', reply)
        self.assertEqual(self.target_store.get_by_id(reply['id'])['reply_to'], parent['id'])

    def test_corrupt_messages_crc_fails_before_any_additions(self):
        data = io.BytesIO()
        payload = json.dumps({'sender': 'user', 'text': 'unique-corruption-marker', 'uid': 'x'}).encode() + b'\n'
        with zipfile.ZipFile(data, 'w', zipfile.ZIP_STORED) as zf:
            zf.writestr('manifest.json', json.dumps({'schema_version': 1}))
            zf.writestr('messages.jsonl', payload)
        corrupt = data.getvalue().replace(b'unique-corruption-marker', b'UNIQUE-corruption-marker', 1)
        report = archive.import_archive(corrupt, self.target_store, ['general'])
        self.assertFalse(report['ok'])
        self.assertIn('corrupt zip', report['error'])
        self.assertEqual(self.target_store.get_recent(), [])

    def test_invalid_rows_are_skipped_without_breaking_valid_merge(self):
        malformed = [None, [], 'text', 42, {'uid': ['bad']},
                     {'sender': {}}, {'text': 5}, {'channel': ['bad']},
                     {'reply_to_uid': {}}, {'timestamp': float('nan')},
                     {'timestamp': True}, {'attachments': ['bad']}, {'metadata': []}]
        valid = {'sender': 'user', 'text': 'valid', 'channel': 'planning', 'uid': 'valid'}
        blob = make_archive({'schema_version': 1}, [valid, *malformed, valid])
        channels = ['general']
        report = archive.import_archive(blob, self.target_store, channels)
        self.assertEqual(report['sections']['messages'],
                         {'created': 1, 'duplicates': 1, 'conflicts': 0, 'skipped': len(malformed)})
        self.assertEqual(len(report['warnings']), 1)
        self.assertEqual(channels, ['general', 'planning'])
        self.assertEqual(self.target_store.get_recent()[0]['text'], 'valid')

    def test_round_trip_preserves_identity_and_dedup(self):
        seed_history(self.source_store)

        blob = archive.build_export(self.source_store, app_version="test")

        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            self.assertEqual(set(zf.namelist()), {"manifest.json", "messages.jsonl"})
            manifest = json.loads(zf.read("manifest.json"))

        self.assertEqual(manifest["schema_version"], archive.SCHEMA_VERSION)
        self.assertEqual(manifest["counts"], {"messages": 2})

        channel_list = ["general"]
        report = archive.import_archive(blob, self.target_store, channel_list, max_channels=8)

        self.assertTrue(report["ok"])
        self.assertEqual(report["sections"]["messages"]["created"], 2)
        self.assertIn("planning", channel_list)
        self.assertIn("planning", report["channels"]["created"])

        imported_messages = self.target_store.get_recent(10)
        self.assertEqual(imported_messages[0]["uid"], "msg-root")
        self.assertEqual(imported_messages[0]["timestamp"], 100.0)
        self.assertEqual(imported_messages[1]["uid"], "msg-reply")
        self.assertEqual(imported_messages[1]["reply_to"], imported_messages[0]["id"])
        self.assertEqual(imported_messages[1]["metadata"]["source"], "test")

        second_report = archive.import_archive(blob, self.target_store, channel_list, max_channels=8)
        self.assertTrue(second_report["ok"])
        self.assertEqual(second_report["sections"]["messages"]["duplicates"], 2)

    def test_legacy_sections_are_skipped_with_a_warning(self):
        seed_history(self.source_store)
        blob = archive.build_export(self.source_store, app_version="test")
        # Older exports also carried jobs, rules and summaries.
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(blob)) as src, zipfile.ZipFile(buf, "w") as dst:
            for name in src.namelist():
                dst.writestr(name, src.read(name))
            dst.writestr("jobs.json", "[]")
            dst.writestr("rules.json", "[]")

        report = archive.import_archive(buf.getvalue(), self.target_store, ["general"], max_channels=8)

        self.assertTrue(report["ok"])
        self.assertEqual(report["sections"]["messages"]["created"], 2)
        self.assertEqual(set(report["sections"]), {"messages"})
        self.assertTrue(any("jobs.json" in w and "rules.json" in w for w in report["warnings"]))

    def test_rejects_newer_schema_version(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "schema_version": archive.SCHEMA_VERSION + 1,
                        "archive_id": "future-archive",
                        "created_at": "2099-01-01T00:00:00Z",
                    }
                ),
            )

        report = archive.import_archive(buf.getvalue(), self.target_store, ["general"], max_channels=8)

        self.assertFalse(report["ok"])
        self.assertIn("unsupported archive schema_version", report["error"])


class ImportThreadTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_preparation_keeps_admission_and_never_applies(self):
        started, release = threading.Event(), threading.Event()
        def prepare(data):
            started.set()
            release.wait(3)
            return {"ok": False}
        async def consume():
            async with archive.prepared_import(b'zip'):
                self.fail('cancelled request must not apply')
        with patch('archive._prepare_import', side_effect=prepare):
            task = asyncio.create_task(consume())
            try:
                async with asyncio.timeout(2):
                    while not started.is_set():
                        await asyncio.sleep(.01)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                async with archive.prepared_import(b'other') as report:
                    self.assertEqual(report['error'], 'import already running')
            finally:
                release.set()
                async with asyncio.timeout(2):
                    while archive._import_lock.locked():
                        await asyncio.sleep(.01)

    async def test_import_applies_to_channels_changed_during_preparation(self):
        started, release = threading.Event(), threading.Event()
        original = archive._prepare_import
        loop_thread = threading.get_ident()
        def prepare(data):
            self.assertNotEqual(threading.get_ident(), loop_thread)
            started.set()
            if not release.wait(3):
                raise RuntimeError('worker not released')
            return original(data)
        with tempfile.TemporaryDirectory() as root:
            source = make_store(Path(root) / 'source')
            target = make_store(Path(root) / 'target')
            seed_history(source)
            upload = UploadFile(file=io.BytesIO(archive.build_export(source)), filename='history.zip')
            settings = {'channels': ['general'], 'max_channels': 8}
            with patch('archive._prepare_import', side_effect=prepare), patch.object(app, 'store', target), patch.object(app, 'room_settings', settings), patch.object(app, '_save_settings'), patch.object(app, 'broadcast_settings', new_callable=AsyncMock), patch.object(app, '_broadcast', new_callable=AsyncMock):
                task = asyncio.create_task(app.import_history(upload))
                try:
                    async with asyncio.timeout(2):
                        while not started.is_set():
                            await asyncio.sleep(.01)
                    settings['channels'].append('concurrent')
                finally:
                    release.set()
                response = await task
                self.assertEqual(response.status_code, 200)
                self.assertEqual(settings['channels'], ['general', 'concurrent', 'planning'])
                self.assertEqual(len(target.get_recent()), 2)


class ExportThreadTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_export_keeps_worker_slot_until_complete(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        def build(*args, **kwargs):
            started.set()
            try:
                if not release.wait(3):
                    raise RuntimeError('test worker not released')
                return b'archive'
            finally:
                finished.set()
        with patch('archive._build_export', side_effect=build):
            task = asyncio.create_task(app.export_history())
            try:
                async with asyncio.timeout(2):
                    while not started.is_set():
                        await asyncio.sleep(.01)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                response = await app.export_history()
                self.assertEqual(response.status_code, 409)
            finally:
                release.set()
                await asyncio.to_thread(finished.wait, 3)
        # Wait for the worker's finally to release the admission lock.
        async with asyncio.timeout(2):
            while archive._export_lock.locked():
                await asyncio.sleep(.01)

    async def test_export_builder_runs_off_loop(self):
        loop_thread = threading.get_ident()
        def build(*args, **kwargs):
            self.assertNotEqual(threading.get_ident(), loop_thread)
            return b'archive'
        with patch('archive.build_export', side_effect=build):
            response = await app.export_history()
        self.assertEqual(response.body, b'archive')


class ImportExportApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = make_store(self.root / "appdata")
        # Don't seed — import target should be empty so imported records are new

        self._saved = {
            "store": getattr(app, "store", None),
            "config": getattr(app, "config", None),
            "room_settings": dict(getattr(app, "room_settings", {})),
        }

        app.store = self.store
        app.config = {"server": {"data_dir": str(self.root / "appdata"), "version": "test"}}
        app.room_settings = {
            "title": "zerocollab",
            "username": "user",
            "font": "sans",
            "channels": ["general"],
            "history_limit": "all",
            "contrast": "normal",
        }

        def restore():
            app.store = self._saved["store"]
            app.config = self._saved["config"]
            app.room_settings = self._saved["room_settings"]

        self.addCleanup(restore)

    def test_export_endpoint_returns_zip_with_manifest(self):
        seed_history(self.store)
        response = asyncio.run(app.export_history())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "application/zip")
        self.assertIn("attachment; filename=", response.headers["Content-Disposition"])

        with zipfile.ZipFile(io.BytesIO(response.body)) as zf:
            manifest = json.loads(zf.read("manifest.json"))

        self.assertEqual(manifest["counts"]["messages"], 2)

    def test_import_endpoint_merges_archive_and_updates_channels(self):
        source_store = make_store(self.root / "source")
        seed_history(source_store)
        blob = archive.build_export(source_store, app_version="test")

        upload = UploadFile(filename="history.zip", file=io.BytesIO(blob))
        response = asyncio.run(app.import_history(upload))

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body.decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertIn("planning", app.room_settings["channels"])
        self.assertEqual(payload["sections"]["messages"]["created"], 2)

    def test_import_endpoint_rejects_wrong_extension(self):
        upload = UploadFile(filename="history.txt", file=io.BytesIO(b"nope"))

        response = asyncio.run(app.import_history(upload))

        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.body.decode("utf-8"))
        self.assertIn("expected .zip", payload["error"])

    def test_invalid_archives_return_400_without_mutation_and_release_admission(self):
        valid = {'sender': 'user', 'text': 'crc-marker', 'uid': 'valid', 'channel': 'new-channel'}
        corrupt = make_archive({'schema_version': 1}, [valid]).replace(b'crc-marker', b'CRC-marker', 1)
        invalid_encoding = io.BytesIO()
        with zipfile.ZipFile(invalid_encoding, 'w') as zf:
            zf.writestr('manifest.json', b'\xff')
        blobs = [make_archive(m, [valid]) for m in
                 ([], None, 'manifest', {'schema_version': '1'}, {'schema_version': None}, {'schema_version': True})]
        blobs.extend([corrupt, invalid_encoding.getvalue()])
        for blob in blobs:
            with self.subTest(blob=blob[:30]), patch.object(app, '_save_settings') as save, patch.object(app, '_broadcast', new_callable=AsyncMock) as broadcast:
                response = asyncio.run(app.import_history(UploadFile(filename='history.zip', file=io.BytesIO(blob))))
                self.assertEqual(response.status_code, 400)
                self.assertTrue(json.loads(response.body)['error'])
                self.assertEqual(self.store.get_recent(), [])
                self.assertEqual(app.room_settings['channels'], ['general'])
                self.assertFalse(archive._import_lock.locked())
                save.assert_not_called()
                broadcast.assert_not_called()
        # A corrected upload succeeds immediately after the failed requests.
        response = asyncio.run(app.import_history(UploadFile(filename='history.zip', file=io.BytesIO(make_archive({'schema_version': 1}, [valid])))))
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
