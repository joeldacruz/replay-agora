"""Geração do MP4 único da Quadra 02, FORA das pastas monitoradas (tmp_path).

Cobre geração/rotação, publicação atômica, identidade uuid5, miniatura do MP4
final e leitura por intervalo (Range). Não usa câmeras, buffers, SQLite real,
sincronizador nem AgentService.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from replay_agent import legacy_engine_mp4 as eng

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                reason="FFmpeg e ffprobe são necessários")
NAME = "replay-20261127-101500-abcd1234.mp4"


def make_segments(base: Path, n=3, audio=True):
    seg = base / "buffer"
    seg.mkdir()
    lines = []
    for i in range(n):
        p = seg / f"{i:04d}.ts"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
               "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2"]
        if audio:
            cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:a", "aac"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(p)]
        subprocess.run(cmd, check=True)
        lines.append(f"file '{p}'\n")
    lst = base / "concat.txt"
    lst.write_text("".join(lines))
    return lst


@pytest.fixture
def dirs(tmp_path):
    for d in ("replays", "work", "thumbs"):
        (tmp_path / d).mkdir()
    return tmp_path


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,width,height:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@pytest.mark.parametrize("angle,dims", [(0, (320, 240)), (90, (240, 320)),
                                        (180, (320, 240)), (270, (240, 320))])
def test_generation_applies_rotation_and_publishes_single_file(dirs, angle, dims):
    lst = make_segments(dirs)
    final = dirs / "replays" / NAME
    info = eng.build_final(lst, final, angle, work_dir=dirs / "work", expected_duration=6)
    assert (info["width"], info["height"]) == dims
    meta = probe(final)
    v = [s for s in meta["streams"] if s["codec_type"] == "video"][0]
    assert (v["width"], v["height"]) == dims
    assert any(s["codec_type"] == "audio" for s in meta["streams"])
    # único MP4 permanente; nenhum temporário sobra em nenhuma pasta
    assert [p.name for p in (dirs / "replays").iterdir()] == [NAME]
    assert list((dirs / "work").iterdir()) == []


def test_part_file_is_never_visible_to_sync_glob_while_generating(dirs, monkeypatch):
    lst = make_segments(dirs)
    final = dirs / "replays" / NAME
    seen = []
    real = subprocess.run

    def spy(cmd, *a, **k):
        seen.append(sorted(p.name for p in (dirs / "replays").glob("replay-*.mp4")))
        seen.append(sorted(p.name for p in (dirs / "work").iterdir()))
        return real(cmd, *a, **k)

    monkeypatch.setattr(eng.subprocess, "run", spy)
    eng.build_final(lst, final, 90, work_dir=dirs / "work")
    assert seen[0] == []                       # nada publicado antes da conclusão
    assert seen[1] and seen[1][0].endswith(".part.mp4") and seen[1][0].startswith(".replay-")
    assert list((dirs / "work").iterdir()) == []


@pytest.mark.parametrize("bad_dir", ["same", "missing"])
def test_refuses_unsafe_work_dir(dirs, bad_dir):
    lst = make_segments(dirs)
    work = dirs / "replays" if bad_dir == "same" else dirs / "nope"
    with pytest.raises(eng.PublishError):
        eng.build_final(lst, dirs / "replays" / NAME, 90, work_dir=work)
    assert list((dirs / "replays").glob("replay-*.mp4")) == []


def test_failure_leaves_no_final_and_cleans_part(dirs):
    lst = dirs / "concat.txt"
    lst.write_text("file '/nao/existe.ts'\n")
    with pytest.raises(eng.PublishError):
        eng.build_final(lst, dirs / "replays" / NAME, 90, work_dir=dirs / "work")
    assert list((dirs / "replays").iterdir()) == []
    assert list((dirs / "work").iterdir()) == []


def test_wrong_expected_duration_blocks_publication(dirs):
    lst = make_segments(dirs)
    with pytest.raises(eng.PublishError, match="duração"):
        eng.build_final(lst, dirs / "replays" / NAME, 0, work_dir=dirs / "work", expected_duration=15)
    assert list((dirs / "replays").iterdir()) == []


def test_never_overwrites_and_rejects_bad_name_or_angle(dirs):
    lst = make_segments(dirs)
    final = dirs / "replays" / NAME
    eng.build_final(lst, final, 0, work_dir=dirs / "work")
    digest = hashlib.sha256(final.read_bytes()).hexdigest()
    with pytest.raises(eng.PublishError, match="já existe"):
        eng.build_final(lst, final, 90, work_dir=dirs / "work")
    assert hashlib.sha256(final.read_bytes()).hexdigest() == digest
    with pytest.raises(eng.PublishError, match="nome"):
        eng.build_final(lst, dirs / "replays" / "qualquer.mp4", 0, work_dir=dirs / "work")
    with pytest.raises(eng.PublishError, match="orientação"):
        eng.build_final(lst, dirs / "replays" / "replay-20261127-101501-abcd1235.mp4", 45,
                        work_dir=dirs / "work")


def test_source_segments_untouched(dirs):
    lst = make_segments(dirs)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (dirs / "buffer").iterdir()}
    eng.build_final(lst, dirs / "replays" / NAME, 270, work_dir=dirs / "work")
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (dirs / "buffer").iterdir()}
    assert before == after


def test_video_without_audio_is_supported(dirs):
    lst = make_segments(dirs, audio=False)
    eng.build_final(lst, dirs / "replays" / NAME, 90, work_dir=dirs / "work")
    meta = probe(dirs / "replays" / NAME)
    assert [s["codec_type"] for s in meta["streams"]] == ["video"]


def test_identity_is_uuid5_of_final_name(dirs):
    lst = make_segments(dirs)
    info = eng.build_final(lst, dirs / "replays" / NAME, 90, work_dir=dirs / "work")
    ns = uuid.UUID("b60f62dd-b871-4d7a-94b7-f65736ca59bc")
    assert info["localId"] == str(uuid.uuid5(ns, "QUADRA_02:" + NAME))
    # idêntica à função do modo sombra, que o sincronizador deve reproduzir
    from replay_agent.legacy_rotation_shadow import local_id_for
    assert info["localId"] == local_id_for(Path(NAME))
    assert (info["width"], info["height"]) == (240, 320)    # metadados do arquivo FINAL


def test_thumbnail_comes_from_final_mp4(dirs):
    lst = make_segments(dirs)
    final = dirs / "replays" / NAME
    eng.build_final(lst, final, 90, work_dir=dirs / "work")
    jpg = dirs / "thumbs" / (NAME + ".jpg")
    size = eng.make_thumbnail(final, jpg)
    data = jpg.read_bytes()
    assert 64 <= size == len(data) <= 262144
    assert data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9"
    dim = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", str(jpg)], capture_output=True, text=True).stdout.strip()
    w, h = map(int, dim.split(","))
    assert h > w                                   # miniatura retrato = orientação aplicada
    assert [p.name for p in (dirs / "thumbs").iterdir()] == [NAME + ".jpg"]


def test_playback_faststart_decode_and_range_slices(dirs):
    lst = make_segments(dirs)
    final = dirs / "replays" / NAME
    eng.build_final(lst, final, 180, work_dir=dirs / "work")
    data = final.read_bytes()
    assert data.find(b"moov") < data.find(b"mdat")             # reprodução progressiva/seek
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(final), "-f", "null", "-"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and not r.stderr.strip()
    # leitura por intervalo exatamente como o relay (206) a faria
    from app.delivery import parse_range
    start, end = parse_range("bytes=100-4195", len(data))
    with final.open("rb") as f:
        f.seek(start)
        assert f.read(end - start + 1) == data[100:4196]
    assert parse_range(f"bytes={len(data)+5}-", len(data)) == "invalid"


def test_resolve_angle_policy(tmp_path):
    d = tmp_path / "priv"
    d.mkdir(mode=0o700)
    f = d / "orientation.json"
    assert eng.resolve_angle(f) == (0, "legacy_no_receipt")
    good = {"courtKey": "QUADRA_02", "rotationDeg": 270, "version": 4}
    f.write_text(json.dumps(good)); os.chmod(f, 0o600)
    assert eng.resolve_angle(f) == (270, "receipt")
    os.chmod(f, 0o644)
    assert eng.resolve_angle(f) == (0, "legacy_invalid_receipt")        # permissão aberta
    os.chmod(f, 0o600)
    for bad in ({**good, "rotationDeg": 45}, {**good, "courtKey": "QUADRA_01"},
                {**good, "version": 0}, {**good, "rotationDeg": True}):
        f.write_text(json.dumps(bad))
        assert eng.resolve_angle(f) == (0, "legacy_invalid_receipt")
    f.write_text("{corrompido")
    assert eng.resolve_angle(f) == (0, "legacy_invalid_receipt")


def test_cleanup_only_removes_old_part_files(dirs):
    w = dirs / "work"
    old, new, other = w / ".replay-a.part.mp4", w / ".replay-b.part.mp4", w / "concat.txt"
    for p in (old, new, other):
        p.write_bytes(b"x")
    os.utime(old, (1, 1))
    assert eng.cleanup_stale(w) == 1
    assert not old.exists() and new.exists() and other.exists()


@pytest.mark.parametrize("angle,expected", [(0, (320, 240)), (90, (240, 320)), (180, (320, 240))])
def test_one_final_mp4_with_frame_and_rotation_in_single_encode(dirs, angle, expected):
    """PNG + orientação no mesmo FFmpeg, sem MP4 intermediário nem duplicata."""
    import struct
    import zlib

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body +
                struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff))

    fw, fh = 320, 240
    pixel = bytes((0, 0, 255, 255))
    clear = bytes((0, 0, 0, 0))
    rows = b"".join(
        b"\\x00" + (pixel * fw if y < 16 or y >= fh - 16
                  else pixel * 16 + clear * (fw - 32) + pixel * 16)
        for y in range(fh))
    png = (b"\\x89PNG\\r\\n\\x1a\\n" +
           chunk(b"IHDR", struct.pack(">IIBBBBB", fw, fh, 8, 6, 0, 0, 0)) +
           chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    private = dirs / "frame-private"
    private.mkdir(mode=0o700)
    path = private / "theme.png"
    path.write_bytes(png)

    out_w, out_h = expected
    frame = {"localPath": str(path), "sha256": hashlib.sha256(png).hexdigest(),
             "width": fw, "height": fh, "out": f"{out_w}x{out_h}",
             "window": {"x": 16, "y": 16, "w": fw - 32, "h": fh - 32},
             "layout": {"frameFit": "contain", "background": "black"}}

    lst = make_segments(dirs)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (dirs / "buffer").iterdir()}
    target = dirs / "replays" / NAME
    info = eng.build_final(lst, target, angle, work_dir=dirs / "work",
                           expected_duration=6, frame=frame)
    assert (info["width"], info["height"]) == expected
    assert len(list((dirs / "replays").glob("*.mp4"))) == 1
    assert list((dirs / "work").iterdir()) == []
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (dirs / "buffer").iterdir()}
    assert any(s["codec_type"] == "audio" for s in probe(target)["streams"])


def test_invalid_png_hash_never_publishes_replay(dirs):
    lst = make_segments(dirs)
    art = dirs / "frame.png"
    art.write_bytes(b"bad")
    frame = {"localPath": str(art), "sha256": "0" * 64,
             "width": 320, "height": 240, "out": "320x240",
             "window": {"x": 16, "y": 16, "w": 288, "h": 208},
             "layout": {}}
    with pytest.raises(eng.PublishError, match="SHA-256"):
        eng.build_final(lst, dirs / "replays" / NAME, 0,
                        work_dir=dirs / "work", frame=frame)
    assert not list((dirs / "replays").glob("*.mp4"))
