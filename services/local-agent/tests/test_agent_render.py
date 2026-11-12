"""Renderização local no agente: prévia patrocinada e exportação de áudio (original|music|mix|mute) sem que o vídeo
original — nem o resultado — fique na central."""
import hashlib
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import storage
from app.config import settings
from app.db import SessionLocal, utcnow
from app.main import app
from app.models import ArenaAgent, ExportJob, MusicTrack, SponsorCampaign, Video
from replay_agent import ffplan
from replay_agent.client import CentralClient, CentralRejected
from replay_agent.relay import RelayServer
from replay_agent.renderjobs import RenderWorker
from replay_agent.simulator import build_arena
from worker import runner
from worker.ffmpeg import ExportPlan as WPlan, Overlay as WOverlay, SourceMeta as WMeta, build_command as wcmd

T0 = datetime(2026, 11, 5, 12, 0, 0)


class Rig:
    pass


@pytest.fixture
def rig(client, world, media, tmp_path, monkeypatch, H):
    monkeypatch.setattr(settings, "relay_timeout_s", 5)
    monkeypatch.setattr(settings, "relay_spool_dir", tmp_path / "spool")
    r = Rig()
    r.client, r.world, r.H, r.tmp, r.media = client, world, H, tmp_path, media
    r.agent = build_arena("A", world.A.token, ["A_01", "A_02"], client, root=tmp_path / "A")
    r.src = {}
    for i, (k, kind) in enumerate((("A_01", "audio"), ("A_02", "mute"))):
        lid = f"A-{k}-{2000 + i}"
        p = tmp_path / "A" / "replays" / k / f"{lid}.mp4"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(media[kind].read_bytes())
        from app import mediainfo
        info = mediainfo.video_info(p)
        r.agent.store.add_replay(lid, k, T0 + timedelta(seconds=i), str(p), p.stat().st_size, info.duration, info.width, info.height, info.has_audio, info.video_codec)
        r.src[k] = (lid, p)
    assert r.agent.link.tick()["online"]
    db = SessionLocal()
    r.ids = {}
    for k, (lid, _) in r.src.items():
        r.ids[k] = db.query(Video).filter_by(arena_id=world.A.id, local_id=lid).one().id
        client.patch(f"/api/arena/{world.A.id}/videos/{r.ids[k]}", headers=H("root"), json={"visibility": "arena"})
    db.close()
    mk = lambda: CentralClient(TestClient(app), world.A.token, "node-A")  # noqa: E731
    r.render = RenderWorker(mk(), r.agent.store, tmp_path / "A" / "renders", nice=0, allowed_source_roots=[tmp_path / "A" / "replays"])
    r.relay = RelayServer(mk(), r.agent.store, [tmp_path / "A"])
    r.stop = threading.Event()
    r.threads = r.relay.start(r.stop, workers=2, wait=1)
    r.snapshot = sorted(str(p) for p in storage.root().rglob("*") if p.is_file())
    yield r
    r.stop.set()
    for t in r.threads:
        t.join(timeout=5)
    r.agent.engine.stop()


def job(db, jid):
    return db.get(ExportJob, jid)


def download(rig, url, tmp_name="dl.mp4"):
    body = rig.client.get(url)
    assert body.status_code == 200, body.text[:200]
    p = rig.tmp / tmp_name
    p.write_bytes(body.content)
    return p


def streams(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(p)], capture_output=True, text=True).stdout
    return out.split()


def magenta(path, box=0.4):
    sub = path.parent / (path.stem + ".png")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "0.5", "-i", str(path), "-frames:v", "1", str(sub)], check=True)
    im = Image.open(sub).convert("RGB")
    w, h = im.size
    return sum(1 for x in range(int(w * box)) for y in range(int(h * box)) if (lambda p: p[0] > 200 and p[1] < 80 and p[2] > 200)(im.getpixel((x, y))))


def make_export(rig, k="A_01", mode="mute", who="a_user", **body):
    r = rig.client.post(f"/api/videos/{rig.ids[k]}/exports", headers=rig.H(who), json={"audioMode": mode, **body})
    assert r.status_code == 200, r.text
    return r.json()


