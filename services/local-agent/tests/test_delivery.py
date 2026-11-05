"""Entrega de vídeo por relay: HTTPS autenticado do agente (saída), links assinados, Range/seek, expiração, arena offline,
arquivo ausente, integridade e garantia de que a central não guarda vídeo."""
import hashlib
import random
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.config import settings
from app.db import SessionLocal, utcnow
from app.main import app
from app.models import ArenaAgent, RelayRequest, SponsorCampaign, Video
from replay_agent.client import CentralClient
from replay_agent.relay import RelayServer
from replay_agent.simulator import build_arena

T0 = datetime(2026, 11, 5, 12, 0, 0)
SIZE = 300_000


def blob(seed: int, n: int = SIZE) -> bytes:
    return random.Random(seed).randbytes(n)


class Rig:
    pass


@pytest.fixture
def rig(client, world, tmp_path, monkeypatch, H):
    monkeypatch.setattr(settings, "relay_window_bytes", 64 * 1024)   # vários trechos por arquivo
    monkeypatch.setattr(settings, "relay_timeout_s", 3)
    monkeypatch.setattr(settings, "relay_spool_dir", tmp_path / "spool")
    db = SessionLocal()
    for c in db.query(SponsorCampaign).filter_by(arena_id=world.A.id):
        c.active = False                                              # sem patrocínio: original liberado ao atleta
    db.commit()
    db.close()
    r = Rig()
    r.client, r.world, r.H, r.tmp = client, world, H, tmp_path
    r.agent = build_arena("A", world.A.token, ["A_01", "A_02"], client, root=tmp_path / "A")
    r.files, r.ids = {}, {}
    for i, k in enumerate(("A_01", "A_02")):
        lid = f"A-{k}-{1000 + i}"
        p = tmp_path / "A" / "replays" / k / f"{lid}.mp4"
        p.parent.mkdir(parents=True, exist_ok=True)
        data = blob(i + 1)
        p.write_bytes(data)
        r.agent.store.add_replay(lid, k, T0 + timedelta(seconds=i), str(p), len(data), 20.0, 1280, 720, True, "h264")
        r.files[k] = (lid, p, data)
    assert r.agent.link.tick()["online"]
    ha = H("a_admin_arena")
    db = SessionLocal()
    for k, (lid, _, _) in r.files.items():
        v = db.query(Video).filter_by(arena_id=world.A.id, local_id=lid).one()
        r.ids[k] = v.id
        client.patch(f"/api/arena/{world.A.id}/videos/{v.id}", headers=ha, json={"visibility": "arena"})
    db.close()
    r.stop = threading.Event()
    r.relay = RelayServer(CentralClient(TestClient(app), world.A.token, "node-A"), r.agent.store, [tmp_path / "A"])
    r.threads = []
    r.snapshot = sorted(str(p) for p in storage.root().rglob("*") if p.is_file())
    r.start = lambda n=2: r.threads.extend(r.relay.start(r.stop, workers=n, wait=1))
    yield r
    r.stop.set()
    for t in r.threads:
        t.join(timeout=5)
    r.agent.engine.stop()


def url_for(rig, k="A_01", who="a_user", kind="play"):
    path = {"play": f"/api/videos/{rig.ids[k]}/play"}[kind]
    r = rig.client.get(path, headers=rig.H(who))
    assert r.status_code == 200, r.text
    return r.json()["url"]


def link(rig, k="A_01", who="a_user"):
    r = rig.client.post(f"/api/videos/{rig.ids[k]}/download-link", headers=rig.H(who))
    assert r.status_code == 200, r.text
    return r.json()["url"]


# ---------- reprodução, seek e download ----------
def test_full_playback_streams_all_windows_and_matches_source(rig):
    rig.start()
    _, _, data = rig.files["A_01"]
    r = rig.client.get(url_for(rig))
    assert r.status_code == 200 and r.content == data
    assert r.headers["accept-ranges"] == "bytes" and int(r.headers["content-length"]) == SIZE
    assert r.headers["cache-control"] == "private, no-store" and r.headers["content-type"] == "video/mp4"
    assert rig.relay.served >= SIZE // settings.relay_window_bytes  # entregue em várias janelas


