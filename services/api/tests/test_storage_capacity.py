"""Limites por arena/quadra, previsão de esgotamento, retenção (somente dry-run) e proteção de vídeos."""
from datetime import timedelta

from app import capacity
from app.db import SessionLocal, utcnow
from app.models import Arena, AuditLog, Video

MB = capacity.MB
BASE = "/api/agent/v1"


def ah(w, tag="A"):
    return {"Authorization": f"Bearer {getattr(w, tag).token}", "X-Agent-Node": f"node-{tag}-0001"}


def setup_agent(client, w, tag="A"):
    assert client.post(f"{BASE}/register", headers=ah(w, tag), json={"nodeId": f"node-{tag}-0001"}).status_code == 200


def put_replays(client, w, n, tag="A", court="A_01", size=MB, age_days=0, prefix="st"):
    at = (utcnow() - timedelta(days=age_days)).isoformat() + "Z"
    items = [{"localId": f"{prefix}-{tag}-{age_days:03d}{i:06d}", "courtKey": court, "capturedAt": at, "durationS": 20, "bytes": size} for i in range(n)]
    r = client.post(f"{BASE}/sync/replays", headers=ah(w, tag), json={"replays": items})
    assert r.status_code == 200, r.text
    return [x["videoId"] for x in r.json()["results"]]


# ---------- previsão ----------
def test_forecast_linear_growth_and_edge_cases():
    t0 = utcnow() - timedelta(days=4)
    pts = [(t0 + timedelta(days=d), d * 10 * MB) for d in range(5)]  # +10 MB/dia, usado = 40 MB
    f = capacity.forecast_days_to_full(pts, 100 * MB)
    assert f["status"] == "growing" and abs(f["daysToFull"] - 6.0) < 0.05 and abs(f["bytesPerDay"] - 10 * MB) < 1000
    assert capacity.forecast_days_to_full(pts, None)["status"] == "no_limit"
    assert capacity.forecast_days_to_full(pts[:2], 100 * MB)["status"] == "insufficient_data"
    assert capacity.forecast_days_to_full([(t0, 5 * MB), (t0 + timedelta(hours=2), 5 * MB), (t0 + timedelta(hours=4), 5 * MB)], 100 * MB)["status"] == "stable"
    assert capacity.forecast_days_to_full([(p[0], 50 * MB + p[1]) for p in pts], 90 * MB)["status"] == "full"
    shrinking = [(t, 90 * MB - i * MB) for i, (t, _) in enumerate(pts)]
    assert capacity.forecast_days_to_full(shrinking, 100 * MB)["daysToFull"] is None
    assert capacity.forecast_days_to_full([(t0, 1), (t0 + timedelta(minutes=5), 2), (t0 + timedelta(minutes=9), 3)], 100)["status"] == "insufficient_data"


def test_usage_levels():
    assert capacity.usage_level(10, None) == "unlimited"
    assert [capacity.usage_level(u, 100) for u in (10, 80, 95, 100)] == ["ok", "warning", "critical", "full"]
    assert capacity.usage_level(70, 100, 60, 90) == "warning"


def test_sample_throttle(client, world):
    db = SessionLocal()
    now = utcnow()
    assert capacity.record_sample(db, world.A.id, None, 1, None, None, now) is True
    db.commit()
    assert capacity.record_sample(db, world.A.id, None, 2, None, None, now + timedelta(seconds=5)) is False
    assert capacity.record_sample(db, world.A.id, None, 3, None, None, now + timedelta(seconds=3600)) is True
    db.close()


# ---------- limites ----------
def test_effective_limit_plan_vs_override_and_permissions(client, world, H):
    # plano de teste: 1024 MB
    st = client.get(f"/api/arena/{world.A.id}/storage", headers=H("a_admin_arena")).json()
    assert st["limitBytes"] == 1024 * MB and st["limitSource"] == "plan" and st["autoDelete"] is False
    # a arena NÃO altera o limite global: sem rota; o portal altera e audita
    assert client.put(f"/api/arena/{world.A.id}/storage-limit", headers=H("a_admin_arena"), json={"storageLimitMb": 99999}).status_code in (404, 405)
    assert client.put(f"/api/platform/arenas/{world.A.id}/storage-limit", headers=H("a_admin_arena"), json={"storageLimitMb": 500}).status_code in (401, 403)
    r = client.put(f"/api/platform/arenas/{world.A.id}/storage-limit", headers=H("root"), json={"storageLimitMb": 500})
    assert r.status_code == 200 and r.json()["limitBytes"] == 500 * MB and r.json()["limitSource"] == "arena"
    db = SessionLocal()
    assert db.query(AuditLog).filter_by(action="platform.storage_limit", arena_id=world.A.id).count() == 1
    db.close()
    # a arena B não é afetada
    assert client.get(f"/api/arena/{world.B.id}/storage", headers=H("b_admin_arena")).json()["limitBytes"] == 1024 * MB
    # remover override volta ao plano
    assert client.put(f"/api/platform/arenas/{world.A.id}/storage-limit", headers=H("root"), json={"storageLimitMb": None}).json()["limitSource"] == "plan"