# ---------- prévia patrocinada ----------
def test_sponsored_preview_is_rendered_on_the_arena_and_streamed_via_relay(rig):
    # o atleta NUNCA recebe o original quando há patrocínio
    assert rig.client.get(f"/api/videos/{rig.ids['A_01']}/play", headers=rig.H("a_user")).status_code == 409
    p = rig.client.post(f"/api/videos/{rig.ids['A_01']}/preview", headers=rig.H("a_user")).json()
    assert p["status"] == "queued" and p["sponsored"] is True and not p.get("url")
    db = SessionLocal()
    j = job(db, p["id"])
    assert j.render_location == "agent" and j.status == "queued" and j.output_key is None
    db.close()
    assert runner.process_one() is False                      # o worker CENTRAL não toca em jobs do agente
    assert rig.render.run_one() is True
    st = rig.client.get(f"/api/previews/{p['id']}", headers=rig.H("a_user")).json()
    assert st["status"] == "ready" and st["url"]
    got = download(rig, st["url"], "preview.mp4")
    assert "video" in streams(got) and "audio" in streams(got)
    assert magenta(got) > magenta(rig.src["A_01"][1]) + 100  # logotipo do patrocinador aplicado no render local
    # saída guardada na ARENA; a central não ficou com nada
    db = SessionLocal()
    j = job(db, p["id"])
    assert j.status == "ready" and j.output_key is None and j.output_bytes == got.stat().st_size
    assert j.output_sha256 == hashlib.sha256(got.read_bytes()).hexdigest()
    db.close()
    assert (rig.tmp / "A" / "renders" / f"{p['id']}.mp4").is_file()
    assert sorted(str(x) for x in storage.root().rglob("*") if x.is_file()) == rig.snapshot
    assert not list(settings.relay_spool_dir.glob("*"))


def test_preview_cache_is_shared_between_players_and_keeps_privacy(rig):
    a = rig.client.post(f"/api/videos/{rig.ids['A_01']}/preview", headers=rig.H("a_user")).json()
    b = rig.client.post(f"/api/videos/{rig.ids['A_01']}/preview", headers=rig.H("a_user2")).json()
    assert a["id"] == b["id"]                                            # um único render para os dois
    rig.render.run_one()
    assert rig.render.run_one() is False
    assert rig.client.get(f"/api/previews/{a['id']}", headers=rig.H("a_user2")).json()["status"] == "ready"
    assert rig.client.get(f"/api/previews/{a['id']}", headers=rig.H("b_user")).status_code == 404  # outra arena
    assert rig.client.get(f"/api/previews/{a['id']}").status_code == 401


# ---------- exportação de áudio ----------
@pytest.mark.parametrize("mode,has_audio", [("original", True), ("music", True), ("mix", True), ("mute", False)])
def test_all_four_audio_modes_rendered_locally(rig, mode, has_audio):
    body = {"musicId": rig.world.A.tracks["ok"]} if mode in ("music", "mix") else {}
    j = make_export(rig, "A_01", mode, **body)
    assert j["status"] == "queued" and j["renderLocation"] == "agent"
    assert rig.render.run_one()
    done = rig.client.get(f"/api/exports/{j['id']}", headers=rig.H("a_user")).json()
    assert done["status"] == "ready" and done["downloadUrl"] and done["available"] is True
    got = download(rig, done["downloadUrl"], f"{mode}.mp4")
    assert ("audio" in streams(got)) is has_audio and "video" in streams(got)
    assert magenta(got) > 100                                              # exportação também leva o patrocinador
    # download direto autenticado (sem link) também passa pelo relay, com Range
    d = rig.client.get(f"/api/exports/{j['id']}/download", headers={**rig.H("a_user"), "Range": "bytes=0-99"})
    assert d.status_code == 206 and d.content == got.read_bytes()[:100]
    assert sorted(str(x) for x in storage.root().rglob("*") if x.is_file()) == rig.snapshot