def test_range_requests_and_seek(rig):
    rig.start()
    data, u = rig.files["A_01"][2], url_for(rig)
    g = lambda rng: rig.client.get(u, headers={"Range": rng})  # noqa: E731
    r = g("bytes=100-199")
    assert r.status_code == 206 and r.content == data[100:200] and r.headers["content-range"] == f"bytes 100-199/{SIZE}"
    r = g("bytes=250000-")                                    # seek perto do fim, aberto: vai até o fim
    assert r.status_code == 206 and r.content == data[250000:] and r.headers["content-range"] == f"bytes 250000-{SIZE - 1}/{SIZE}"
    r = g("bytes=-500")                                       # sufixo (o Safari pede o final p/ ler o moov)
    assert r.status_code == 206 and r.content == data[-500:]
    r = g("bytes=0-")                                         # inline: janela limitada => o player continua pedindo
    assert r.status_code == 206 and r.content == data[:65536] and r.headers["content-range"] == f"bytes 0-65535/{SIZE}"
    r = g("bytes=150000-150000")
    assert r.status_code == 206 and r.content == data[150000:150001]
    assert g(f"bytes={SIZE + 5}-").status_code == 416 and g(f"bytes={SIZE + 5}-").headers["content-range"] == f"bytes */{SIZE}"
    assert g("bytes=abc").status_code == 416
    assert g("bytes=0-10,20-30").status_code == 200           # multi-range ignorado: resposta completa é válida


def test_seek_sequence_like_a_browser_reconstructs_the_file(rig):
    rig.start()
    data, u = rig.files["A_02"][2], None
    u = url_for(rig, "A_02")
    got, pos = bytearray(), 0
    while pos < SIZE:
        r = rig.client.get(u, headers={"Range": f"bytes={pos}-"})
        assert r.status_code == 206
        got += r.content
        pos += len(r.content)
    assert bytes(got) == data


def test_attachment_download_resumes_with_range_across_windows(rig):
    rig.start()
    data, u = rig.files["A_01"][2], link(rig)
    full = rig.client.get(u)
    assert full.status_code == 200 and full.content == data
    assert full.headers["content-disposition"].startswith('attachment; filename="replay-')
    part = rig.client.get(u, headers={"Range": "bytes=100000-"})   # retomada de download: vai até o fim, não só uma janela
    assert part.status_code == 206 and part.content == data[100000:]
    direct = rig.client.get(f"/api/videos/{rig.ids['A_01']}/download", headers=rig.H("a_user"))
    assert direct.status_code == 200 and direct.content == data


def test_real_mp4_through_relay_is_valid_and_seekable(rig, tmp_path):
    from replay_agent.render import SimRenderer
    out = tmp_path / "A" / "replays" / "A_01" / "real.mp4"
    SimRenderer(mode="ffmpeg", clip_seconds=3).render("A_01", out, 1, 1)
    lid = "A-A_01-real0001"
    rig.agent.store.add_replay(lid, "A_01", T0 + timedelta(minutes=5), str(out), out.stat().st_size, 3.0, 320, 180, True, "h264")
    rig.agent.link.tick()
    db = SessionLocal()
    vid = db.query(Video).filter_by(local_id=lid).one().id
    db.close()
    rig.client.patch(f"/api/arena/{rig.world.A.id}/videos/{vid}", headers=rig.H("a_admin_arena"), json={"visibility": "arena"})
    rig.start()
    u = rig.client.get(f"/api/videos/{vid}/play", headers=rig.H("a_user")).json()["url"]
    body = rig.client.get(u).content
    assert hashlib.sha256(body).hexdigest() == hashlib.sha256(out.read_bytes()).hexdigest()
    got = tmp_path / "got.mp4"
    got.write_bytes(body)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(got)], capture_output=True, text=True)
    assert probe.returncode == 0 and 1.5 < float(probe.stdout) < 2.5


def test_two_courts_stream_simultaneously(rig):
    rig.start(2)
    res, errs = {}, []

    def go(k):
        try:
            with TestClient(app) as c:
                res[k] = c.get(url_for(rig, k)).content, c.get(url_for(rig, k), headers={"Range": "bytes=70000-90000"}).content
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=go, args=(k,)) for k in ("A_01", "A_02")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    for k in ("A_01", "A_02"):
        data = rig.files[k][2]
        assert res[k][0] == data and res[k][1] == data[70000:90001]
    assert rig.files["A_01"][2] != rig.files["A_02"][2]