def test_court_limit_policy_validation_and_idor(client, world, H):
    ha, hb = H("root"), H("b_admin_arena")
    url = f"/api/arena/{world.A.id}/courts/{world.A.c1}/storage-policy"
    assert client.put(url, headers=ha, json={"storageLimitMb": 400, "retentionDays": 10}).status_code == 200
    assert client.put(url, headers=ha, json={"storageLimitMb": 5000, "retentionDays": 10}).status_code == 422  # > limite da arena
    assert client.put(url, headers=H("a_op_arena"), json={"storageLimitMb": 400}).status_code == 403  # operador não altera política
    assert client.put(url, headers=hb, json={"storageLimitMb": 400}).status_code == 404  # outra arena
    # quadra de B pela URL de A
    assert client.put(f"/api/arena/{world.A.id}/courts/{world.B.c1}/storage-policy", headers=ha, json={"storageLimitMb": 200}).status_code == 404
    assert client.put(f"/api/arena/{world.A.id}/storage-policy", headers=ha, json={"warnPct": 90, "criticalPct": 80}).status_code == 422
    st = client.put(f"/api/arena/{world.A.id}/storage-policy", headers=ha, json={"warnPct": 50, "criticalPct": 70}).json()
    assert (st["warnPct"], st["criticalPct"]) == (50, 70)
    c1 = next(c for c in st["courts"] if c["courtId"] == world.A.c1)
    assert c1["limitBytes"] == 400 * MB and c1["retentionDays"] == 10
    assert client.get(f"/api/arena/{world.B.id}/storage", headers=hb).json()["warnPct"] == 80


