"""Ajuste por arena dos recursos do agente: permissões, validação e entrega na configuração."""
BASE = "/api/agent/v1"


def test_defaults_permissions_validation_and_isolation(client, world, H):
    url = f"/api/arena/{world.A.id}/agent-resources"
    assert client.get(url, headers=H("a_op_arena")).status_code == 403 and client.get(url, headers=H("a_admin_arena")).status_code == 403   # técnico: só o global
    d = client.get(url, headers=H("root")).json()
    assert d == {"maxFfmpeg": 1, "hwaccel": "none", "renderNice": 10, "note": d["note"]}
    assert client.put(url, headers=H("a_op_arena"), json={"maxFfmpeg": 2}).status_code == 403             # operador só lê
    assert client.put(url, headers=H("b_admin_arena"), json={"maxFfmpeg": 2}).status_code == 404          # outra arena (IDOR)
    assert client.put(url, headers=H("a_user"), json={"maxFfmpeg": 2}).status_code in (401, 403)
    for bad in ({"maxFfmpeg": 0}, {"maxFfmpeg": 99}, {"hwaccel": "cuda"}, {"renderNice": 30}):
        assert client.put(url, headers=H("root"), json=bad).status_code == 422
    ok = client.put(url, headers=H("root"), json={"maxFfmpeg": 2, "hwaccel": "auto", "renderNice": 15})
    assert ok.status_code == 200 and ok.json()["hwaccel"] == "auto"
    assert client.get(f"/api/arena/{world.B.id}/agent-resources", headers=H("root")).json()["maxFfmpeg"] == 1   # B intacta
    assert client.get("/api/platform/capacity", headers=H("a_admin_arena")).status_code in (401, 403)


def test_resources_are_delivered_to_the_agent_and_change_the_config_version(client, world, H):
    h = {"Authorization": f"Bearer {world.A.token}", "X-Agent-Node": "node-A-0001"}
    client.post(f"{BASE}/register", headers=h, json={"nodeId": "node-A-0001"})
    before = client.get(f"{BASE}/config", headers=h).json()
    assert before["resources"] == {"maxFfmpeg": 1, "hwaccel": "none", "renderNice": 10}
    client.put(f"/api/arena/{world.A.id}/agent-resources", headers=H("root"), json={"maxFfmpeg": 3, "hwaccel": "vaapi", "renderNice": 5})
    after = client.get(f"{BASE}/config", headers=h).json()
    assert after["resources"] == {"maxFfmpeg": 3, "hwaccel": "vaapi", "renderNice": 5} and after["configVersion"] != before["configVersion"]
    hb = client.post(f"{BASE}/heartbeat", headers=h, json={"configVersion": before["configVersion"]}).json()
    assert hb["configChanged"] is True
    from app.db import SessionLocal
    from app.models import AuditLog
    db = SessionLocal()
    assert db.query(AuditLog).filter_by(action="agent.resources", arena_id=world.A.id).count() == 1
    db.close()