# ---------- links assinados / autorização ----------
def test_expired_tampered_and_revoked_links(rig, monkeypatch):
    rig.start()
    monkeypatch.setattr(settings, "signed_url_ttl_s", 1)
    short = url_for(rig)
    assert rig.client.get(short).status_code == 200
    time.sleep(2.2)
    assert rig.client.get(short).status_code == 403                     # expirado
    monkeypatch.setattr(settings, "signed_url_ttl_s", 300)
    ok = url_for(rig)
    assert rig.client.get(ok[:-3] + ("aaa" if not ok.endswith("aaa") else "bbb")).status_code == 403  # adulterado
    assert rig.client.get("/media/notatoken").status_code == 403
    # a autorização é REVALIDADA a cada requisição: gestor despublica => o link antigo morre
    rig.client.patch(f"/api/arena/{rig.world.A.id}/videos/{rig.ids['A_01']}", headers=rig.H("a_admin_arena"), json={"visibility": "private"})
    assert rig.client.get(ok).status_code == 404


def test_player_and_arena_scoping(rig):
    rig.start()
    vid = rig.ids["A_01"]
    assert rig.client.get(f"/api/videos/{vid}/play", headers=rig.H("b_user")).status_code == 404       # jogador de outra arena
    assert rig.client.get(f"/api/videos/{vid}/play", headers=rig.H("nobody")).status_code == 404
    assert rig.client.get(f"/api/arena/{rig.world.A.id}/videos/{vid}/play", headers=rig.H("b_admin_arena")).status_code == 404
    assert rig.client.get(f"/api/videos/{vid}/play").status_code == 401
    u = url_for(rig)
    # um link emitido na arena A não serve vídeo de outra arena: tokens referenciam o replay e o usuário
    other = rig.client.get(f"/api/videos/{rig.world.B.pub}/play", headers=rig.H("a_user"))
    assert other.status_code == 404
    assert rig.client.get(u).status_code == 200


def test_sponsor_rule_is_preserved_for_remote_videos(rig):
    rig.start()
    ok = url_for(rig)
    db = SessionLocal()
    db.query(SponsorCampaign).filter_by(arena_id=rig.world.A.id).update({"active": True})
    db.commit()
    db.close()
    assert rig.client.get(ok).status_code == 409                                        # link antigo não burla o patrocínio
    assert rig.client.get(f"/api/videos/{rig.ids['A_01']}/play", headers=rig.H("a_user")).status_code == 409
    assert rig.client.get(f"/api/videos/{rig.ids['A_01']}/download", headers=rig.H("a_user")).status_code == 409
    staff = rig.client.get(f"/api/arena/{rig.world.A.id}/videos/{rig.ids['A_01']}/play", headers=rig.H("a_admin_arena")).json()["url"]
    assert rig.client.get(staff).content == rig.files["A_01"][2]                        # equipe vê a origem (operação interna)


# ---------- arena offline / arquivo ausente / falhas ----------
def test_arena_offline_is_reported_unavailable_and_never_playable(rig):
    rig.start()
    u = url_for(rig)
    assert rig.client.get(u, headers={"Range": "bytes=0-9"}).status_code == 206
    rig.stop.set()
    for t in rig.threads:
        t.join(timeout=5)
    db = SessionLocal()
    for ag in db.query(ArenaAgent).filter_by(arena_id=rig.world.A.id):
        ag.last_used_at = ag.last_heartbeat_at = utcnow() - timedelta(minutes=10)
    db.commit()
    db.close()
    items = rig.client.get("/api/videos", headers=rig.H("a_user")).json()["items"]
    mine = [x for x in items if x["id"] in rig.ids.values()]
    assert mine and all(x["playable"] is False and x["availability"] == "arena_offline" and x["reason"] for x in mine)
    assert rig.client.get(f"/api/videos/{rig.ids['A_01']}", headers=rig.H("a_user")).json()["playable"] is False
    assert rig.client.get(f"/api/videos/{rig.ids['A_01']}/play", headers=rig.H("a_user")).status_code == 503
    assert rig.client.get(f"/api/videos/{rig.ids['A_01']}/download-link", headers=rig.H("a_user")).status_code in (404, 405)
    assert rig.client.post(f"/api/videos/{rig.ids['A_01']}/download-link", headers=rig.H("a_user")).status_code == 503
    r = rig.client.get(u)                                                                 # link emitido antes da queda
    assert r.status_code == 503 and r.headers.get("retry-after")
    # reconexão: heartbeat volta, relay volta, o mesmo vídeo volta a ser reproduzível
    rig.stop.clear()
    assert rig.agent.link.tick()["online"]
    rig.threads.clear()
    rig.start()
    assert rig.client.get(f"/api/videos/{rig.ids['A_01']}", headers=rig.H("a_user")).json()["playable"] is True
    assert rig.client.get(u).content == rig.files["A_01"][2]