def test_video_without_original_audio_still_refuses_original_and_mix(rig):
    for mode in ("original", "mix"):
        r = rig.client.post(f"/api/videos/{rig.ids['A_02']}/exports", headers=rig.H("a_user"), json={"audioMode": mode, "musicId": rig.world.A.tracks["ok"]})
        assert r.status_code == 422
    j = make_export(rig, "A_02", "music", musicId=rig.world.A.tracks["ok"])        # só música: permitido
    rig.render.run_one()
    got = download(rig, rig.client.get(f"/api/exports/{j['id']}", headers=rig.H("a_user")).json()["downloadUrl"])
    assert "audio" in streams(got)


def test_music_license_is_revalidated_when_the_agent_claims_the_job(rig):
    j = make_export(rig, "A_01", "music", musicId=rig.world.A.tracks["ok"])
    db = SessionLocal()
    t = db.get(MusicTrack, rig.world.A.tracks["ok"])
    t.license_valid_until = utcnow() - timedelta(days=1)                       # licença venceu antes da renderização
    db.commit()
    db.close()
    rig.render.run_one()
    r = rig.client.get(f"/api/exports/{j['id']}", headers=rig.H("a_user")).json()
    assert r["status"] == "failed" and "licença" in r["error"]
    assert not list((rig.tmp / "A" / "renders").glob("*.mp4"))                  # nada renderizado


def test_music_and_logos_of_other_arenas_are_refused(rig):
    r = rig.client.post(f"/api/videos/{rig.ids['A_01']}/exports", headers=rig.H("a_user"), json={"audioMode": "music", "musicId": rig.world.B.tracks["ok"]})
    assert r.status_code == 422


# ---------- lease / recuperação ----------
def test_abandoned_render_is_reassigned_after_lease_and_eventually_fails(rig):
    j = make_export(rig, "A_01", "mute")
    spy = CentralClient(rig.client, rig.world.A.token, "node-A")
    first = spy.call("GET", "/render/next")                                   # agente "cai" logo depois de reivindicar
    assert first["jobId"] == j["id"] and spy.call("GET", "/render/next") is None   # ninguém mais pega enquanto a concessão vale
    db = SessionLocal()
    db.query(ExportJob).filter_by(id=j["id"]).update({"lease_until": utcnow() - timedelta(seconds=1)})
    db.commit()
    db.close()
    assert rig.render.run_one()                                                 # concessão vencida: reatribuído e concluído
    db = SessionLocal()
    x = job(db, j["id"])
    assert x.status == "ready" and x.attempts == 2
    db.close()
    j2 = make_export(rig, "A_01", "original")
    db = SessionLocal()
    db.query(ExportJob).filter_by(id=j2["id"]).update({"status": "processing", "attempts": settings.render_max_attempts, "lease_until": utcnow() - timedelta(seconds=1)})
    db.commit()
    db.close()
    assert rig.render.run_one() is False
    assert rig.client.get(f"/api/exports/{j2['id']}", headers=rig.H("a_user")).json()["status"] == "failed"


def test_progress_renews_lease_and_complete_requires_claim(rig):
    j = make_export(rig, "A_01", "mute")
    c = CentralClient(rig.client, rig.world.A.token, "node-A")
    with pytest.raises(CentralRejected) as e:
        c.call("POST", f"/render/{j['id']}/complete", {"bytes": 10, "sha256": "0" * 64})
    assert e.value.status == 409                                                # não reivindicado
    c.call("GET", "/render/next")
    db = SessionLocal()
    db.query(ExportJob).filter_by(id=j["id"]).update({"lease_until": utcnow() + timedelta(seconds=5)})
    db.commit()
    db.close()
    c.call("POST", f"/render/{j['id']}/progress", {"progress": 40})
    db = SessionLocal()
    assert job(db, j["id"]).lease_until > utcnow() + timedelta(seconds=60)
    db.close()


