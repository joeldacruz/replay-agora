from app.db import SessionLocal
from app.models import ReplayEvent, Video


def agent(w, tag="A"):
    return {"Authorization": f"Bearer {getattr(w, tag).token}"}


def ev(key, court="A_01", at="2026-11-04T15:00:00Z", **kw):
    return {"courtKey": court, "idempotencyKey": key, "capturedAt": at, **kw}


def upload(client, w, media, key, court="A_01", tag="A", src="audio", name="r.mp4", content=None):
    data = content if content is not None else media[src].read_bytes()
    return client.post("/api/ingest/videos", headers=agent(w, tag), data={"idempotencyKey": key, "courtKey": court},
                       files={"file": (name, data, "video/mp4")})


def test_event_idempotent_and_dedup_window(client, world):
    h = agent(world)
    r1 = client.post("/api/ingest/events", headers=h, json=ev("evt-0000001")).json()
    r2 = client.post("/api/ingest/events", headers=h, json=ev("evt-0000001")).json()  # retry com a mesma chave
    assert r1["eventId"] == r2["eventId"] and r2["duplicate"] is True
    # outro acionamento (chave diferente) a 1 s do primeiro: é descartado como duplicado do botão
    r3 = client.post("/api/ingest/events", headers=h, json=ev("evt-0000002", at="2026-11-04T15:00:01Z")).json()
    assert r3["status"] == "duplicate" and r3["duplicateOf"] == r1["eventId"]
    # 30 s depois é um novo replay legítimo
    r4 = client.post("/api/ingest/events", headers=h, json=ev("evt-0000003", at="2026-11-04T15:00:30Z")).json()
    assert r4["status"] == "pending" and not r4["duplicate"]
    db = SessionLocal()
    assert db.query(ReplayEvent).filter(ReplayEvent.idempotency_key.like("evt-%")).count() == 3
    db.close()


def test_two_simulated_courts_have_separate_events_and_videos(client, world, media):
    h = agent(world)
    # mesmo instante nas duas quadras: não pode haver mistura nem deduplicação cruzada
    e1 = client.post("/api/ingest/events", headers=h, json=ev("evt-q1-0001", "A_01")).json()
    e2 = client.post("/api/ingest/events", headers=h, json=ev("evt-q2-0001", "A_02")).json()
    assert e1["courtId"] == world.A.c1 and e2["courtId"] == world.A.c2 and e1["eventId"] != e2["eventId"]
    assert e2["status"] == "pending"
    v1 = upload(client, world, media, "evt-q1-0001", "A_01").json()
    v2 = upload(client, world, media, "evt-q2-0001", "A_02").json()
    db = SessionLocal()
    a, b = db.get(Video, v1["videoId"]), db.get(Video, v2["videoId"])
    assert (a.court_id, b.court_id) == (world.A.c1, world.A.c2) and a.arena_id == b.arena_id == world.A.id
    assert a.event_id == e1["eventId"] and b.event_id == e2["eventId"] and a.visibility == "private"
    assert a.has_audio and a.thumb_key and a.duration > 2
    db.close()
    # arena B recebe na quadra de B; mesma chave de idempotência em arena diferente não colide
    eb = client.post("/api/ingest/events", headers=agent(world, "B"), json=ev("evt-q1-0001", "B_01")).json()
    assert eb["courtId"] == world.B.c1 and not eb["duplicate"]


def test_video_upload_idempotent_and_wrong_court(client, world, media):
    client.post("/api/ingest/events", headers=agent(world), json=ev("evt-up-0001"))
    first = upload(client, world, media, "evt-up-0001").json()
    again = upload(client, world, media, "evt-up-0001")
    assert again.status_code == 201 and again.json()["videoId"] == first["videoId"] and again.json()["duplicate"] is True
    db = SessionLocal()
    assert db.query(Video).filter(Video.event_id.isnot(None), Video.title == "", Video.arena_id == world.A.id, Video.code == first["code"]).count() == 1
    db.close()
    assert upload(client, world, media, "evt-up-0001", "A_02").status_code == 409  # evento é da quadra 1
    assert upload(client, world, media, "evt-up-0009", "ZZZ_99").status_code == 404


def test_upload_without_prior_event_and_dup_event_rejected(client, world, media):
    assert upload(client, world, media, "evt-solo-001").status_code == 201  # agente offline que envia depois
    client.post("/api/ingest/events", headers=agent(world), json=ev("evt-dup-a001", at="2026-11-04T16:00:00Z"))
    client.post("/api/ingest/events", headers=agent(world), json=ev("evt-dup-b001", at="2026-11-04T16:00:01Z"))
    assert upload(client, world, media, "evt-dup-b001").status_code == 409


def test_malicious_or_invalid_video_uploads(client, world, media):
    assert upload(client, world, media, "evt-bad-0001", content=b"#!/bin/sh\nrm -rf /\n").status_code == 422
    assert upload(client, world, media, "evt-bad-0002", content=b"\x00\x00\x00\x18ftypmp42" + b"lixo" * 200).status_code == 422
    assert upload(client, world, media, "evt-bad-0003", name="x.php", content=media["audio"].read_bytes()).status_code == 422
    assert upload(client, world, media, "evt-bad-0004", name="../../etc/passwd.mp4").status_code == 201  # nome do cliente é ignorado
    db = SessionLocal()
    assert all("/" not in v.storage_key.replace("arenas/", "", 1).split("/", 1)[1].replace("videos/", "") for v in db.query(Video))
    db.close()


def test_video_without_audio_flagged(client, world, media):
    r = upload(client, world, media, "evt-mute-001", src="mute").json()
    db = SessionLocal()
    assert db.get(Video, r["videoId"]).has_audio is False
    db.close()


