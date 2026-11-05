"""Contrato Central <-> motor local: registro, heartbeat, sincronização idempotente, isolamento, fronteiras e fencing."""
import threading
from datetime import timedelta

from fastapi.testclient import TestClient

from app.capacity import connection_state
from app.config import settings
from app.db import SessionLocal, utcnow
from app.main import app
from app.models import ArenaAgent, Video

BASE = "/api/agent/v1"


def ah(w, tag="A", node=None):
    h = {"Authorization": f"Bearer {getattr(w, tag).token}"}
    if node is not False:
        h["X-Agent-Node"] = node or f"node-{tag}-0001"
    return h


def reg(client, w, tag="A", node=None):
    r = client.post(f"{BASE}/register", headers=ah(w, tag, node), json={"nodeId": node or f"node-{tag}-0001", "hostname": "h", "courts": [f"{tag}_01"]})
    assert r.status_code == 200, r.text
    return r.json()


def item(lid, court="A_01", **kw):
    return {"localId": lid, "courtKey": court, "capturedAt": "2026-11-04T15:00:00Z", "durationS": 20, "bytes": 1000, "hasAudio": True, **kw}


def sync(client, w, items, tag="A"):
    return client.post(f"{BASE}/sync/replays", headers=ah(w, tag), json={"replays": items})


# ---------- registro ----------
def test_register_binds_token_to_first_node(client, world):
    r = reg(client, world)
    assert r["arenaId"] == world.A.id and r["config"]["configVersion"] and r["unknownCourts"] == []
    assert reg(client, world)["agentId"] == r["agentId"]  # idempotente para o mesmo computador
    clone = client.post(f"{BASE}/register", headers=ah(world, "A", "node-clone-0001"), json={"nodeId": "node-clone-0001"})
    assert clone.status_code == 409  # token copiado para outro computador
    assert client.post(f"{BASE}/heartbeat", headers=ah(world, "A", "node-clone-0001"), json={}).status_code == 409


def test_unregistered_or_unbound_calls_rejected(client, world):
    assert client.post(f"{BASE}/heartbeat", headers=ah(world), json={}).status_code == 409  # nunca registrou
    reg(client, world)
    assert client.post(f"{BASE}/heartbeat", headers=ah(world, node=False), json={}).status_code == 409  # sem X-Agent-Node
    assert client.post(f"{BASE}/heartbeat", json={}).status_code == 401
    bad = {"Authorization": "Bearer rag_nope", "X-Agent-Node": "node-A-0001"}
    assert client.post(f"{BASE}/heartbeat", headers=bad, json={}).status_code == 401


def test_register_reports_unknown_courts_and_is_audited_once(client, world, H):
    r = client.post(f"{BASE}/register", headers=ah(world), json={"nodeId": "node-A-0001", "courts": ["A_01", "B_01", "X"]}).json()
    assert r["unknownCourts"] == ["B_01", "X"]  # quadra de outra arena é desconhecida
    reg(client, world)
    logs = client.get(f"/api/arena/{world.A.id}/audit", headers=H("a_admin_arena")).json()
    assert sum(1 for x in logs if x["action"] == "agent.register") == 1


# ---------- fronteiras ----------
def test_agent_api_never_accepts_files():
    spec = TestClient(app).get("/api/openapi.json").json()
    for path, ops in spec["paths"].items():
        if path.startswith(BASE):
            for op in ops.values():
                assert "multipart/form-data" not in str(op.get("requestBody", {})), path


def test_other_credentials_cannot_use_agent_api_and_vice_versa(client, world, H):
    reg(client, world)
    for who in ("a_user", "a_admin_arena", "root"):
        r = client.post(f"{BASE}/heartbeat", headers={**H(who), "X-Agent-Node": "node-A-0001"}, json={})
        assert r.status_code == 401, who
    agent = ah(world)
    for url in (f"/api/arena/{world.A.id}/storage", f"/api/arena/{world.A.id}/runtime", "/api/platform/capacity", "/api/videos"):
        assert client.get(url, headers=agent).status_code in (401, 403), url


# ---------- sincronização idempotente ----------
def test_sync_is_idempotent_and_metadata_only(client, world):
    reg(client, world)
    batch = [item("sim-A-0000000001"), item("sim-A-0000000002", "A_02")]
    r1 = sync(client, world, batch).json()["results"]
    assert [x["status"] for x in r1] == ["created", "created"]
    r2 = sync(client, world, batch).json()["results"]
    assert [x["status"] for x in r2] == ["duplicate", "duplicate"] and [x["videoId"] for x in r2] == [x["videoId"] for x in r1]
    db = SessionLocal()
    vids = db.query(Video).filter(Video.local_id.like("sim-A-%")).all()
    assert len(vids) == 2 and all(v.storage_location == "agent" and v.storage_key == "" and v.visibility == "private" for v in vids)
    assert {v.court_id for v in vids} == {world.A.c1, world.A.c2}
    db.close()