def test_agent_reachable_but_relay_not_answering_times_out_cleanly(rig, monkeypatch):
    monkeypatch.setattr(settings, "relay_timeout_s", 1)
    u = url_for(rig)                                                                      # heartbeat fresco, mas ninguém serve
    r = rig.client.get(u)
    assert r.status_code == 503
    db = SessionLocal()
    assert db.query(RelayRequest).filter_by(status="expired").count() == 1
    db.close()
    assert not list(settings.relay_spool_dir.glob("*.bin"))


def test_missing_local_file_is_reported_and_recovers(rig):
    rig.start()
    lid, path, data = rig.files["A_01"]
    u = url_for(rig)
    path.unlink()                                                                         # alguém apagou o arquivo na arena
    assert rig.client.get(u).status_code == 404
    v = rig.client.get(f"/api/videos/{rig.ids['A_01']}", headers=rig.H("a_user")).json()
    assert v["playable"] is False and v["availability"] == "missing"
    assert rig.client.get(f"/api/videos/{rig.ids['A_01']}/play", headers=rig.H("a_user")).status_code == 404
    path.write_bytes(data)                                                                # arquivo restaurado
    assert rig.agent.link.tick()["online"]                                                # reconcilia e ressincroniza
    assert rig.client.get(f"/api/videos/{rig.ids['A_01']}", headers=rig.H("a_user")).json()["playable"] is True
    assert rig.client.get(url_for(rig)).content == data


def test_file_changed_on_disk_is_detected_and_not_served_as_old_metadata(rig):
    rig.start()
    _, path, data = rig.files["A_01"]
    u = url_for(rig)
    path.write_bytes(data[:200_000])                                                      # arquivo truncado
    r = rig.client.get(u)
    assert r.status_code == 409                                                           # metadado diverge: pede nova tentativa
    db = SessionLocal()
    assert db.query(Video).filter_by(id=rig.ids["A_01"]).one().bytes == 200_000           # auto-correção
    db.close()


def test_corrupted_chunk_is_rejected_by_hash(rig):
    stop, seen = threading.Event(), []

    def evil():
        c = CentralClient(TestClient(app), rig.world.A.token, "node-A")
        while not stop.is_set():
            job = c.call("GET", "/relay/next?wait=1")
            if job:
                seen.append(job)
                bad = b"\x00" * (job["end"] - job["start"] + 1)
                try:
                    c.request_raw("POST", f"/relay/{job['requestId']}/data", bad,
                                  {"X-Total-Size": str(SIZE), "X-Chunk-Sha256": hashlib.sha256(b"outro").hexdigest()})
                except Exception:  # noqa: BLE001
                    pass
    t = threading.Thread(target=evil, daemon=True)
    t.start()
    r = rig.client.get(url_for(rig))
    stop.set()
    t.join(timeout=5)
    assert seen and r.status_code == 503 and b"\x00" * 100 not in r.content[:200]
    assert not list(settings.relay_spool_dir.glob("*.bin"))


# ---------- autenticação nos endpoints do relay ----------
def test_relay_endpoints_are_scoped_to_the_agents_own_arena(rig, world):
    db = SessionLocal()
    r = RelayRequest(arena_id=world.A.id, kind="video", ref="A-A_01-1000", start=0, end=9, expires_at=utcnow() + timedelta(seconds=60))
    db.add(r)
    db.commit()
    rid = r.id
    db.close()
    other = CentralClient(rig.client, world.B.token, "node-B-0001")
    other.call("POST", "/register", {"nodeId": "node-B-0001"})
    assert other.call("GET", "/relay/next?wait=0") is None                                 # B não enxerga pedidos de A
    from replay_agent.client import CentralRejected
    with pytest.raises(CentralRejected) as e:
        other.request_raw("POST", f"/relay/{rid}/data", b"x" * 10, {"X-Total-Size": "10", "X-Chunk-Sha256": hashlib.sha256(b"x" * 10).hexdigest()})
    assert e.value.status == 404
    mine = CentralClient(rig.client, world.A.token, "node-A")
    job = mine.call("GET", "/relay/next?wait=0")
    assert job["requestId"] == rid
    with pytest.raises(CentralRejected) as e:
        other.call("POST", f"/relay/{rid}/error", {"code": "not_found"})
    assert e.value.status == 404
    for who in ("a_user", "a_admin_arena", "root"):
        assert rig.client.get("/api/agent/v1/relay/next?wait=0", headers={**rig.H(who), "X-Agent-Node": "x"}).status_code == 401


