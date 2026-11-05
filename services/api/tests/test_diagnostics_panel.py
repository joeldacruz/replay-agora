"""Painel de diagnóstico da arena: dados, alertas, isolamento, permissões e teste manual de acionamento (comando via heartbeat)."""
from datetime import timedelta

from app.db import SessionLocal, utcnow
from app.models import AgentCommand, ArenaAgent, AuditLog, CourtRuntime
from replay_agent.client import CentralClient, CentralRejected

BASE = "/api/agent/v1"
SECRET = "rtsp://admin:S3nh4@10.0.0.11/s1"


def agent(client, world, tag="A"):
    c = CentralClient(client, getattr(world, tag).token, f"node-{tag}-0001")
    c.call("POST", "/register", {"nodeId": f"node-{tag}-0001"})
    return c


def diag_payload(key="A_01"):
    return {"courts": {key: {"label": "Quadra 1", "camera": {"state": "streaming", "fps": 24.8, "declaredFps": 25, "lossPct": 0.4, "bitrateKbps": 1800,
                                                            "reconnects": 2, "url": "rtsp://***@10.0.0.11/s1", "lastError": None,
                                                            "buffer": {"availableSeconds": 96.0, "targetSeconds": 120, "segments": 48, "gaps": 0}},
                                    "trigger": {"state": "connected", "last": {"at": 1.0, "ageS": 30, "source": "serial:b1", "button": key, "outcome": "accepted"}},
                                    "queue": {"queued": 1, "processing": 0, "done": 7, "failed": 0, "unrecoverable": 1, "oldestWaitS": 3.2}}},
            "resources": {"maxFfmpeg": 1, "activeFfmpeg": 0, "hw": {"active": "cpu", "setting": "none"}}, "link": {"state": "online", "pending": 0},
            "triggerSources": [{"kind": "serial", "state": "connected", "port": "ttyUSB0"}],
            "alerts": [{"code": "unrecoverable", "severity": "warning", "court": key, "message": "1 acionamento(s) sem vídeo recuperável"}], "version": "x"}


def beat(c, key="A_01", **over):
    body = {"courts": [{"courtKey": key, "state": "ok", "queueDepth": 1, "completed": 7, "failed": 0, "avgRenderS": 1.2}],
            "system": {"cpuPct": 31.5, "memUsedPct": 40, "diskFreeBytes": 5 * 10**10, "diskTotalBytes": 10**11}, "diagnostics": diag_payload(key)}
    body.update(over)
    return c.call("POST", "/heartbeat", body)


def test_panel_shows_courts_cameras_buffer_queue_alerts_and_sync_state(client, world, H):
    c = agent(client, world)
    beat(c)
    r = client.get(f"/api/arena/{world.A.id}/diagnostics", headers=H("a_admin_arena"))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["connection"] == "online" and d["stale"] is False and d["agent"]["pendingSync"] == 0 and d["agent"]["system"]["cpuPct"] == 31.5
    c1 = next(x for x in d["courts"] if x["courtId"] == world.A.c1)
    assert c1["diag"]["camera"]["fps"] == 24.8 and c1["diag"]["camera"]["buffer"]["availableSeconds"] == 96.0 and c1["diag"]["queue"]["unrecoverable"] == 1
    assert c1["stale"] is False and c1["storage"]["level"] in ("ok", "unlimited", "warning")
    c2 = next(x for x in d["courts"] if x["courtId"] == world.A.c2)
    assert c2["diag"] is None and c2["stale"] is True                                          # quadra sem telemetria: aparece como sem dados
    assert d["agent"]["link"]["state"] == "online" and d["agent"]["resources"]["hw"]["active"] == "cpu"
    assert {a["code"] for a in d["alerts"]} >= {"unrecoverable", "unsigned_agent"} and all("severity" in a for a in d["alerts"])
    assert d["storage"]["limitBytes"] and "forecast" in d["storage"]
    assert "S3nh4" not in r.text and "admin:" not in r.text                                       # nenhum segredo de câmera no painel
    assert client.get(f"/api/arena/{world.A.id}/diagnostics", headers=H("a_op_arena")).status_code == 200      # operador lê


def test_panel_flags_offline_stale_storage_and_outdated_config(client, world, H):
    d0 = client.get(f"/api/arena/{world.A.id}/diagnostics", headers=H("a_admin_arena")).json()
    assert d0["connection"] == "never_connected" and any(a["code"] == "agent_never_connected" and a["severity"] == "critical" for a in d0["alerts"])
    c = agent(client, world)
    beat(c, **{"configVersion": "antiga"})
    client.put(f"/api/platform/arenas/{world.A.id}/storage-limit", headers=H("root"), json={"storageLimitMb": 100})
    h = H("a_admin_arena")
    d = client.get(f"/api/arena/{world.A.id}/diagnostics", headers=h).json()
    assert any(a["code"].startswith("storage_") for a in d["alerts"]) or d["storage"]["level"] == "ok"
    db = SessionLocal()
    for ag in db.query(ArenaAgent).filter_by(arena_id=world.A.id):
        ag.last_heartbeat_at = ag.last_used_at = utcnow() - timedelta(minutes=30)
    for rt in db.query(CourtRuntime).filter_by(arena_id=world.A.id):
        rt.updated_at = utcnow() - timedelta(minutes=30)
    db.commit()
    db.close()
    d = client.get(f"/api/arena/{world.A.id}/diagnostics", headers=h).json()
    assert d["connection"] == "offline" and d["stale"] is True and all(x["stale"] for x in d["courts"])
    assert any(a["code"] == "agent_offline" for a in d["alerts"])
    assert not any(a.get("source") == "agent" for a in d["alerts"])                              # alertas do agente velho não são exibidos como atuais