def test_concurrent_identical_syncs_create_no_duplicates(world):
    with TestClient(app) as c0:
        reg(c0, world)
    batch = [item(f"sim-A-conc{i:07d}") for i in range(10)]
    out, errs = [], []

    def go():
        try:
            with TestClient(app) as c:
                out.append(sync(c, world, batch))
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and all(r.status_code == 200 for r in out)
    db = SessionLocal()
    assert db.query(Video).filter(Video.local_id.like("sim-A-conc%")).count() == 10
    assert len({x["videoId"] for r in out for x in r.json()["results"]}) == 10
    db.close()


def test_sync_rejects_bad_items_without_blocking_the_batch(client, world):
    reg(client, world)
    res = sync(client, world, [item("sim-A-ok00000001"), item("sim-A-bad0000001", "B_01"), item("sim-A-bad0000002", "NOPE")]).json()["results"]
    assert [x["status"] for x in res] == ["created", "rejected", "rejected"]
    assert client.post(f"{BASE}/sync/replays", headers=ah(world), json={"replays": [item("x")]}).status_code == 422  # id curto
    assert client.post(f"{BASE}/sync/replays", headers=ah(world), json={"replays": [item("sim-A-neg0000001", bytes=-1)]}).status_code == 422


def test_batch_limit(client, world, monkeypatch):
    reg(client, world)
    monkeypatch.setattr(settings, "agent_sync_batch_max", 2)
    assert sync(client, world, [item(f"sim-A-lim{i:07d}") for i in range(3)]).status_code == 413


def test_same_local_id_cannot_jump_between_courts(client, world):
    reg(client, world)
    sync(client, world, [item("sim-A-move000001", "A_01")])
    r = sync(client, world, [item("sim-A-move000001", "A_02")]).json()["results"][0]
    assert r["status"] == "rejected"


# ---------- isolamento multiarena ----------
def test_same_local_id_in_two_arenas_stays_isolated(client, world):
    reg(client, world, "A")
    reg(client, world, "B")
    ra = sync(client, world, [item("shared-id-0001", "A_01", protected=True)], "A").json()["results"][0]
    rb = sync(client, world, [item("shared-id-0001", "B_01")], "B").json()["results"][0]
    assert ra["status"] == rb["status"] == "created" and ra["videoId"] != rb["videoId"]
    assert client.get(f"{BASE}/sync/protection", headers=ah(world, "B")).json()["localIds"] == []  # proteção de A não vaza
    assert client.get(f"{BASE}/sync/protection", headers=ah(world, "A")).json()["localIds"] == ["shared-id-0001"]
    db = SessionLocal()
    assert {db.get(Video, ra["videoId"]).arena_id, db.get(Video, rb["videoId"]).arena_id} == {world.A.id, world.B.id}
    db.close()


def test_suspended_arena_agent_blocked(client, world, H):
    reg(client, world)
    assert client.patch(f"/api/platform/arenas/{world.A.id}", headers=H("root"), json={"status": "suspended"}).status_code == 200
    assert client.post(f"{BASE}/heartbeat", headers=ah(world), json={}).status_code == 403
    assert sync(client, world, [item("sim-A-susp000001")]).status_code == 403


# ---------- heartbeat / estado de conexão ----------
def test_connection_state_machine():
    a = ArenaAgent(arena_id="x", label="l", token_hash="h", active=True)
    now = utcnow()
    assert connection_state(a, now) == "never_connected"
    hb = settings.agent_heartbeat_s
    for age, expected in ((hb, "online"), (hb * 2, "online"), (hb * 3, "delayed"), (hb * 6, "delayed"), (hb * 7, "offline")):
        a.last_heartbeat_at = now - timedelta(seconds=age)
        assert connection_state(a, now) == expected, age
    a.active = False
    assert connection_state(a, now) == "revoked"