def test_suspended_arena_agent_blocked_and_heartbeat(client, world, H):
    assert client.post("/api/ingest/heartbeat", headers=agent(world), json={"version": "1.2.3", "pendingSync": 4}).status_code == 200
    d = client.get(f"/api/arena/{world.A.id}/dashboard", headers=H("a_admin_arena")).json()
    assert d["agentOnline"] is True
    ag = client.get(f"/api/arena/{world.A.id}/agents", headers=H("a_admin_arena")).json()[0]
    assert ag["version"] == "1.2.3" and ag["pendingSync"] == 4
    root = H("root")
    assert client.patch(f"/api/platform/arenas/{world.A.id}", headers=root, json={"status": "suspended"}).status_code == 200
    assert client.post("/api/ingest/events", headers=agent(world), json=ev("evt-susp-001")).status_code == 403


# ---------------- Portal do Desenvolvedor ----------------
def test_platform_arena_lifecycle_and_audit(client, world, H):
    r = H("root")
    new = client.post("/api/platform/arenas", headers=r, json={"slug": "arena-nova", "name": "Arena Nova", "planId": world.plan_id,
                                                               "adminEmail": world.emails["nobody"]})
    assert new.status_code == 201 and new.json()["admins"][0]["email"] == world.emails["nobody"]
    assert client.post("/api/platform/arenas", headers=r, json={"slug": "arena-nova", "name": "Outra"}).status_code == 409
    assert client.post("/api/platform/arenas", headers=r, json={"slug": "Slug Invalido!", "name": "X"}).status_code == 422
    assert client.post("/api/platform/arenas", headers=r, json={"slug": "tz-ruim", "name": "TZ", "timezone": "Marte/Olimpo"}).status_code == 422
    aid = new.json()["id"]
    # o novo gestor entra no app da arena e vê somente a arena nova
    from conftest import login
    ha = login(client, world.emails["nobody"], "arena")
    assert [m["arenaId"] for m in client.get("/api/auth/arena/me", headers=ha).json()["memberships"]] == [aid]
    assert client.get(f"/api/arena/{world.A.id}", headers=ha).status_code == 404
    # suspensão bloqueia gestor e atletas imediatamente
    assert client.patch(f"/api/platform/arenas/{aid}", headers=r, json={"status": "suspended"}).json()["status"] == "suspended"
    assert client.get(f"/api/arena/{aid}", headers=ha).status_code == 403
    assert client.patch(f"/api/platform/arenas/{aid}", headers=r, json={"status": "active"}).status_code == 200
    assert client.get(f"/api/arena/{aid}", headers=ha).status_code == 200
    actions = [e["action"] for e in client.get(f"/api/platform/audit?arenaId={aid}", headers=r).json()]
    assert "platform.arena_create" in actions and "platform.arena_suspend" in actions


def test_platform_plan_limits_apply_to_arena(client, world, H):
    r = H("root")
    plan = client.post("/api/platform/plans", headers=r, json={"name": "Mini", "maxCourts": 2, "maxStorageMb": 100, "maxExportsPerUserDay": 1}).json()
    client.patch(f"/api/platform/arenas/{world.A.id}", headers=r, json={"planId": plan["id"]})
    ha = H("a_admin_arena")
    assert client.post(f"/api/arena/{world.A.id}/courts", headers=ha, json={"label": "Q3", "localCourtKey": "A_03"}).status_code == 409
    uh = H("a_user")
    assert client.post(f"/api/videos/{world.A.pub}/exports", headers=uh, json={"audioMode": "mute"}).status_code == 200
    assert client.post(f"/api/videos/{world.A.pub}/exports", headers=uh, json={"audioMode": "original"}).status_code == 429


def test_platform_users_and_self_lockout(client, world, H):
    r, uh = H("root"), H("a_user")
    users = client.get("/api/platform/users?q=a_user", headers=r).json()
    assert {u["email"] for u in users} >= {world.emails["a_user"]}
    assert client.patch(f"/api/platform/users/{world.uid['a_user']}", headers=r, json={"active": False}).status_code == 200
    assert client.get("/api/videos", headers=uh).status_code == 401  # token existente deixa de valer
    assert client.patch(f"/api/platform/users/{world.uid['root']}", headers=r, json={"active": False}).status_code == 409
    assert client.patch(f"/api/platform/users/{world.uid['root']}", headers=r, json={"globalRole": "none"}).status_code == 409


def test_platform_status_agents_and_no_private_media_access(client, world, H):
    r = H("root")
    s = client.get("/api/platform/status", headers=r).json()
    assert s["arenas"]["active"] == 2 and s["videos"] == 6 and s["agents"]["total"] == 2
    assert all("token" not in a for a in client.get("/api/platform/agents", headers=r).json())
    # global_admin não reproduz mídia privada por padrão: sem rota e sem acesso via app do usuário/arena
    paths = client.get("/api/openapi.json").json()["paths"]
    assert not [p for p in paths if p.startswith("/api/platform") and ("video" in p or "play" in p or "download" in p)]
    assert client.get(f"/api/videos/{world.A.pub}", headers=r).status_code == 403
    assert client.get(f"/api/arena/{world.A.id}/videos/{world.A.pub}/play", headers=r).status_code == 403


def test_platform_assign_admin_audited(client, world, H):
    r = H("root")
    assert client.post(f"/api/platform/arenas/{world.B.id}/administrators", headers=r, json={"email": world.emails["nobody"], "role": "arena_admin"}).status_code == 201
    assert client.post(f"/api/platform/arenas/{world.B.id}/administrators", headers=r, json={"email": world.emails["nobody"], "role": "global_admin"}).status_code == 422
    log = client.get(f"/api/platform/audit?arenaId={world.B.id}", headers=r).json()
    assert any(e["action"] == "platform.admin_assign" for e in log)
