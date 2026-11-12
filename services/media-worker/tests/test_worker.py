"""Worker FFmpeg: executa de verdade e confere as faixas de áudio por análise espectral e o overlay por pixel."""
import re
import subprocess

import pytest
from PIL import Image

from app import mediainfo, storage
from app.config import settings
from app.db import SessionLocal, utcnow
from app.models import ExportJob, MusicTrack
from conftest import MUSIC_HZ, ORIG_HZ
from worker import runner
from worker.ffmpeg import ExportPlan, Overlay, PlanError, SourceMeta, build_command


def band_db(path, hz) -> float:
    """Nível médio (dB) em banda estreita ao redor de `hz` (dois passa-faixas em cascata)."""
    af = f"bandpass=f={hz}:width_type=h:w=100,bandpass=f={hz}:width_type=h:w=100,volumedetect"
    r = subprocess.run([settings.ffmpeg_bin, "-v", "info", "-i", str(path), "-vn", "-af", af, "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"mean_volume: (-?[\d.]+|-inf) dB", r.stderr)
    return -200.0 if not m or m.group(1) == "-inf" else float(m.group(1))


def make_job(client, H, world, vid, **body):
    r = client.post(f"/api/videos/{vid}/exports", headers=H("a_user"), json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def run_all():
    n = 0
    while runner.process_one():
        n += 1
    return n


def job(id_):
    db = SessionLocal()
    j = db.get(ExportJob, id_)
    db.expunge(j)
    db.close()
    return j


def out(id_):
    return storage.path_for(job(id_).output_key)


PRESENT, ABSENT = -40.0, -60.0


def test_mode_original_keeps_only_original_audio(client, world, H):
    jid = make_job(client, H, world, world.A.pub, audioMode="original")
    run_all()
    j = job(jid)
    assert j.status == "ready" and j.progress == 100
    assert band_db(out(jid), ORIG_HZ) > PRESENT and band_db(out(jid), MUSIC_HZ) < ABSENT


def test_mode_music_replaces_audio_without_leaking_original(client, world, H):
    jid = make_job(client, H, world, world.A.pub, audioMode="music", musicId=world.A.tracks["ok"])
    run_all()
    assert job(jid).status == "ready"
    o = out(jid)
    assert band_db(o, MUSIC_HZ) > PRESENT and band_db(o, ORIG_HZ) < ABSENT
    # música (2s) é repetida para cobrir todo o vídeo (3s)
    assert abs(float(mediainfo.probe(o)["format"]["duration"]) - 3.0) < 0.3


def test_mode_mix_has_both_tracks_synchronised(client, world, H):
    jid = make_job(client, H, world, world.A.pub, audioMode="mix", musicId=world.A.tracks["ok"], originalVolume=1.0, musicVolume=0.8)
    run_all()
    o = out(jid)
    assert band_db(o, ORIG_HZ) > PRESENT and band_db(o, MUSIC_HZ) > PRESENT
    assert abs(float(mediainfo.probe(o)["format"]["duration"]) - 3.0) < 0.3


def test_mix_volumes_are_applied(client, world, H):
    a = make_job(client, H, world, world.A.pub, audioMode="mix", musicId=world.A.tracks["ok"], originalVolume=1.0, musicVolume=1.0)
    b = make_job(client, H, world, world.A.pub, audioMode="mix", musicId=world.A.tracks["ok"], originalVolume=1.0, musicVolume=0.2)
    run_all()
    assert band_db(out(a), MUSIC_HZ) - band_db(out(b), MUSIC_HZ) > 8  # ~14 dB de diferença esperada


def test_mode_mute_has_no_audio_stream(client, world, H):
    jid = make_job(client, H, world, world.A.pub, audioMode="mute")
    run_all()
    streams = mediainfo.probe(out(jid))["streams"]
    assert job(jid).status == "ready" and not any(s["codec_type"] == "audio" for s in streams)
    assert any(s["codec_type"] == "video" for s in streams)


def test_source_without_audio_handled(client, world, H):
    # API recusa original/mix ...
    for mode in ("original", "mix"):
        r = client.post(f"/api/videos/{world.A.mute}/exports", headers=H("a_user"), json={"audioMode": mode, "musicId": world.A.tracks["ok"]})
        assert r.status_code == 422
    opts = client.get(f"/api/videos/{world.A.mute}/export-options", headers=H("a_user")).json()
    assert opts["hasOriginalAudio"] is False and set(opts["modes"]) == {"music", "mute"}
    # ... e música e mudo funcionam
    m = make_job(client, H, world, world.A.mute, audioMode="music", musicId=world.A.tracks["ok"])
    z = make_job(client, H, world, world.A.mute, audioMode="mute")
    run_all()
    assert band_db(out(m), MUSIC_HZ) > PRESENT
    assert not any(s["codec_type"] == "audio" for s in mediainfo.probe(out(z))["streams"])


def test_sponsor_overlay_rendered_in_correct_corner(client, world, H, tmp_path):
    jid = make_job(client, H, world, world.A.pub, audioMode="mute")
    from app.models import Video
    db = SessionLocal()
    source = storage.path_for(db.get(Video, world.A.pub).storage_key)
    db.close()
    run_all()
    assert job(jid).sponsor_template_version
    def frame(p, n):
        dst = tmp_path / f"{n}.png"
        subprocess.run([settings.ffmpeg_bin, "-y", "-v", "error", "-ss", "1", "-i", str(p), "-frames:v", "1", str(dst)], check=True)
        return Image.open(dst).convert("RGB")
    f, g = frame(out(jid), "ov"), frame(source, "plain")
    # campanha da seed: top-left, 20% da largura (640px -> 128px), margem 2% (13px)
    px = f.getpixel((13 + 64, 13 + 25))
    assert px[0] > 200 and px[2] > 200 and px[1] < 80, px  # magenta do logo
    assert g.getpixel((13 + 64, 13 + 25)) != px
    # canto oposto intacto
    # A codificacao com overlay muda ligeiramente os pixels: comparar com tolerancia.
    corner_a, corner_b = f.getpixel((f.width - 20, f.height - 20)), g.getpixel((g.width - 20, g.height - 20))
    assert max(abs(a - b) for a, b in zip(corner_a, corner_b)) < 35


def test_overlay_uses_only_own_arena_campaigns(client, world, H):
    from app.db import SessionLocal as S
    from app.models import SponsorCampaign
    db = S()
    db.add(SponsorCampaign(arena_id=world.B.id, sponsor_id=world.B.sponsor, position="bottom-right", priority=1))  # B: não deve vazar para A
    db.commit()
    db.close()
    jid = make_job(client, H, world, world.A.pub, audioMode="mute")
    import json
    spec = json.loads(job(jid).overlay_spec)
    assert spec and all(o["asset_key"].startswith(f"arenas/{world.A.id}/") for o in spec)
    assert {o["position"] for o in spec} == {"top-left"}


def test_license_expiring_before_render_blocks_music(client, world, H):
    from datetime import timedelta
    jid = make_job(client, H, world, world.A.pub, audioMode="music", musicId=world.A.tracks["ok"])
    db = SessionLocal()
    db.get(MusicTrack, world.A.tracks["ok"]).license_valid_until = utcnow() - timedelta(seconds=5)
    db.commit()
    db.close()
    run_all()
    j = job(jid)
    assert j.status == "failed" and "licença" in j.error and j.output_key is None


def test_corrupt_source_fails_cleanly_without_partial_file(client, world, H):
    jid = make_job(client, H, world, world.A.pub, audioMode="mute")
    db = SessionLocal()
    from app.models import Video
    v = db.get(Video, world.A.pub)
    storage.path_for(v.storage_key).write_bytes(b"not a video")
    db.close()
    run_all()
    j = job(jid)
    assert j.status == "failed" and j.error
    assert not list((settings.storage_dir / "arenas" / world.A.id / "exports").glob("*"))


def test_stale_processing_job_is_requeued_then_completed(client, world, H):
    from datetime import timedelta
    jid = make_job(client, H, world, world.A.pub, audioMode="mute")
    db = SessionLocal()
    j = db.get(ExportJob, jid)
    j.status, j.attempts, j.heartbeat_at = "processing", 1, utcnow() - timedelta(seconds=settings.export_stale_after_s + 5)
    db.commit()
    db.close()
    run_all()
    assert job(jid).status == "ready" and job(jid).attempts == 2


def test_claim_is_exclusive(client, world, H):
    make_job(client, H, world, world.A.pub, audioMode="mute")
    d1, d2 = SessionLocal(), SessionLocal()
    a, b = runner.claim(d1), runner.claim(d2)
    d1.close(); d2.close()
    assert a is not None and b is None


def test_dedup_identical_requests_single_job(client, world, H):
    a = make_job(client, H, world, world.A.pub, audioMode="mute")
    b = make_job(client, H, world, world.A.pub, audioMode="mute")
    assert a == b
    run_all()
    assert make_job(client, H, world, world.A.pub, audioMode="mute") == a  # pronto: reutiliza


# ---------- unidade: construtor do comando ----------
META = SourceMeta(10.0, 1280, 720, "h264", True, "aac")


def cmd(**kw):
    return build_command(ExportPlan("in.mp4", "out.mp4", kw.pop("meta", META), kw.pop("mode"), **kw))


def test_command_is_argument_list_and_copy_when_possible():
    c = cmd(mode="original")
    assert isinstance(c, list) and all(isinstance(x, str) for x in c)
    assert c[c.index("-c:v") + 1] == "copy" and c[c.index("-c:a") + 1] == "copy"
    assert "-an" in cmd(mode="mute") and "-filter_complex" not in cmd(mode="mute")


def test_command_music_and_mix_filters():
    assert "amix" not in " ".join(cmd(mode="music", music_path="m.mp3"))
    mix = cmd(mode="mix", music_path="m.mp3", original_volume=0.5, music_volume=0.7)
    s = " ".join(mix)
    assert "amix=inputs=2" in s and "normalize=0" in s and "alimiter" in s and "volume=0.500" in s and "volume=0.700" in s
    assert "-stream_loop" in mix


def test_command_rejects_invalid_combinations():
    with pytest.raises(PlanError):
        cmd(mode="original", meta=SourceMeta(5, 640, 360, "h264", False))
    with pytest.raises(PlanError):
        cmd(mode="mix", meta=SourceMeta(5, 640, 360, "h264", False), music_path="m.mp3")
    with pytest.raises(PlanError):
        cmd(mode="music")


def test_command_transcodes_with_overlay_or_non_h264():
    ov = Overlay("l.png", 400, 160, "bottom-right", 15, 0.5, 3.0)
    s = " ".join(cmd(mode="mute", overlays=[ov]))
    assert "libx264" in s and "overlay=x=" in s and "between(t,0,3.000)" in s and "colorchannelmixer=aa=0.500" in s
    assert "libx264" in " ".join(cmd(mode="mute", meta=SourceMeta(5, 640, 360, "hevc", False)))


def test_user_input_never_reaches_shell():
    c = cmd(mode="music", music_path="m; rm -rf /.mp3")
    assert "m; rm -rf /.mp3" in c  # permanece um único argumento literal


# ---------- música ATRIBUÍDA ao replay é usada de verdade na reprodução (versão derivada), sem tocar no original ----------
def _arena_music(world, mode, campaigns_active=False, assign=True, track="ok"):
    import hashlib
    from app.models import Arena, SponsorCampaign, Video
    db = SessionLocal()
    db.get(Arena, world.A.id).audio_default_mode = mode
    for c in db.query(SponsorCampaign).filter_by(arena_id=world.A.id):
        c.active = campaigns_active
    v = db.get(Video, world.A.pub)
    v.music_id = world.A.tracks[track] if assign else None
    v.music_reason = "rotation" if assign else "fallback_ambient:teste"
    db.commit()
    src = storage.path_for(v.storage_key)
    h = hashlib.sha256(src.read_bytes()).hexdigest()
    db.close()
    return src, h


def _play(client, H, vid):
    r = client.get(f"/api/videos/{vid}", headers=H("a_user")).json()
    return r


def test_assigned_music_is_what_the_player_hears_and_original_is_untouched(client, world, H):
    src, h0 = _arena_music(world, "music")
    v = _play(client, H, world.A.pub)
    assert v["derived"] is True and v["playbackAudio"] == "music"
    assert client.get(f"/api/videos/{world.A.pub}/play", headers=H("a_user")).status_code == 409            # não serve o original cru
    assert client.post(f"/api/videos/{world.A.pub}/download-link", headers=H("a_user")).status_code == 409
    pv = client.post(f"/api/videos/{world.A.pub}/preview", headers=H("a_user")).json()
    assert pv["status"] in ("queued", "processing", "ready")
    run_all()
    done = client.get(f"/api/previews/{pv['id']}", headers=H("a_user")).json()
    assert done["status"] == "ready" and done["url"]
    o = out(pv["id"])
    assert band_db(o, MUSIC_HZ) > PRESENT and band_db(o, ORIG_HZ) < ABSENT          # ouve-se a música atribuída, não o som ambiente
    assert job(pv["id"]).music_id == world.A.tracks["ok"]
    import hashlib
    assert hashlib.sha256(src.read_bytes()).hexdigest() == h0                         # MP4 original intacto


def test_mix_mode_plays_ambient_plus_assigned_music(client, world, H):
    _arena_music(world, "mix")
    assert _play(client, H, world.A.pub)["playbackAudio"] == "mix"
    pv = client.post(f"/api/videos/{world.A.pub}/preview", headers=H("a_user")).json()
    run_all()
    assert band_db(out(pv["id"]), ORIG_HZ) > PRESENT and band_db(out(pv["id"]), MUSIC_HZ) > PRESENT


def test_ambient_fallback_keeps_original_playback_when_no_valid_track(client, world, H):
    _arena_music(world, "music", assign=False)
    v = _play(client, H, world.A.pub)
    assert v["derived"] is False and v["playbackAudio"] == "original"
    assert client.get(f"/api/videos/{world.A.pub}/play", headers=H("a_user")).status_code == 200
    _arena_music(world, "music", track="expired")                       # faixa atribuída com licença vencida ⇒ não é usada
    assert _play(client, H, world.A.pub)["derived"] is False
    _arena_music(world, "music", track="disabled")
    assert _play(client, H, world.A.pub)["derived"] is False


def test_export_defaults_to_assigned_track_and_arena_without_music_mode_plays_ambient(client, world, H):
    _arena_music(world, "music")
    opts = client.get(f"/api/videos/{world.A.pub}/export-options", headers=H("a_user")).json()
    assert opts["assignedMusicId"] == world.A.tracks["ok"] and opts["defaultMusicId"] == world.A.tracks["ok"]
    _arena_music(world, "original")                                       # arena em som ambiente: a faixa registrada não toca
    v = _play(client, H, world.A.pub)
    assert v["derived"] is False and v["playbackAudio"] == "original"