def test_rendered_file_removed_on_arena_requeues_and_regenerates(rig):
    j = make_export(rig, "A_01", "mute")
    rig.render.run_one()
    url = rig.client.get(f"/api/exports/{j['id']}", headers=rig.H("a_user")).json()["downloadUrl"]
    (rig.tmp / "A" / "renders" / f"{j['id']}.mp4").unlink()
    r = rig.client.get(url)
    assert r.status_code == 503 and "novamente" in r.json()["detail"]
    db = SessionLocal()
    assert job(db, j["id"]).status == "queued"
    db.close()
    assert rig.render.run_one()
    new = rig.client.get(f"/api/exports/{j['id']}", headers=rig.H("a_user")).json()
    assert new["status"] == "ready" and download(rig, new["downloadUrl"]).stat().st_size > 0


# ---------- isolamento / escopo ----------
def test_render_endpoints_are_scoped_to_the_agents_arena(rig, world):
    j = make_export(rig, "A_01", "mute")
    b = CentralClient(rig.client, world.B.token, "node-B-0001")
    b.call("POST", "/register", {"nodeId": "node-B-0001"})
    assert b.call("GET", "/render/next") is None                                # B não vê jobs de A
    for path, body in ((f"/render/{j['id']}/complete", {"bytes": 1, "sha256": "0" * 64}), (f"/render/{j['id']}/fail", {"error": "x"}),
                       (f"/render/{j['id']}/progress", {"progress": 1})):
        with pytest.raises(CentralRejected) as e:
            b.call("POST", path, body)
        assert e.value.status == 404
    a = CentralClient(rig.client, world.A.token, "node-A")
    with pytest.raises(CentralRejected) as e:
        a.request_raw("GET", f"/render/{j['id']}/asset/logo0")
    assert e.value.status == 409                                                # antes de reivindicar não há acesso a recursos
    a.call("GET", "/render/next")
    for bad in ("logo9", "music", "x"):                                         # só o que o snapshot do job referencia
        with pytest.raises(CentralRejected) as e:
            a.request_raw("GET", f"/render/{j['id']}/asset/{bad}")
        assert e.value.status == 404
    with pytest.raises(CentralRejected) as e:
        b.request_raw("GET", f"/render/{j['id']}/asset/logo0")
    assert e.value.status == 404
    assert a.request_raw("GET", f"/render/{j['id']}/asset/logo0").content[:4] == b"\x89PNG"


def test_agent_refuses_unknown_sources_and_renders_nothing_for_them(rig):
    j = make_export(rig, "A_01", "mute")
    rig.agent.store._x("DELETE FROM replays WHERE local_id=?", (rig.src["A_01"][0],))   # a arena não conhece mais o replay
    rig.render.run_one()
    r = rig.client.get(f"/api/exports/{j['id']}", headers=rig.H("a_user")).json()
    assert r["status"] == "failed" and "não encontrado" in r["error"]


# ---------- sponsor / arena offline ----------
def test_campaign_change_after_render_blocks_old_version(rig):
    p = rig.client.post(f"/api/videos/{rig.ids['A_01']}/preview", headers=rig.H("a_user")).json()
    rig.render.run_one()
    url = rig.client.get(f"/api/previews/{p['id']}", headers=rig.H("a_user")).json()["url"]
    db = SessionLocal()
    db.query(SponsorCampaign).filter_by(arena_id=rig.world.A.id).update({"size_pct": 30})
    db.commit()
    db.close()
    assert rig.client.get(url).status_code == 409                               # versão antiga do patrocínio não circula