def test_agent_only_serves_files_it_knows_and_inside_allowed_roots(rig, tmp_path):
    srv = rig.relay
    assert srv._resolve("video", "../../etc/passwd") is None
    assert srv._resolve("video", "A-A_01-9999999") is None
    secret = tmp_path / "outside.mp4"
    secret.write_bytes(b"x" * 100)
    rig.agent.store._x("UPDATE replays SET path=? WHERE local_id=?", (str(secret), rig.files["A_01"][0]))   # banco adulterado
    assert srv._resolve("video", rig.files["A_01"][0]) is None
    assert srv._resolve("weird", rig.files["A_02"][0]) is None


# ---------- sem armazenamento permanente na central ----------
def test_central_never_keeps_video_bytes(rig):
    rig.start()
    for k in ("A_01", "A_02"):
        rig.client.get(url_for(rig, k))
        rig.client.get(link(rig, k))
        rig.client.get(url_for(rig, k), headers={"Range": "bytes=1000-2000"})
    after = sorted(str(p) for p in storage.root().rglob("*") if p.is_file())
    assert after == rig.snapshot                                                          # STORAGE_DIR intocado
    assert not list(settings.relay_spool_dir.glob("*"))                                    # spool esvaziado
    db = SessionLocal()
    states = {r.status for r in db.query(RelayRequest)}
    assert states <= {"consumed", "expired"}                                              # nada fica aguardando/guardado
    assert db.query(Video).filter(Video.storage_location == "agent", Video.storage_key != "").count() == 0
    db.close()


def test_full_journey_offline_capture_reconnect_then_playable(client, world, tmp_path, monkeypatch, H):
    """Arena fica sem internet, continua gerando replays (duas quadras); ao voltar, os metadados sincronizam sozinhos
    e o jogador passa a ver e baixar os vídeos — que continuam só no disco da arena."""
    from replay_agent.render import SimRenderer
    monkeypatch.setattr(settings, "relay_spool_dir", tmp_path / "spool")
    db = SessionLocal()
    db.query(SponsorCampaign).filter_by(arena_id=world.A.id).update({"active": False})
    db.commit()
    db.close()
    ar = build_arena("A", world.A.token, ["A_01", "A_02"], client, root=tmp_path / "A", renderer=SimRenderer(mode="ffmpeg", clip_seconds=2), dedup_s=0)
    assert ar.link.tick()["online"]
    ar.http.down = True                                                   # sem internet na arena
    for k in ("A_01", "A_02"):
        ar.engine.trigger(k, T0)                                          # acionamentos simultâneos nas duas quadras
    assert ar.engine.wait_idle(60) and not ar.link.tick()["online"]
    assert ar.store.count() == 2 and ar.store.count("pending") == 2
    ar.http.down = False                                                  # voltou
    assert ar.link.tick()["online"] and ar.store.count("pending") == 0
    srv = RelayServer(CentralClient(TestClient(app), world.A.token, "node-A"), ar.store, [tmp_path / "A"])
    stop = threading.Event()
    threads = srv.start(stop, workers=2, wait=1)
    try:
        db = SessionLocal()
        vids = db.query(Video).filter(Video.arena_id == world.A.id, Video.local_id.like("A-A_0%-%")).all()
        assert len(vids) == 2
        for v in vids:
            client.patch(f"/api/arena/{world.A.id}/videos/{v.id}", headers=H("a_admin_arena"), json={"visibility": "arena"})
            assert client.get(f"/api/videos/{v.id}", headers=H("a_user")).json()["playable"] is True
            url = client.get(f"/api/videos/{v.id}/play", headers=H("a_user")).json()["url"]
            got = client.get(url).content
            assert got == Path(ar.store.get(v.local_id)["path"]).read_bytes()
        db.close()
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=5)
        ar.engine.stop()
