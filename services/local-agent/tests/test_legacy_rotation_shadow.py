"""Ensaio do scanner com mídia e catálogo descartáveis."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from replay_agent.legacy_rotation_shadow import scan, STABLE_NS


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "requer FFmpeg")
class ShadowTest(unittest.TestCase):
    def test_only_new_court_two_original_is_rotated_once(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            one, two, output = (base / name for name in ("quadra01", "quadra02", "variants"))
            for root in (one, two, output):
                root.mkdir()
            old = two / "replay-old.mp4"
            old.write_bytes(b"antigo")
            other = one / "replay-shadow.mp4"
            other.write_bytes(b"outra quadra")
            since = time.time_ns()
            video = two / "replay-shadow.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                "testsrc2=size=320x240:rate=25:duration=2", "-c:v", "libx264",
                "-preset", "ultrafast", "-n", str(video)
            ], check=True, timeout=60)
            db = base / "catalogo.sqlite"
            local_id = "4d7b79da-34df-5ab1-a423-6d7c2fc443fb"
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE replays (local_id TEXT PRIMARY KEY, path TEXT)")
                conn.execute("INSERT INTO replays VALUES (?, ?)", (local_id, str(video)))
            originals = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (old, other, video, db)}
            state = base / "state.json"
            config = {"activeVersion": 0, "snapshot": {"courts": [
                {"key": "QUADRA_02", "active": True, "camera": {"rotationDeg": 90}}
            ]}}
            state.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ainda não aplicada"):
                scan(two, output, state, since)
            config["activeVersion"] = 1
            state.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "isolada"):
                scan(two, two, state, since)
            self.assertEqual(scan(two, output, state, since), 0)
            self.assertEqual(list(output.iterdir()), [])
            time.sleep(STABLE_NS / 1e9)
            self.assertEqual(scan(two, output, state, since), 0)
            [variant] = output.glob("*/player.mp4")
            receipt = json.loads(variant.with_name("receipt.json").read_text())
            self.assertEqual(receipt["localId"], local_id)
            self.assertEqual((receipt["width"], receipt["height"], receipt["rotationAppliedDeg"]),
                             (240, 320, 90))
            probe = subprocess.check_output([
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(variant)
            ], text=True, timeout=30)
            self.assertEqual(float(probe), 2.0)
            subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(variant),
                            "-f", "null", "-"], check=True, timeout=60)
            first_stat = variant.stat()
            config["activeVersion"] = 2
            config["snapshot"]["courts"][0]["camera"]["rotationDeg"] = 180
            state.write_text(json.dumps(config), encoding="utf-8")
            self.assertEqual(scan(two, output, state, since), 0)
            self.assertEqual(list(output.glob("*/player.mp4")), [variant])
            self.assertEqual(variant.stat().st_mtime_ns, first_stat.st_mtime_ns)
            for path, digest in originals.items():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
