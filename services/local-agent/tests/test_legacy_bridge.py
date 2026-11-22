"""Exercita a ponte com MP4/PNG gerados no diretório temporário do pytest."""

import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import threading
import zlib

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from replay_agent.legacy_bridge import render_legacy_replay, replay_profile
from replay_agent.remote import RemoteManager
from replay_agent.renderjobs import RenderError

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="requer FFmpeg e ffprobe"
)


def ffmpeg(*args):
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-y", "-filter_threads", "1", *args],
        check=True, capture_output=True, timeout=60,
    ).stdout


def court(tmp_path, key, orientation, size, rotation):
    w, h = size
    version = f"{int(key):032x}"
    png = tmp_path / f"{version}.png"
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    blue = bytes((0, 0, 255, 255))
    transparent = bytes(4)
    rows = b"".join(
        b"\0" + (blue * w if y < 16 or y >= h - 16 else blue * 16 + transparent * (w - 32) + blue * 16)
        for y in range(h)
    )
    png.write_bytes(b"\x89PNG\r\n\x1a\n" +
                    chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) +
                    chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    frame = {"versionId": version, "sha256": hashlib.sha256(png.read_bytes()).hexdigest(),
             "width": w, "height": h, "out": f"{w}x{h}",
             "window": {"x": 16, "y": 16, "w": w - 32, "h": h - 32},
             "layout": {"frameScale": 0.9, "dx": 4, "background": "black"}}
    return {"key": key, "active": True, "camera": {"rotationDeg": rotation},
            "replay": {"preS": 1.25, "postS": 0.75, "formats": [orientation]},
            "frames": {"enabled": True, orientation: frame}}


@pytest.mark.parametrize(("size", "output", "orientation", "rotation", "quadrant"), [
    ((320, 240), (320, 240), "horizontal", 0, (0, 0)),
    ((320, 240), (240, 320), "vertical", 90, (1, 0)),
    ((240, 320), (240, 320), "vertical", 180, (1, 1)),
    ((240, 320), (320, 240), "horizontal", 270, (0, 1)),
])
def test_sample(tmp_path, size, output, orientation, rotation, quadrant):
    remote = RemoteManager.__new__(RemoteManager)
    remote.lock = threading.Lock()
    remote.frames_dir = tmp_path
    first = court(tmp_path, "01", orientation, output, rotation)
    other = court(tmp_path, "02", "horizontal", (320, 240), 17)
    remote.state = {"activeVersion": 3, "snapshot": {"courts": [first, other]}}
    destination = tmp_path / "ready"
    src = tmp_path / "raw.mp4"
    w, h = size
    ffmpeg("-f", "lavfi", "-i", f"color=white:s={w}x{h}:d=2,drawbox=x=0:y=0:w=iw/2:h=ih/2:t=fill:color=red",
           "-c:v", "libx264", str(src))
    original = hashlib.sha256(src.read_bytes()).digest()
    with pytest.raises(ValueError, match="rotação inválida"):
        render_legacy_replay(remote, "02", src, destination)
    assert not destination.exists()

    profile = replay_profile(remote, "01")
    assert profile[1:3] == (1.25, 0.75)
    other["camera"]["rotationDeg"] = 180
    other["replay"]["postS"] = 1.75
    with pytest.raises(RenderError, match="fora da janela"):
        render_legacy_replay(remote, "02", src, destination)
    assert not destination.exists()
    remote.state = {"activeVersion": 4, "snapshot": {"courts": [other]}}
    manifest = render_legacy_replay(remote, "01", src, destination, profile=profile)
    assert manifest == json.loads((destination / "manifest.json").read_text())
    assert manifest["configVersion"] == 3
    assert manifest["rotationDeg"] == 0
    variant = manifest["variants"][manifest["playerFormat"]]
    assert (variant["width"], variant["height"]) == output
    assert variant["durationS"] == pytest.approx(2.0, abs=0.1)
    assert hashlib.sha256(src.read_bytes()).digest() == original
    assert (destination / "thumbnail.jpg").read_bytes().startswith(b"\xff\xd8")

    video = destination / variant["path"]
    rgb = ffmpeg("-i", str(video), "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1")
    w, h = output

    def pixel(x, y):
        index = (y * w + x) * 3
        return rgb[index:index + 3]

    red = pixel(w * (1 + 2 * quadrant[0]) // 4, h * (1 + 2 * quadrant[1]) // 4)
    assert red[0] > 160 and max(red[1:]) < 80
    # A escala de 0,9 e o deslocamento de +4 mantêm a borda esquerda em x > 0.
    assert max(pixel(0, h // 2)) < 40
    border = pixel(round(w * 0.05) + 8, h // 2)
    assert border[2] > 160 and max(border[:2]) < 80
