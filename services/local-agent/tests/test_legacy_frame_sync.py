"""Testes offline de recepção das molduras sem alterar o motor da arena."""
import hashlib
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import zlib

import pytest

from replay_agent.legacy_frame_sync import fetch_manifest, receive_frames


def png(width=320, height=240):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    rows = (b"\x00" + b"\x00" * (width * 4)) * height
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def specimen(data):
    return {"versionId": "a" * 32, "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "width": 320, "height": 240,
            "out": "1920x1080", "window": {"x": 15, "y": 15, "w": 290, "h": 210},
            "layout": {"frameFit": "contain", "videoFit": "cover"}}


class FakeCentral:
    def __init__(self, data):
        self.data = data
        self.calls = []
        self.manifest = {"courtKey": "QUADRA_02", "serverId": "srv", "arenaId": "arena",
                         "configurationState": "desired_unapplied", "formats": ["horizontal"],
                         "framesEnabled": True,
                         "frames": {"horizontal": specimen(data), "vertical": None}}

    def call(self, method, path):
        self.calls.append((method, path))
        assert (method, path) == ("GET", "/remote/frames/QUADRA_02")
        return self.manifest

    def request_raw(self, method, path, timeout):
        self.calls.append((method, path))
        assert method == "GET" and path == "/remote/assets/" + "a" * 32
        return SimpleNamespace(content=self.data)


def test_download_and_reuse_cached_png(tmp_path):
    cache = tmp_path / "private"
    cache.mkdir(mode=0o700)
    central = FakeCentral(png())
    received = receive_frames(central, cache, expected_server_id="srv",
                              expected_arena_id="arena")
    frame = received["frames"]["horizontal"]
    assert Path(frame["localPath"]).read_bytes() == central.data
    assert json.loads((cache / "quadra02-frames.json").read_text()) == received
    assert all(method == "GET" for method, _ in central.calls)
    assert len(central.calls) == 2
    receive_frames(central, cache, expected_server_id="srv", expected_arena_id="arena")
    assert len(central.calls) == 3  # somente manifesto; usa PNG já validado


def test_bad_digest_does_not_publish_receipt(tmp_path):
    cache = tmp_path / "private"
    cache.mkdir(mode=0o700)
    central = FakeCentral(png())
    central.data = b"nao-e-png"
    with pytest.raises(ValueError, match="SHA-256"):
        receive_frames(central, cache, expected_server_id="srv",
                       expected_arena_id="arena")
    assert not (cache / "quadra02-frames.json").exists()


def test_credentials_scope_and_private_directory(tmp_path):
    data = png()
    central = FakeCentral(data)
    with pytest.raises(ValueError, match="divergente"):
        fetch_manifest(central, expected_server_id="outro", expected_arena_id="arena")
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="privada"):
        receive_frames(central, public, expected_server_id="srv", expected_arena_id="arena")
    assert not list(public.iterdir())


def test_missing_selected_frame_is_rejected():
    central = FakeCentral(png())
    central.manifest["frames"]["horizontal"] = None
    with pytest.raises(ValueError, match="sem moldura"):
        fetch_manifest(central, expected_server_id="srv", expected_arena_id="arena")


def test_disabled_frames_are_accepted():
    central = FakeCentral(png())
    central.manifest["framesEnabled"] = False
    central.manifest["frames"]["horizontal"] = None
    received = fetch_manifest(central, expected_server_id="srv", expected_arena_id="arena")
    assert not received["framesEnabled"]