def test_heartbeat_updates_runtime_visible_only_to_own_arena(client, world, H):
    reg(client, world)
    body = {"version": "9.9", "configVersion": "old", "pendingSync": 3, "system": {"cpuPct": 12.5, "diskFreeBytes": 10**9, "diskTotalBytes": 10**10},
            "courts": [{"courtKey": "A_01", "queueDepth": 2, "completed": 5, "failed": 1, "avgRenderS": 1.5, "p95RenderS": 2.0, "state": "degraded"},
                       {"courtKey": "B_01", "queueDepth": 9}]}
    r = client.post(f"{BASE}/heartbeat", headers=ah(world), json=body).json()
    assert r["configChanged"] is True and r["unknownCourts"] == ["B_01"] and r["epoch"] == settings.central_epoch
    rt = client.get(f"/api/arena/{world.A.id}/runtime", headers=H("a_admin_arena")).json()
    assert rt["connection"] == "online" and rt["agents"][0]["system"]["cpuPct"] == 12.5 and rt["agents"][0]["pendingSync"] == 3
    c1 = next(c for c in rt["courts"] if c["courtId"] == world.A.c1)
    assert (c1["queueDepth"], c1["failed"], c1["state"]) == (2, 1, "degraded")
    assert client.get(f"/api/arena/{world.A.id}/runtime", headers=H("a_op_arena")).status_code == 200  # operador lê
    assert client.get(f"/api/arena/{world.A.id}/runtime", headers=H("b_admin_arena")).status_code == 404  # outra arena: IDOR
    assert client.get(f"/api/arena/{world.A.id}/runtime", headers=H("a_user")).status_code in (401, 403)
    cap = client.get("/api/platform/capacity", headers=H("root")).json()
    a = next(x for x in cap["arenas"] if x["arenaId"] == world.A.id)
    assert a["connection"] == "online" and a["queueDepth"] == 2 and a["courtsDegraded"] == 1


def test_legacy_heartbeat_marks_online(client, world, H):
    client.post("/api/ingest/heartbeat", headers={"Authorization": f"Bearer {world.A.token}"}, json={"version": "1", "pendingSync": 0})
    assert client.get(f"/api/arena/{world.A.id}/runtime", headers=H("a_admin_arena")).json()["connection"] == "online"


# ---------- redundância da central (fencing) ----------
def test_standby_node_refuses_agent_writes_but_reports_role(client, world, monkeypatch):
    reg(client, world)
    monkeypatch.setattr(settings, "node_role", "standby")
    for url, body in (("/heartbeat", {}), ("/sync/replays", {"replays": []}), ("/register", {"nodeId": "node-A-0001"})):
        r = client.post(BASE + url, headers=ah(world), json=body)
        assert r.status_code == 503 and r.headers["x-central-role"] == "standby", url
    st = client.get(f"{BASE}/status", headers=ah(world)).json()
    assert st["role"] == "standby"


def test_stale_primary_is_fenced_by_epoch(client, world, monkeypatch):
    reg(client, world)
    monkeypatch.setattr(settings, "central_epoch", 3)
    h = {**ah(world), "X-Agent-Epoch": "4"}  # o agente já viu um primário mais novo
    r = client.post(f"{BASE}/sync/replays", headers=h, json={"replays": [item("sim-A-fence000001")]})
    assert r.status_code == 409 and "epoch obsoleto" in r.text
    db = SessionLocal()
    assert db.query(Video).filter_by(local_id="sim-A-fence000001").count() == 0  # nada foi gravado
    db.close()
    assert client.post(f"{BASE}/sync/replays", headers={**ah(world), "X-Agent-Epoch": "3"}, json={"replays": []}).status_code == 200


# ---------- compatibilidade com o legado ----------
def test_legacy_video_upload_can_be_disabled(client, world, media, monkeypatch):
    monkeypatch.setattr(settings, "allow_agent_video_upload", False)
    r = client.post("/api/ingest/videos", headers={"Authorization": f"Bearer {world.A.token}"},
                    data={"idempotencyKey": "evt-legacy-001", "courtKey": "A_01"}, files={"file": ("r.mp4", media["audio"].read_bytes(), "video/mp4")})
    assert r.status_code == 410


def test_agent_hosted_video_is_not_offered_as_playable_while_arena_is_unreachable(client, world, H):
    reg(client, world)
    vid = sync(client, world, [item("sim-A-host000001")]).json()["results"][0]["videoId"]
    db = SessionLocal()
    for ag in db.query(ArenaAgent).filter_by(arena_id=world.A.id):
        ag.last_used_at = ag.last_heartbeat_at = utcnow() - timedelta(hours=1)  # arena offline
    db.commit()
    db.close()
    ha = H("a_admin_arena")
    lst = client.get(f"/api/arena/{world.A.id}/videos", headers=ha).json()
    v = next(x for x in lst if x["id"] == vid)
    assert v["playable"] is False and v["availability"] == "arena_offline"
    assert client.get(f"/api/arena/{world.A.id}/videos/{vid}/play", headers=ha).status_code == 503  # sem link para vídeo inacessível
    client.patch(f"/api/arena/{world.A.id}/videos/{vid}", headers=ha, json={"visibility": "arena"})
    assert client.get(f"/api/videos/{vid}", headers=H("a_user")).json()["playable"] is False
    assert client.post(f"/api/videos/{vid}/download-link", headers=H("a_user")).status_code in (409, 503)  # patrocinado ou offline
