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


def test_legacy_remote_retries_confirmation_and_rolls_back(tmp_path):
    from copy import deepcopy
    from types import SimpleNamespace

    from replay_agent.client import CentralUnavailable
    from replay_agent.legacy_publish import load_offline_remote
    from replay_agent.legacy_remote import LegacyRemote

    remote_dir = tmp_path / "agent"
    first = court(tmp_path, "01", "horizontal", (320, 240), 0)
    second = court(tmp_path, "02", "vertical", (240, 320), 90)
    asset_data = {p.stem: p.read_bytes() for p in tmp_path.glob("*.png")}

    def revision(version, configure=lambda courts: None):
        courts = deepcopy([first, second])
        configure(courts)
        return {"version": version, "snapshot": {"schema": 1, "courts": courts},
                "secrets": {"camera": {"password": "senha-sintetica"}}, "allowRestart": True,
                "assets": [{"versionId": key, "url": f"/remote/assets/{key}", "bytes": len(data),
                            "sha256": hashlib.sha256(data).hexdigest()} for key, data in asset_data.items()]}

    class Central:
        rev = None
        lose_answer = False
        calls = []

        def request_raw(self, method, path, *, timeout):
            assert method == "GET" and path.startswith("/remote/assets/")
            return SimpleNamespace(content=asset_data[path.rsplit("/", 1)[-1]], headers={})

        def call(self, method, path, body=None):
            self.calls.append((method, path))
            if method == "GET":
                assert path == "/remote/pending"
                return self.rev
            if not path.endswith("/result"):
                return {"ok": True}
            assert (remote_dir / "remote" / "legacy-result.json").is_file()
            if self.lose_answer:
                self.lose_answer = False
                raise CentralUnavailable("resposta perdida")
            return {"status": body["status"]}

    central = Central()
    central.rev = revision(3)
    central.lose_answer = True
    adapter = LegacyRemote(central, remote_dir, {"01", "02"})
    with pytest.raises(CentralUnavailable):
        adapter.poll()
    assert load_offline_remote(remote_dir).active_version == 0

    adapter = LegacyRemote(central, remote_dir, {"01", "02"})
    assert adapter.tick(None)["status"] == "aplicado"
    assert central.calls.count(("GET", "/remote/pending")) == 1
    assert not adapter.outbox.exists()
    assert "senha-sintetica" not in adapter.state_path.read_text()
    reader = load_offline_remote(remote_dir)
    assert replay_profile(reader, "01")[:4] == (3, 1.25, 0.75, 0)
    assert replay_profile(reader, "02")[:4] == (3, 1.25, 0.75, 90)

    central.rev = revision(4, lambda c: c[1]["camera"].update(rotationDeg=17))
    rejected = adapter.poll()
    assert rejected["status"] == "falha" and rejected["activeVersion"] == 3
    assert load_offline_remote(remote_dir).active_version == 3

    central.rev = revision(5, lambda c: c[1]["camera"].update(rotationDeg=270))
    assert adapter.poll()["status"] == "aplicado"
    assert replay_profile(adapter, "01")[3] == 0
    assert replay_profile(adapter, "02")[3] == 270
    assert adapter.rollback() is True
    assert replay_profile(load_offline_remote(remote_dir), "02")[:4] == (3, 1.25, 0.75, 90)
    assert adapter.poll()["status"] == "falha"