def test_panel_is_isolated_between_arenas_and_closed_to_other_apps(client, world, H):
    agent(client, world, "A"); agent(client, world, "B")
    beat(agent(client, world, "A"), "A_01")
    assert client.get(f"/api/arena/{world.A.id}/diagnostics", headers=H("b_admin_arena")).status_code == 404       # IDOR
    for who in ("a_user", "nobody"):
        assert client.get(f"/api/arena/{world.A.id}/diagnostics", headers=H(who)).status_code in (401, 403)
    assert client.get(f"/api/arena/{world.A.id}/diagnostics").status_code == 401
    db_b = client.get(f"/api/arena/{world.B.id}/diagnostics", headers=H("b_admin_arena")).json()
    assert all(x["diag"] is None for x in db_b["courts"])                                          # a telemetria de A não vaza para B
    assert client.get(f"/api/arena/{world.A.id}/diagnostics", headers={"Authorization": f"Bearer {world.A.token}"}).status_code == 401   # token de agente


def test_agent_cannot_push_diagnostics_for_courts_of_other_arenas(client, world, H):
    c = agent(client, world, "A")
    body = {"courts": [{"courtKey": "B_01", "state": "ok"}], "diagnostics": diag_payload("B_01")}
    r = c.call("POST", "/heartbeat", body)
    assert r["unknownCourts"] == ["B_01"]
    db = SessionLocal()
    assert db.query(CourtRuntime).filter_by(court_id=world.B.c1).count() == 0
    db.close()


def test_manual_test_trigger_roundtrip_through_heartbeat(client, world, H):
    c = agent(client, world)
    beat(c)
    ha, url = H("a_admin_arena"), f"/api/arena/{world.A.id}/courts/{world.A.c1}/test-trigger"
    r = client.post(url, headers=H("a_op_arena"))
    assert r.status_code == 202, r.text                                                          # operador no local também pode testar
    cid = r.json()["commandId"]
    assert client.post(url, headers=ha).status_code == 409                                       # um teste por vez na quadra
    assert client.get(f"/api/arena/{world.A.id}/commands/{cid}", headers=ha).json()["status"] == "pending"
    hb = beat(c)                                                                                  # o agente busca no heartbeat (conexão de saída)
    assert [x["id"] for x in hb["commands"]] == [cid] and hb["commands"][0]["courtKey"] == "A_01" and hb["commands"][0]["by"].startswith("painel-")
    assert beat(c)["commands"] == []                                                              # entregue uma única vez
    assert client.get(f"/api/arena/{world.A.id}/commands/{cid}", headers=ha).json()["status"] == "delivered"
    c.call("POST", f"/commands/{cid}/result", {"outcome": "accepted", "jobId": "A-A_01-1"})
    done = client.get(f"/api/arena/{world.A.id}/commands/{cid}", headers=ha).json()
    assert done["status"] == "done" and done["outcome"] == "accepted"
    with __import__("pytest").raises(CentralRejected) as e:
        c.call("POST", f"/commands/{cid}/result", {"outcome": "accepted"})
    assert e.value.status == 409
    db = SessionLocal()
    assert db.query(AuditLog).filter_by(action="agent.test_trigger", arena_id=world.A.id).count() == 1
    db.close()


def test_test_trigger_guards_offline_scope_expiry_and_other_agents(client, world, H):
    ha = H("a_admin_arena")
    assert client.post(f"/api/arena/{world.A.id}/courts/{world.A.c1}/test-trigger", headers=ha).status_code == 503      # agente offline
    ca, cb = agent(client, world, "A"), agent(client, world, "B")
    beat(ca)
    assert client.post(f"/api/arena/{world.A.id}/courts/{world.B.c1}/test-trigger", headers=ha).status_code == 404       # quadra de outra arena
    assert client.post(f"/api/arena/{world.B.id}/courts/{world.B.c1}/test-trigger", headers=ha).status_code == 404       # não é membro de B
    assert client.post(f"/api/arena/{world.A.id}/courts/{world.A.c1}/test-trigger", headers=H("a_user")).status_code in (401, 403)
    assert client.post(f"/api/arena/{world.A.id}/courts/{world.A.c1}/test-trigger").status_code == 401
    cid = client.post(f"/api/arena/{world.A.id}/courts/{world.A.c1}/test-trigger", headers=ha).json()["commandId"]
    assert beat(cb, "B_01")["commands"] == []                                                     # o agente de B não recebe comandos de A
    with __import__("pytest").raises(CentralRejected) as e:
        cb.call("POST", f"/commands/{cid}/result", {"outcome": "accepted"})
    assert e.value.status == 404
    db = SessionLocal()
    db.query(AgentCommand).filter_by(id=cid).update({"expires_at": utcnow() - timedelta(seconds=1)})
    db.commit()
    db.close()
    assert beat(ca)["commands"] == []                                                             # vencido: nunca é executado
    assert client.get(f"/api/arena/{world.A.id}/commands/{cid}", headers=ha).json()["status"] == "expired"
    assert client.get(f"/api/arena/{world.B.id}/commands/{cid}", headers=H("b_admin_arena")).status_code == 404
