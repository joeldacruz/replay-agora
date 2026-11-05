"""Motor local <-> API central real (in-process): queda, reconexão, idempotência, failover e isolamento multiarena."""
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from app import storage
from app.config import settings
from app.db import SessionLocal
from app.models import Video
from replay_agent.render import SimRenderer
from replay_agent.simulator import Fleet, build_arena

T0 = datetime(2026, 11, 5, 12, 0, 0)


def arena(client, world, tag, tmp_path, keys=None, **kw):
    t = getattr(world, tag)
    return build_arena(tag, t.token, keys or [f"{tag}_01", f"{tag}_02"], client, root=tmp_path / tag,
                       renderer=SimRenderer(size_bytes=50_000), **kw)


def fire(a, n=1, start=0):
    for k in a.court_keys:
        for i in range(n):
            assert a.engine.trigger(k, T0 + timedelta(seconds=10 * (start + i)))["accepted"]
    assert a.engine.wait_idle(20)


def central_videos(prefix=None):
    db = SessionLocal()
    q = db.query(Video).filter(Video.local_id.isnot(None))
    rows = [v for v in q.all() if not prefix or v.local_id.startswith(prefix)]
    db.close()
    return rows


def storage_files():
    return sorted(str(p) for p in storage.root().rglob("*") if p.is_file())


def test_replays_sync_as_metadata_only_and_central_gets_no_video_files(client, world, tmp_path):
    a = arena(client, world, "A", tmp_path)
    before = storage_files()
    fire(a, n=2)
    r = a.link.tick()
    assert r["online"] and r["synced"] == 4 and r["pending"] == 0 and a.link.state == "online"
    vids = central_videos("A-")
    assert len(vids) == 4 and all(v.storage_location == "agent" and v.storage_key == "" and v.arena_id == world.A.id for v in vids)
    assert storage_files() == before                                   # a central não recebeu nenhum arquivo
    assert len(list((tmp_path / "A").rglob("*.mp4"))) == 4              # os originais continuam na arena
    assert a.link.tick()["synced"] == 0 and len(central_videos("A-")) == 4  # ciclo repetido: sem duplicar
    fleet = Fleet([a]); fleet.stop()


def test_local_operation_continues_during_outage_and_resyncs_once(client, world, tmp_path):
    a = arena(client, world, "A", tmp_path)
    a.link.tick()                                                       # registra e baixa a config
    a.http.down = True                                                  # API fora do ar
    fire(a, n=3)
    t = a.link.tick()
    assert t["online"] is False and a.link.state == "offline" and a.store.count("pending") == 6
    assert a.link.fail_streak == 1 and a.link.backoff_s() > 2
    fire(a, n=2, start=10)                                              # segue gerando replays localmente
    a.link.tick()
    assert a.store.count() == 10 and a.store.count("pending") == 10 and not central_videos("A-")
    a.http.down = False                                                 # reconexão
    r = a.link.tick()
    assert r["online"] and r["synced"] == 10 and a.store.count("pending") == 0 and a.link.fail_streak == 0
    assert len(central_videos("A-")) == 10
    assert a.link.tick()["synced"] == 0 and len(central_videos("A-")) == 10
    a.engine.stop()


def test_lost_response_after_server_commit_does_not_duplicate(client, world, tmp_path):
    a = arena(client, world, "A", tmp_path)
    a.link.tick()
    fire(a, n=3)
    a.http.lose_responses, a.http.lose_match = 1, "/sync/replays"                                           # servidor grava, agente não recebe resposta
    assert a.link.tick()["online"] is False
    assert len(central_videos("A-")) == 6 and a.store.count("pending") == 6   # central tem; agente ainda não sabe
    r = a.link.tick()
    assert r["online"] and a.store.count("pending") == 0 and len(central_videos("A-")) == 6
    a.engine.stop()


def test_agent_started_while_central_is_down_works_from_cached_config(client, world, tmp_path):
    a = arena(client, world, "A", tmp_path)
    a.link.tick()
    cfg_ver = a.link.config_version
    a.engine.stop(); a.store.close()
    b = arena(client, world, "A", tmp_path)                             # reinício da arena com o mesmo disco
    b.http.down = True
    assert b.engine.court_cfg and b.link.tick()["online"] is False
    fire(b)
    assert b.store.count() == 2
    b.http.down = False
    assert b.link.tick()["online"] and len(central_videos("A-")) == 2
    assert cfg_ver
    b.engine.stop()


def test_standby_then_promotion_and_failover_resend_is_idempotent(client, world, tmp_path, monkeypatch):
    a = arena(client, world, "A", tmp_path)
    fire(a, n=2)
    assert a.link.tick()["online"]
    n_before = len(central_videos("A-"))
    # failover: o principal cai; o reserva responde como standby (não grava)
    monkeypatch.setattr(settings, "node_role", "standby")
    fire(a, n=1, start=50)
    r = a.link.tick()
    assert r["online"] is False and "503" in r["error"] and len(central_videos("A-")) == n_before
    # reserva assume com epoch maior
    monkeypatch.setattr(settings, "node_role", "primary")
    monkeypatch.setattr(settings, "central_epoch", settings.central_epoch + 1)
    r = a.link.tick()
    assert r["online"] and a.link.client.epoch == settings.central_epoch
    assert a.store.count("pending") == 0
    # o agente reenviou a janela recente (replicação pode ter perdido escritas) sem duplicar nada
    assert len(central_videos("A-")) == n_before + 2
    a.engine.stop()