def test_usage_levels_reflect_synced_metadata_and_config_reaches_agent(client, world, H):
    setup_agent(client, world)
    client.put(f"/api/platform/arenas/{world.A.id}/storage-limit", headers=H("root"), json={"storageLimitMb": 100})
    client.put(f"/api/arena/{world.A.id}/courts/{world.A.c1}/storage-policy", headers=H("root"), json={"storageLimitMb": 60, "retentionDays": 7})
    base = client.get(f"/api/arena/{world.A.id}/storage", headers=H("a_admin_arena")).json()["usedBytes"]  # vídeos semeados pela fixture
    put_replays(client, world, 82 - base // MB, size=MB)  # ~82 MB de 100 => warning (80%); A_01 acima de 60 MB => full
    st = client.get(f"/api/arena/{world.A.id}/storage", headers=H("a_admin_arena")).json()
    assert st["level"] == "warning" and st["usedBytes"] > 80 * MB
    assert next(c for c in st["courts"] if c["courtId"] == world.A.c1)["level"] == "full"
    cfg = client.get(f"{BASE}/config", headers=ah(world)).json()
    assert cfg["storage"]["arenaLimitBytes"] == 100 * MB and cfg["storage"]["autoDelete"] is False
    c = next(x for x in cfg["courts"] if x["courtKey"] == "A_01")
    assert c["storageLimitBytes"] == 60 * MB and c["retentionDays"] == 7
    sync_level = client.post(f"{BASE}/sync/replays", headers=ah(world), json={"replays": []}).json()["storageLevel"]
    assert sync_level == "warning"
    # configVersion muda quando a política muda (agentes detectam pelo heartbeat)
    hb = client.post(f"{BASE}/heartbeat", headers=ah(world), json={"configVersion": cfg["configVersion"]}).json()
    assert hb["configChanged"] is False
    client.put(f"/api/arena/{world.A.id}/courts/{world.A.c1}/storage-policy", headers=H("root"), json={"storageLimitMb": 50, "retentionDays": 7})
    assert client.post(f"{BASE}/heartbeat", headers=ah(world), json={"configVersion": cfg["configVersion"]}).json()["configChanged"] is True


def test_forecast_endpoint_from_heartbeat_samples(client, world, H):
    setup_agent(client, world)
    db = SessionLocal()
    t0 = utcnow() - timedelta(days=3)
    from app.models import StorageSample
    for d in range(4):
        db.add(StorageSample(arena_id=world.A.id, court_id=None, ts=t0 + timedelta(days=d), used_bytes=d * 100 * MB,
                             disk_free_bytes=(1000 - d * 100) * MB, disk_total_bytes=1000 * MB))
    db.commit()
    db.close()
    st = client.get(f"/api/arena/{world.A.id}/storage", headers=H("a_admin_arena")).json()
    assert st["forecast"]["status"] == "growing" and abs(st["forecast"]["daysToFull"] - 7.24) < 0.2  # (1024-300)/100
    assert st["diskForecast"]["status"] == "growing" and abs(st["diskForecast"]["daysToFull"] - 7.0) < 0.2


# ---------- retenção / proteção ----------
def test_retention_plan_is_dry_run_and_respects_protection(client, world, H):
    setup_agent(client, world)
    old = put_replays(client, world, 3, age_days=60)
    put_replays(client, world, 2, age_days=1)
    ha = H("root")
    assert client.post(f"/api/arena/{world.A.id}/videos/{old[0]}/protection", headers=ha, json={"protected": True}).json()["protected"] is True
    plan = client.get(f"/api/arena/{world.A.id}/retention/plan", headers=ha).json()
    ids = {c["videoId"] for c in plan["candidates"]}
    assert ids == set(old[1:]) and plan["deleteEnabled"] is False and plan["dryRun"] is True and plan["protectedSkipped"] == 1
    assert all(c["reason"] == "expired" for c in plan["candidates"])
    # o mesmo plano é entregue ao agente (também só candidatos)
    assert {c["videoId"] for c in client.get(f"{BASE}/retention/plan", headers=ah(world)).json()["candidates"]} == ids
    # nada foi alterado: linhas e flags intactas
    db = SessionLocal()
    assert db.query(Video).filter(Video.local_id.like("st-A-%"), Video.deleted_at.is_not(None)).count() == 0
    db.close()
    # a lista de proteção chega ao agente
    assert client.get(f"{BASE}/sync/protection", headers=ah(world)).json()["localIds"] == [f"st-A-060{0:06d}"]
    # desproteger exige admin e é auditado
    assert client.post(f"/api/arena/{world.A.id}/videos/{old[0]}/protection", headers=H("a_op_arena"), json={"protected": False}).status_code == 403
    client.post(f"/api/arena/{world.A.id}/videos/{old[0]}/protection", headers=ha, json={"protected": False})
    db = SessionLocal()
    assert {r.action for r in db.query(AuditLog).filter(AuditLog.arena_id == world.A.id)} >= {"video.protect", "video.unprotect"}
    db.close()


def test_retention_pressure_reclaims_oldest_unprotected_until_below_warn(client, world, H):
    setup_agent(client, world)
    client.put(f"/api/platform/arenas/{world.A.id}/storage-limit", headers=H("root"), json={"storageLimitMb": 100})
    ids = put_replays(client, world, 5, age_days=5, size=20 * MB, prefix="pa") + put_replays(client, world, 1, age_days=2, size=2 * MB, prefix="pb")
    ha = H("root")
    client.post(f"/api/arena/{world.A.id}/videos/{ids[0]}/protection", headers=ha, json={"protected": True})
    plan = client.get(f"/api/arena/{world.A.id}/retention/plan", headers=ha).json()  # usado 102 MB de 100 => full
    assert plan["level"] == "full" and ids[0] not in {c["videoId"] for c in plan["candidates"]}
    assert all(c["reason"] == "pressure" for c in plan["candidates"])
    freed = sum(c["bytes"] for c in plan["candidates"])
    assert 102 * MB - freed <= 80 * MB  # alvo = limiar de aviso (80%)
    assert len(plan["candidates"]) == 2  # 2 × 20 MB bastam, parando assim que cruza o alvo


def test_protection_idor(client, world, H):
    setup_agent(client, world)
    vid = put_replays(client, world, 1)[0]
    assert client.post(f"/api/arena/{world.B.id}/videos/{vid}/protection", headers=H("b_admin_arena"), json={"protected": True}).status_code in (403, 404)
    assert client.post(f"/api/arena/{world.A.id}/videos/{vid}/protection", headers=H("b_admin_arena"), json={"protected": True}).status_code == 404
    assert client.post(f"/api/arena/{world.A.id}/videos/{vid}/protection", headers=H("a_user"), json={"protected": True}).status_code in (401, 403)


def test_existing_prune_previews_still_dry_run_by_default(client, world):
    from app.retention import prune_previews
    db = SessionLocal()
    assert prune_previews(db, days=7)["dry_run"] is True
    db.close()


def test_platform_sees_all_arenas_but_arena_sees_only_own(client, world, H):
    cap = client.get("/api/platform/capacity", headers=H("root")).json()
    assert {a["arenaId"] for a in cap["arenas"]} == {world.A.id, world.B.id}
    assert client.get("/api/platform/capacity", headers=H("a_admin_arena")).status_code in (401, 403)
    assert client.get(f"/api/platform/arenas/{world.A.id}/capacity", headers=H("root")).status_code == 200
    db = SessionLocal()
    assert db.get(Arena, world.A.id).storage_limit_mb is None
    db.close()