def test_offline_arena_ready_outputs_are_not_offered_and_new_renders_refused(rig):
    j = make_export(rig, "A_01", "mute")
    p = rig.client.post(f"/api/videos/{rig.ids['A_01']}/preview", headers=rig.H("a_user")).json()
    rig.render.run_one()
    rig.render.run_one()
    ok_url = rig.client.get(f"/api/exports/{j['id']}", headers=rig.H("a_user")).json()["downloadUrl"]
    rig.stop.set()
    for t in rig.threads:
        t.join(timeout=5)
    db = SessionLocal()
    for ag in db.query(ArenaAgent).filter_by(arena_id=rig.world.A.id):
        ag.last_used_at = ag.last_heartbeat_at = utcnow() - timedelta(minutes=10)
    db.commit()
    db.close()
    e = rig.client.get(f"/api/exports/{j['id']}", headers=rig.H("a_user")).json()
    assert e["status"] == "ready" and e["downloadUrl"] is None and e["available"] is False
    pv = rig.client.get(f"/api/previews/{p['id']}", headers=rig.H("a_user")).json()
    assert pv["status"] == "unavailable" and pv["url"] is None
    assert rig.client.get(ok_url).status_code == 503
    assert rig.client.post(f"/api/videos/{rig.ids['A_01']}/exports", headers=rig.H("a_user2"), json={"audioMode": "mute"}).status_code == 503
    assert rig.client.post(f"/api/videos/{rig.ids['A_01']}/preview", headers=rig.H("a_user2")).status_code in (200, 503)


# ---------- paridade com o worker central e convivência com a geração de replays ----------
@pytest.mark.parametrize("mode", ["original", "music", "mix", "mute"])
@pytest.mark.parametrize("overlays", [0, 2])
@pytest.mark.parametrize("codec,acodec", [("h264", "aac"), ("mpeg4", "mp3")])
def test_ffplan_matches_central_worker(mode, overlays, codec, acodec):
    ov = [("/l0.png", 400, 160, "top-left", 20, 1.0, None), ("/l1.png", 300, 300, "bottom-right", 15, 0.6, 3.5)][:overlays]
    m = (12.0, 1280, 720, codec, True, acodec)
    mine = ffplan.build_command(ffplan.ExportPlan("/s.mp4", "/o.mp4", ffplan.SourceMeta(*m), mode, "/m.mp3", 0.9, 0.5, [ffplan.Overlay(*o) for o in ov]))
    ref = wcmd(WPlan("/s.mp4", "/o.mp4", WMeta(*m), mode, "/m.mp3", 0.9, 0.5, [WOverlay(*o) for o in ov]))
    assert mine == ref


def test_rendering_does_not_starve_replay_generation_on_two_courts(rig):
    from replay_agent.render import SimRenderer
    rig.agent.renderer.delay_s = 0.05
    for i in range(6):
        for k in ("A_01", "A_02"):
            rig.agent.engine.trigger(k, T0 + timedelta(minutes=10 + i))
    j = make_export(rig, "A_01", "mute")
    t = threading.Thread(target=rig.render.run_one)
    t.start()
    assert rig.agent.engine.wait_idle(30)
    t.join(timeout=60)
    assert rig.agent.store.job_counts() == {"done": 12}
    assert rig.client.get(f"/api/exports/{j['id']}", headers=rig.H("a_user")).json()["status"] == "ready"