def test_agent_ignores_stale_primary_after_failover(client, world, tmp_path, monkeypatch):
    a = arena(client, world, "A", tmp_path)
    monkeypatch.setattr(settings, "central_epoch", 5)
    assert a.link.tick()["online"] and a.link.client.epoch == 5
    fire(a)
    monkeypatch.setattr(settings, "central_epoch", 4)                   # o antigo principal volta "achando" que manda
    r = a.link.tick()
    assert r["online"] is False and "obsoleto" in r["error"]
    assert not central_videos("A-")                                     # nada foi gravado no nó obsoleto
    monkeypatch.setattr(settings, "central_epoch", 5)
    assert a.link.tick()["online"] and len(central_videos("A-")) == 2
    a.engine.stop()


def test_two_arenas_four_courts_concurrently_stay_isolated(client, world, tmp_path):
    fa, fb = arena(client, world, "A", tmp_path), arena(client, world, "B", tmp_path)
    fleet = Fleet([fa, fb])
    for a in fleet.arenas:
        a.renderer.delay_s = 0.15
    errs = []

    def run(a):
        try:
            for r in range(3):
                for k in a.court_keys:
                    a.engine.trigger(k, T0 + timedelta(seconds=10 * r))
            a.engine.wait_idle(30)
            a.link.tick()
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=run, args=(a,)) for a in fleet.arenas]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    va, vb = central_videos("A-"), central_videos("B-")
    assert len(va) == len(vb) == 6
    assert {v.arena_id for v in va} == {world.A.id} and {v.arena_id for v in vb} == {world.B.id}
    assert {v.court_id for v in va} == {world.A.c1, world.A.c2} and {v.court_id for v in vb} == {world.B.c1, world.B.c2}
    fleet.stop()


def test_misconfigured_agent_cannot_write_into_another_arena(client, world, tmp_path):
    a = arena(client, world, "A", tmp_path, keys=["A_01", "B_01"])      # agente de A "conhece" uma quadra de B
    fire(a)
    a.link.tick()
    assert [v.court_id for v in central_videos("A-")] == [world.A.c1]
    assert not central_videos("B-") and a.store.count("rejected") == 1 and a.store.count("pending") == 0
    a.engine.stop()


def test_central_protection_reaches_local_retention_filter(client, world, tmp_path, H):
    a = arena(client, world, "A", tmp_path)
    a.link.tick()
    old = datetime.utcnow() - timedelta(days=90)
    for i in range(3):
        a.engine.trigger("A_01", old + timedelta(seconds=10 * i))
    a.engine.wait_idle(10)
    a.link.tick()
    ids = [v for v in central_videos("A-")]
    ids.sort(key=lambda v: v.local_id)
    assert client.post(f"/api/arena/{world.A.id}/videos/{ids[0].id}/protection", headers=H("a_admin_arena"), json={"protected": True}).status_code == 200
    a.link.tick()
    cands = a.store.retention_candidates("A_01", 30)
    assert ids[0].local_id not in cands and len(cands) == 2
    assert len(list((tmp_path / "A").rglob("*.mp4"))) == 3
    a.engine.stop()


def test_heartbeat_reports_court_metrics_and_failures_to_central(client, world, tmp_path, H):
    a = arena(client, world, "A", tmp_path)
    a.renderer.camera_up["A_02"] = False
    fire(a, n=2)
    a.link.tick()
    rt = client.get(f"/api/arena/{world.A.id}/runtime", headers=H("a_admin_arena")).json()
    by = {c["courtId"]: c for c in rt["courts"]}
    assert by[world.A.c1]["completed"] == 2 and by[world.A.c1]["state"] == "ok"
    assert by[world.A.c2]["failed"] == 2 and by[world.A.c2]["state"] == "camera_down"
    assert rt["agents"][0]["system"]["diskTotalBytes"] and rt["agents"][0]["state"] == "online"
    st = client.get(f"/api/arena/{world.A.id}/storage", headers=H("a_admin_arena")).json()
    assert any(c["courtId"] == world.A.c1 for c in st["courts"])
    a.engine.stop()


def test_agent_token_revoked_stops_sync_but_not_local_capture(client, world, tmp_path, H):
    a = arena(client, world, "A", tmp_path)
    a.link.tick()
    ag = client.get(f"/api/arena/{world.A.id}/agents", headers=H("a_admin_arena")).json()[0]
    client.delete(f"/api/arena/{world.A.id}/agents/{ag['id']}", headers=H("a_admin_arena"))
    fire(a)
    r = a.link.tick()
    assert r["online"] is False and a.link.state == "rejected"
    assert a.store.count() == 2 and not central_videos("A-")
    a.engine.stop()
