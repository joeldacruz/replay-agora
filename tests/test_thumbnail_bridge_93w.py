"""Tests isolados da ponte JPEG: sem rede, FFmpeg, arena ou API real."""
from pathlib import Path
import importlib.util
import sqlite3
import hashlib
import tempfile
import unittest
from unittest.mock import Mock

MODULE = Path(__file__).resolve().parents[1] / "services/arena-adapter/thumbnail_bridge.py"
SPEC = importlib.util.spec_from_file_location("thumbnail_bridge", MODULE)
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class ThumbnailBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / "replay.mp4"
        self.file.write_bytes(b"M" * 256)
        self.catalog = self.root / "catalogo.sqlite"
        with sqlite3.connect(self.catalog) as db:
            db.executescript(
                "CREATE TABLE replays(local_id TEXT, court_key TEXT, path TEXT, "
                "bytes INTEGER, sync_state TEXT, deleted INTEGER, "
                "file_ok INTEGER, captured_at TEXT);"
            )
            db.execute(
                "INSERT INTO replays VALUES(?,?,?,?,?,?,?,?)",
                ("quadra01.abcdef123", "QUADRA_01", str(self.file), 256,
                 "synced", 0, 1, "2026-11-21T12:00:00Z")
            )
        self.jpeg = bytes.fromhex("FFD8FF") + b"A" * 80 + bytes.fromhex("FFD9")

    def test_catalog_query_does_not_change_file(self):
        before = hashlib.sha256(self.catalog.read_bytes()).hexdigest()
        rows = bridge.eligible_rows(self.catalog)
        after = hashlib.sha256(self.catalog.read_bytes()).hexdigest()
        self.assertEqual(len(rows), 1)
        self.assertEqual(before, after)
        self.assertEqual(rows[0]["local_id"], "quadra01.abcdef123")

    def test_binary_upload_uses_existing_client(self):
        row = bridge.eligible_rows(self.catalog)[0]
        client = Mock()
        client.request_raw.return_value.json.return_value = {
            "ok": True, "status": "created"
        }
        status = bridge.send_thumbnail(
            client, row, lambda _: self.jpeg, (self.root,)
        )
        self.assertEqual(status, "created")
        args, kwargs = client.request_raw.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(kwargs["content"], self.jpeg)
        self.assertEqual(kwargs["headers"]["X-Thumb-Sha256"],
                         hashlib.sha256(self.jpeg).hexdigest())
        self.assertEqual(self.file.read_bytes(), b"M" * 256)

    def test_outside_storage_is_blocked(self):
        row = bridge.eligible_rows(self.catalog)[0]
        client = Mock()
        with self.assertRaises(ValueError):
            bridge.send_thumbnail(client, row, lambda _: self.jpeg,
                                  (self.root / "outra-arena",))
        client.request_raw.assert_not_called()

    def test_nonexistent_allowed_root_is_rejected_before_upload(self):
        row = bridge.eligible_rows(self.catalog)[0]
        client = Mock()
        missing = self.root / "pasta-ainda-nao-criada"
        self.assertFalse(missing.exists())
        with self.assertRaisesRegex(ValueError, "raizes autorizadas"):
            bridge.send_thumbnail(client, row, lambda _: self.jpeg, (missing,))
        self.assertFalse(missing.exists())
        client.request_raw.assert_not_called()

    def test_existing_outside_root_is_rejected(self):
        row = bridge.eligible_rows(self.catalog)[0]
        client = Mock()
        other = self.root / "outra-arena"
        other.mkdir()
        with self.assertRaisesRegex(ValueError, "raizes autorizadas"):
            bridge.send_thumbnail(client, row, lambda _: self.jpeg, (other,))
        client.request_raw.assert_not_called()

    def test_only_synced_records(self):
        with sqlite3.connect(self.catalog) as db:
            db.execute("UPDATE replays SET sync_state='pending'")
        self.assertEqual(bridge.eligible_rows(self.catalog), [])


if __name__ == "__main__":
    unittest.main()
