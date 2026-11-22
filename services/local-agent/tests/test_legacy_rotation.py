"""Testes offline do ajuste simples de orientação; não acessam câmeras ou central."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from replay_agent.legacy_rotation import rotate_replay, rotation_for_court


@pytest.fixture
def applied_state():
    return {"activeVersion": 3, "snapshot": {"courts": [
        {"key": "QUADRA_01", "active": True, "camera": {"rotationDeg": 0}},
        {"key": "QUADRA_02", "active": True, "camera": {"rotationDeg": 90}},
    ]}}


def test_rotation_comes_only_from_applied_state(applied_state):
    assert rotation_for_court(applied_state, "QUADRA_01") == (3, 0)
    assert rotation_for_court(applied_state, "QUADRA_02") == (3, 90)
    with pytest.raises(ValueError, match="ainda não aplicada"):
        rotation_for_court({"activeVersion": 0, "snapshot": applied_state["snapshot"]}, "QUADRA_02")
    applied_state["snapshot"]["courts"][1]["camera"]["rotationDeg"] = 45
    with pytest.raises(ValueError, match="inválida"):
        rotation_for_court(applied_state, "QUADRA_02")


@pytest.mark.parametrize("angle,dimensions", [
    (0, (320, 240)), (90, (240, 320)),
    (180, (320, 240)), (270, (240, 320)),
])
def test_rotation_preserves_original_and_changes_dimensions(tmp_path, angle, dimensions):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg e ffprobe são necessários")
    source = tmp_path / "replay-original.mp4"
    destination = tmp_path / "variante.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
         "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15:duration=1",
         "-c:v", "mpeg4", "-y", str(source)],
        check=True, capture_output=True, timeout=60
    )
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    result = rotate_replay(source, destination, angle)
    assert (result["width"], result["height"]) == dimensions
    assert destination.stat().st_size > 0
    assert hashlib.sha256(source.read_bytes()).hexdigest() == checksum
    with pytest.raises(FileExistsError):
        rotate_replay(source, destination, angle)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == checksum


def test_invalid_angle_does_not_create_output(tmp_path):
    with pytest.raises(ValueError, match="0, 90, 180 ou 270"):
        rotate_replay(tmp_path / "not-found.mp4", tmp_path / "out.mp4", 45)
    assert not (tmp_path / "out.mp4").exists()
