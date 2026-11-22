"""Testes unitários offline: nenhuma câmera, servidor ou rede é acessado."""
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "local-agent"))

from replay_agent.thumbnail import extract_thumbnail, ThumbnailUnavailable
from replay_agent.store import LocalStore


class ThumbnailTests(unittest.TestCase):
    def test_jpeg_is_generated_without_touching_mp4(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "replay.mp4"
            path.write_bytes(b"mock-original-mp4")
            jpeg = bytes.fromhex("FFD8FF") + b"x" * 96 + bytes.fromhex("FFD9")
            with patch("replay_agent.thumbnail.subprocess.run",
                       return_value=SimpleNamespace(returncode=0, stdout=jpeg)) as runner:
                self.assertEqual(extract_thumbnail(str(path)), jpeg)
                argv = runner.call_args.args[0]
                self.assertIn("-noautorotate", argv)
                self.assertIn("-frames:v", argv)
                self.assertEqual(path.read_bytes(), b"mock-original-mp4")

    def test_missing_file_does_not_call_ffmpeg(self):
        with patch("replay_agent.thumbnail.subprocess.run") as runner:
            with self.assertRaises(ThumbnailUnavailable):
                extract_thumbnail("/not/found.mp4")
            runner.assert_not_called()

    def test_store_thumbnail_queue_is_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            mp4 = Path(directory) / "clip.mp4"
            mp4.write_bytes(b"mock")
            sqlite_path = Path(directory) / "local.sqlite"
            store = LocalStore(str(sqlite_path))
            try:
                replay_id = "abc123456"
                store.add_replay(replay_id, "quadra01", datetime.now(timezone.utc), str(mp4),
                                 4, 3.5, 1920, 1080, False, "h264")
                self.assertEqual(len(store.pending_thumbnails()), 0)
                store.mark(replay_id, "synced")
                self.assertEqual(len(store.pending_thumbnails()), 1)
                store.mark_thumbnail(replay_id, "synced")
                self.assertEqual(len(store.pending_thumbnails()), 0)
                reopened = LocalStore(str(sqlite_path))
                try:
                    self.assertEqual(len(reopened.pending_thumbnails()), 0)
                finally:
                    reopened.close()
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
