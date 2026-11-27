"""Homologação offline da entrega única ao catálogo; sem tocar SQLite real."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from replay_agent.legacy_rotation_handoff import prepare_rotation_handoff


def state(angle, version=3):
    return {"activeVersion": version, "snapshot": {"courts": [
        {"key": "QUADRA_02", "active": True, "camera": {"rotationDeg": angle}}
    ]}}


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="requer FFmpeg")
def test_handoff_keeps_identity_and_pinned_rotation(tmp_path):
    source = tmp_path / "source.mp4"
    root = tmp_path / "variants"
    root.mkdir()
    subprocess.run([
        "ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi",
        "-i", "testsrc2=size=320x240:rate=15:duration=1",
        "-c:v", "mpeg4", "-y", str(source)
    ], check=True, capture_output=True, timeout=60)
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    first = prepare_rotation_handoff(source, root, court_key="QUADRA_02",
                                     local_id="replay-20261122-224209", applied_state=state(90))
    assert (first["configVersion"], first["rotationAppliedDeg"],
            first["width"], first["height"]) == (3, 90, 240, 320)
    assert first["rotationDeg"] == 0
    assert Path(first["path"]).is_file()
    assert json.loads((Path(first["path"]).parent / "receipt.json").read_text())["localId"] == first["localId"]
    # Repetição durante nova versão: não aplica outra rotação nem duplica.
    repeated = prepare_rotation_handoff(source, root, court_key="QUADRA_02",
                                        local_id="replay-20261122-224209", applied_state=state(180, 4))
    assert repeated == first
    assert len(list(root.glob("*/player.mp4"))) == 1
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original


def test_invalid_or_pending_revision_never_prepares_variant(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"placeholder")
    root = tmp_path / "variants"
    root.mkdir()
    with pytest.raises(ValueError, match="ainda não aplicada"):
        prepare_rotation_handoff(source, root, court_key="QUADRA_02",
                                 local_id="replay-20261122-224209", applied_state=state(90, 0))
    assert not list(root.glob("*/player.mp4"))
    with pytest.raises(ValueError, match="local_id inválido"):
        prepare_rotation_handoff(source, root, court_key="QUADRA_02",
                                 local_id="../x", applied_state=state(90))
