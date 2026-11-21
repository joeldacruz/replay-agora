"""Testes HTTP isolados do upload JPEG da arena: usam SQLite/Storage temporários do conftest."""
import hashlib
from io import BytesIO

from PIL import Image
from app.db import SessionLocal
from app.models import Video
from app import storage

BASE = "/api/agent/v1"


def _headers(world, arena="A", node=None, jpeg=None):
    header = {
        "Authorization": "Bearer " + getattr(world, arena).token,
        "X-Agent-Node": node or f"node-{arena}-0001",
        "Content-Type": "image/jpeg",
    }
    if jpeg is not None:
        header["X-Thumb-Sha256"] = hashlib.sha256(jpeg).hexdigest()
    return header


def _register(client, world, arena="A"):
    h = _headers(world, arena)\n    h.pop("Content-Type", None)\n    r = client.post(f"{BASE}/register", headers=h,
                    json={"nodeId": f"node-{arena}-0001"})
    assert r.status_code == 200, r.text


def _create_replay(client, world, arena="A", local_id="thumb-demo-001"):
    h = _headers(world, arena)\n    h.pop("Content-Type", None)\n    r = client.post(f"{BASE}/sync/replays", headers=h,
                    json={"replays": [{
                        "localId": local_id, "courtKey": f"{arena}_01",
                        "capturedAt": "2026-11-20T12:00:00Z",
                        "durationS": 8, "bytes": 5000,
                        "fileAvailable": True,
                    }]})
    assert r.status_code == 200, r.text
    out = r.json()["results"][0]
    assert out["status"] in ("created", "duplicate"), out
    return out["videoId"]


def _jpeg():
    out = BytesIO()
    Image.new("RGB", (480, 270), (48, 110, 170)).save(out, format="JPEG")
    return out.getvalue()


def _upload(client, world, local_id, image, arena="A", headers=None):
    h = _headers(world, arena, jpeg=image)
    if headers:
        h.update(headers)
    return client.post(f"{BASE}/sync/replays/{local_id}/thumbnail",
                       content=image, headers=h)


def test_thumbnail_upload_valid_and_idempotent(client, world):
    _register(client, world)
    vid = _create_replay(client, world)
    jpeg = _jpeg()
    one = _upload(client, world, "thumb-demo-001", jpeg)
    assert one.status_code == 200, one.text
    assert one.json()["status"] == "created"

    db = SessionLocal()
    try:
        video = db.get(Video, vid)
        key = video.thumb_key
        assert key and key.endswith(f"{vid}.jpg")
        assert storage.path_for(key).read_bytes() == jpeg
    finally:
        db.close()

    two = _upload(client, world, "thumb-demo-001", jpeg)
    assert two.status_code == 200, two.text
    assert two.json()["status"] == "exists"


def test_thumbnail_upload_rejects_other_arena_and_unauthenticated(client, world):
    _register(client, world, "A")
    _register(client, world, "B")
    vid = _create_replay(client, world)
    jpeg = _jpeg()
    assert _upload(client, world, "thumb-demo-001", jpeg, arena="B").status_code == 404
    no_auth = client.post(f"{BASE}/sync/replays/thumb-demo-001/thumbnail",
                          content=jpeg, headers={"Content-Type": "image/jpeg"})
    assert no_auth.status_code == 401
    db = SessionLocal()
    try:
        assert db.get(Video, vid).thumb_key is None
    finally:
        db.close()


def test_thumbnail_upload_rejects_invalid_integrity_format_and_size(client, world):
    _register(client, world)
    vid = _create_replay(client, world)
    jpeg = _jpeg()
    wrong_hash = _upload(client, world, "thumb-demo-001", jpeg,
                         headers={"X-Thumb-Sha256": "0" * 64})
    assert wrong_hash.status_code == 422

    fake_jpeg = bytes.fromhex("FFD8FF") + b"x" * 96 + bytes.fromhex("FFD9")
    invalid = _upload(client, world, "thumb-demo-001", fake_jpeg)
    assert invalid.status_code == 422

    bad_type = _upload(client, world, "thumb-demo-001", jpeg,
                       headers={"Content-Type": "video/mp4"})
    assert bad_type.status_code == 415

    large = bytes.fromhex("FFD8FF") + b"x" * (256 * 1024) + bytes.fromhex("FFD9")
    oversized = _upload(client, world, "thumb-demo-001", large)
    assert oversized.status_code == 413

    db = SessionLocal()
    try:
        assert db.get(Video, vid).thumb_key is None
    finally:
        db.close()