# ---------- música ATRIBUÍDA: reproduzida na versão derivada renderizada NA ARENA (topologia real: MP4 fica no servidor local) ----------
def _band_db(path, hz):
    af = f"bandpass=f={hz}:width_type=h:w=100,bandpass=f={hz}:width_type=h:w=100,volumedetect"
    r = subprocess.run([settings.ffmpeg_bin, "-v", "info", "-i", str(path), "-vn", "-af", af, "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"mean_volume: (-?[\d.]+|-inf) dB", r.stderr)
    return -200.0 if not m or m.group(1) == "-inf" else float(m.group(1))


def _music_arena(rig, track="ok", mode="music"):
    from app.models import Arena, SponsorCampaign
    db = SessionLocal()
    db.get(Arena, rig.world.A.id).audio_default_mode = mode
    for c in db.query(SponsorCampaign).filter_by(arena_id=rig.world.A.id):
        c.active = False                                   # isola a música (sem patrocínio)
    v = db.get(Video, rig.ids["A_01"]); v.music_id = rig.world.A.tracks[track]; v.music_reason = "rotation"
    db.commit(); db.close()


def test_assigned_music_is_rendered_on_the_arena_and_played_instead_of_the_original(rig):
    from conftest import MUSIC_HZ, ORIG_HZ
    src = rig.src["A_01"][1]; h0 = hashlib.sha256(src.read_bytes()).hexdigest()
    _music_arena(rig)
    assert rig.client.get(f"/api/videos/{rig.ids['A_01']}/play", headers=rig.H("a_user")).status_code == 409   # nunca o original cru
    a = rig.client.post(f"/api/videos/{rig.ids['A_01']}/preview", headers=rig.H("a_user")).json()
    b = rig.client.post(f"/api/videos/{rig.ids['A_01']}/preview", headers=rig.H("a_user2")).json()
    assert a["id"] == b["id"] and a["status"] == "queued"                                   # um único render compartilhado (sem duplicar)
    db = SessionLocal(); j = job(db, a["id"]); assert j.render_location == "agent" and j.audio_mode == "music" and j.music_id == rig.world.A.tracks["ok"]; db.close()
    assert runner.process_one() is False                                                    # o worker central não toca (MP4 só na arena)
    assert rig.render.run_one() is True and rig.render.run_one() is False
    st = rig.client.get(f"/api/previews/{a['id']}", headers=rig.H("a_user2")).json()
    assert st["status"] == "ready" and st["url"]
    got = download(rig, st["url"], "music-preview.mp4")
    assert _band_db(got, MUSIC_HZ) > -40 and _band_db(got, ORIG_HZ) < -60                     # o que o jogador ouve é a música atribuída
    assert hashlib.sha256(src.read_bytes()).hexdigest() == h0                                # MP4 original intacto
    assert sorted(str(x) for x in storage.root().rglob("*") if x.is_file()) == rig.snapshot   # a central não guardou cópia
    assert rig.client.get(f"/api/previews/{a['id']}", headers=rig.H("b_user")).status_code == 404   # outra arena não vê


def test_ambient_mode_and_invalid_track_play_the_original_and_failed_render_offers_ambient_fallback(rig):
    _music_arena(rig, mode="original")                                         # arena em som ambiente: a faixa registrada não toca
    assert rig.client.get(f"/api/videos/{rig.ids['A_01']}/play", headers=rig.H("a_user")).status_code == 200
    _music_arena(rig, track="expired")                                         # licença vencida: cai para o ambiente, sem erro
    assert rig.client.get(f"/api/videos/{rig.ids['A_01']}/play", headers=rig.H("a_user")).status_code == 200
    _music_arena(rig)                                                          # válida: derivada
    assert rig.client.get(f"/api/videos/{rig.ids['A_01']}/play?fallback=ambient", headers=rig.H("a_user")).status_code == 409   # sem falha de render não há atalho
    from app.models import MusicTrack
    db = SessionLocal(); db.get(MusicTrack, rig.world.A.tracks["ok"]).license_valid_until = utcnow() - timedelta(days=1); db.commit(); db.close()
    r = rig.client.get(f"/api/videos/{rig.ids['A_01']}/play", headers=rig.H("a_user"))
    assert r.status_code == 200                                                # licença venceu depois da atribuição: volta ao original (sem 409, sem erro)


def test_failed_music_render_allows_ambient_fallback_but_never_bypasses_sponsors(rig):
    _music_arena(rig)
    p = rig.client.post(f"/api/videos/{rig.ids['A_01']}/preview", headers=rig.H("a_user")).json()
    db = SessionLocal(); j = job(db, p["id"]); j.status, j.error = "failed", "falha simulada do render"; db.commit(); db.close()
    st = rig.client.get(f"/api/previews/{p['id']}", headers=rig.H("a_user")).json()
    assert st["status"] == "failed"
    assert rig.client.get(f"/api/videos/{rig.ids['A_01']}/play?fallback=ambient", headers=rig.H("a_user")).status_code == 200   # som ambiente
    from app.models import SponsorCampaign
    db = SessionLocal()
    for c in db.query(SponsorCampaign).filter_by(arena_id=rig.world.A.id):
        c.active = True
    db.commit(); db.close()
    assert rig.client.get(f"/api/videos/{rig.ids['A_01']}/play?fallback=ambient", headers=rig.H("a_user")).status_code == 409    # patrocínio não é contornado
