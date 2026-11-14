"""Credenciais rotativas, requisições assinadas (anti-replay), mTLS preparado e ausência de segredos em logs/auditoria."""
import json
import logging
import time
from datetime import timedelta

from app.agent_security import compute_sig
from app.config import settings
from app.db import SessionLocal, utcnow
from app.logsafe import redact
from app.models import AgentNonce, ArenaAgent, AuditLog
from replay_agent.client import CentralClient, CentralRejected
from replay_agent.creds import CredentialStore
from replay_agent.signing import sign_headers

BASE = "/api/agent/v1"


def new_signed_agent(client, world, H, label="ag-signed"):
    r = client.post(f"/api/arena/{world.A.id}/agents", headers=H("root"), json={"label": label})
    assert r.status_code == 201, r.text
    return r.json()


def sclient(client, token, key, node="node-sig-0001"):
    return CentralClient(client, token, node, signing_key=key)


def raw(client, token, key, method, path, body=b"", node="node-sig-0001", **over):
    from urllib.parse import urlsplit
    parts = urlsplit(path)
    h = {"Authorization": f"Bearer {token}", "X-Agent-Node": node, "Content-Type": "application/json"}
    if key:
        h |= sign_headers(key, method, parts.path, parts.query, body, **over)
    return client.request(method, path, content=body or None, headers=h)


# ---------- assinatura / anti-replay ----------
def test_signed_client_works_and_unsigned_is_refused_for_signed_agents(client, world, H):
    a = new_signed_agent(client, world, H)
    c = sclient(client, a["token"], a["signingKey"])
    assert c.call("POST", "/register", {"nodeId": "node-sig-0001"})["arenaId"] == world.A.id
    assert c.call("POST", "/heartbeat", {})["ok"] is True
    # só Bearer, sem assinatura: recusado (token vazado em log não basta)
    r = client.post(f"{BASE}/heartbeat", headers={"Authorization": f"Bearer {a['token']}", "X-Agent-Node": "node-sig-0001"}, json={})
    assert r.status_code == 401


def test_tampered_body_path_method_and_wrong_key_rejected(client, world, H):
    a = new_signed_agent(client, world, H)
    sclient(client, a["token"], a["signingKey"]).call("POST", "/register", {"nodeId": "node-sig-0001"})
    body = b'{"version":"1"}'
    ok = raw(client, a["token"], a["signingKey"], "POST", f"{BASE}/heartbeat", body)
    assert ok.status_code == 200, ok.text
    # corpo trocado depois de assinar
    h = {"Authorization": f"Bearer {a['token']}", "X-Agent-Node": "node-sig-0001", "Content-Type": "application/json",
         **sign_headers(a["signingKey"], "POST", f"{BASE}/heartbeat", "", body)}
    assert client.post(f"{BASE}/heartbeat", content=b'{"version":"666"}', headers=h).status_code == 401
    # assinatura de outro caminho / outro método
    h2 = {**h, **sign_headers(a["signingKey"], "POST", f"{BASE}/config", "", body)}
    assert client.post(f"{BASE}/heartbeat", content=body, headers=h2).status_code == 401
    assert raw(client, a["token"], "0" * 64, "POST", f"{BASE}/heartbeat", body).status_code == 401  # chave errada


def test_replayed_request_and_stale_timestamp_rejected(client, world, H):
    a = new_signed_agent(client, world, H)
    sclient(client, a["token"], a["signingKey"]).call("POST", "/register", {"nodeId": "node-sig-0001"})
    body = b"{}"
    hdr = {"Authorization": f"Bearer {a['token']}", "X-Agent-Node": "node-sig-0001", "Content-Type": "application/json",
           **sign_headers(a["signingKey"], "POST", f"{BASE}/heartbeat", "", body)}
    assert client.post(f"{BASE}/heartbeat", content=body, headers=hdr).status_code == 200
    r = client.post(f"{BASE}/heartbeat", content=body, headers=hdr)          # captura reapresentada, idêntica
    assert r.status_code == 401 and "repetida" in r.text
    old = raw(client, a["token"], a["signingKey"], "POST", f"{BASE}/heartbeat", body, now=time.time() - 3600)
    assert old.status_code == 401 and "janela" in old.text
    future = raw(client, a["token"], a["signingKey"], "POST", f"{BASE}/heartbeat", body, now=time.time() + 3600)
    assert future.status_code == 401
    # um novo nonce legítimo continua funcionando
    assert raw(client, a["token"], a["signingKey"], "POST", f"{BASE}/heartbeat", body).status_code == 200


def test_failed_request_still_burns_its_nonce(client, world, H):
    a = new_signed_agent(client, world, H)
    hdr = {"Authorization": f"Bearer {a['token']}", "X-Agent-Node": "node-sig-0001", "Content-Type": "application/json",
           **sign_headers(a["signingKey"], "POST", f"{BASE}/heartbeat", "", b"{}")}
    assert client.post(f"{BASE}/heartbeat", content=b"{}", headers=hdr).status_code == 409   # não registrado
    assert client.post(f"{BASE}/heartbeat", content=b"{}", headers=hdr).status_code == 401   # nonce já queimado


def test_nonce_table_is_pruned(client, world, H):
    a = new_signed_agent(client, world, H)
    c = sclient(client, a["token"], a["signingKey"])
    c.call("POST", "/register", {"nodeId": "node-sig-0001"})
    db = SessionLocal()
    ag = db.query(ArenaAgent).filter_by(label="ag-signed").one()
    db.add(AgentNonce(agent_id=ag.id, nonce="old-nonce-0000000000", ts=utcnow() - timedelta(hours=2)))
    db.commit()
    db.close()
    c.call("POST", "/heartbeat", {})
    db = SessionLocal()
    assert db.query(AgentNonce).filter_by(nonce="old-nonce-0000000000").count() == 0
    db.close()


def test_client_and_server_signature_schemes_match():
    k, ts, n, body = "ab" * 32, "1762300000", "n" * 20, b'{"x":1}'
    srv = compute_sig(k, "POST", "/api/agent/v1/x", "a=1", ts, n, body)
    cli = sign_headers(k, "POST", "/api/agent/v1/x", "a=1", body, now=1762300000, nonce=n)["X-Agent-Sig"]
    assert srv == cli


def test_require_signature_flag_forces_legacy_agents_to_rotate(client, world, monkeypatch):
    h = {"Authorization": f"Bearer {world.A.token}", "X-Agent-Node": "node-A-0001"}
    assert client.post(f"{BASE}/register", headers=h, json={"nodeId": "node-A-0001"}).status_code == 200  # legado ainda aceito
    monkeypatch.setattr(settings, "agent_require_signature", True)
    assert client.post(f"{BASE}/heartbeat", headers=h, json={}).status_code == 401
    # a rotação (assinada não é possível ainda) também exige assinatura => gestor emite nova credencial
    assert client.post(f"{BASE}/credentials/rotate", headers=h).status_code == 401


# ---------- rotação ----------
def test_agent_rotation_grace_window_and_old_token_expiry(client, world, H):
    a = new_signed_agent(client, world, H)
    c = sclient(client, a["token"], a["signingKey"])
    c.call("POST", "/register", {"nodeId": "node-sig-0001"})
    new = c.call("POST", "/credentials/rotate")
    assert new["token"] != a["token"] and new["signingKey"] != a["signingKey"] and new["previousValidUntil"]
    # nova credencial funciona
    n = sclient(client, new["token"], new["signingKey"])
    assert n.call("POST", "/heartbeat", {})["ok"]
    # anterior ainda vale dentro da janela de carência (resposta da rotação pode ter se perdido)
    assert c.call("POST", "/heartbeat", {})["ok"]
    # fora da janela: invalidada
    db = SessionLocal()
    ag = db.query(ArenaAgent).filter_by(label="ag-signed").one()
    ag.prev_valid_until = utcnow() - timedelta(seconds=1)
    db.commit()
    db.close()
    try:
        c.call("POST", "/heartbeat", {})
        raise AssertionError("credencial antiga deveria estar inválida")
    except CentralRejected as e:
        assert e.status == 401
    assert n.call("POST", "/heartbeat", {})["ok"]


def test_rotation_with_lost_response_can_be_repeated_via_previous_credential(client, world, H):
    a = new_signed_agent(client, world, H)
    c = sclient(client, a["token"], a["signingKey"])
    c.call("POST", "/register", {"nodeId": "node-sig-0001"})
    lost = c.call("POST", "/credentials/rotate")             # agente nunca persistiu esta resposta
    again = c.call("POST", "/credentials/rotate")            # repete com a credencial antiga (ainda na janela)
    assert again["token"] != lost["token"]
    assert sclient(client, again["token"], again["signingKey"]).call("POST", "/heartbeat", {})["ok"]
    try:
        sclient(client, lost["token"], lost["signingKey"]).call("POST", "/heartbeat", {})
        raise AssertionError("par perdido deveria ter sido substituído")
    except CentralRejected as e:
        assert e.status == 401


def test_forced_rotation_by_manager_invalidates_old_immediately_and_is_scoped(client, world, H):
    a = new_signed_agent(client, world, H)
    c = sclient(client, a["token"], a["signingKey"])
    c.call("POST", "/register", {"nodeId": "node-sig-0001"})
    assert client.post(f"/api/arena/{world.B.id}/agents/{a['id']}/rotate", headers=H("b_admin_arena")).status_code in (403, 404)  # IDOR (gestor nem tem a permissão)
    assert client.post(f"/api/arena/{world.A.id}/agents/{a['id']}/rotate", headers=H("a_op_arena")).status_code == 403
    r = client.post(f"/api/arena/{world.A.id}/agents/{a['id']}/rotate", headers=H("root")).json()
    try:
        c.call("POST", "/heartbeat", {})
        raise AssertionError("antiga deveria cair na hora")
    except CentralRejected as e:
        assert e.status == 401
    assert sclient(client, r["token"], r["signingKey"]).call("POST", "/heartbeat", {})["ok"]


def test_expired_credentials_rejected_but_rotation_allowed_briefly(client, world, H):
    a = new_signed_agent(client, world, H)
    c = sclient(client, a["token"], a["signingKey"])
    c.call("POST", "/register", {"nodeId": "node-sig-0001"})
    db = SessionLocal()
    ag = db.query(ArenaAgent).filter_by(label="ag-signed").one()
    ag.token_expires_at = utcnow() - timedelta(days=1)
    db.commit()
    db.close()
    for call in (lambda: c.call("POST", "/heartbeat", {}), lambda: c.call("POST", "/sync/replays", {"replays": []})):
        try:
            call()
            raise AssertionError("expirada deveria falhar")
        except CentralRejected as e:
            assert e.status == 401
    new = c.call("POST", "/credentials/rotate")                 # dentro da tolerância: pode se recuperar sozinha
    assert sclient(client, new["token"], new["signingKey"]).call("POST", "/heartbeat", {})["ok"]
    db = SessionLocal()
    ag = db.query(ArenaAgent).filter_by(label="ag-signed").one()
    ag.token_expires_at = utcnow() - timedelta(days=30)
    ag.prev_valid_until = None
    db.commit()
    db.close()
    n = sclient(client, new["token"], new["signingKey"])
    try:
        n.call("POST", "/credentials/rotate")
        raise AssertionError("muito expirada: exige nova credencial do gestor")
    except CentralRejected as e:
        assert e.status == 401


def test_legacy_agent_is_told_to_rotate_and_upgrades_to_signed(client, world):
    legacy = CentralClient(client, world.A.token, "node-A-0001")
    r = legacy.call("POST", "/register", {"nodeId": "node-A-0001"})
    assert r["rotateRecommended"] is True and r["credentialExpiresAt"]
    new = legacy.call("POST", "/credentials/rotate")
    s = sclient(client, new["token"], new["signingKey"], "node-A-0001")
    assert s.call("POST", "/heartbeat", {})["rotateRecommended"] is False
    assert client.post("/api/ingest/heartbeat", headers={"Authorization": f"Bearer {world.A.token}"}, json={"pendingSync": 0}).status_code == 200  # janela


def test_revoked_agent_and_other_arena_tokens(client, world, H):
    a = new_signed_agent(client, world, H)
    c = sclient(client, a["token"], a["signingKey"])
    c.call("POST", "/register", {"nodeId": "node-sig-0001"})
    client.delete(f"/api/arena/{world.A.id}/agents/{a['id']}", headers=H("root"))
    try:
        c.call("POST", "/heartbeat", {})
        raise AssertionError
    except CentralRejected as e:
        assert e.status == 401


def test_agent_with_rotation_hint_rotates_and_persists_credentials(client, world, H, tmp_path):
    """Integração: o CentralLink rotaciona sozinho, grava o arquivo 0600 e passa a usar o par novo."""
    from replay_agent.simulator import build_arena
    a = new_signed_agent(client, world, H)
    db = SessionLocal()
    ag = db.query(ArenaAgent).filter_by(label="ag-signed").one()
    ag.token_expires_at = utcnow() + timedelta(days=2)       # perto de expirar
    db.commit()
    db.close()
    store = CredentialStore(tmp_path / "creds" / "agent.json")
    ar = build_arena("A", a["token"], ["A_01", "A_02"], client, root=tmp_path / "A", signing_key=a["signingKey"], creds=store)
    ar.link.client.node_id = "node-sig-0001"
    out = ar.link.tick()
    assert out["online"] and ar.link.rotations == 1
    saved = store.load()
    assert saved["token"] != a["token"] and (tmp_path / "creds" / "agent.json").stat().st_mode & 0o777 == 0o600
    assert ar.link.tick()["online"] and ar.link.rotations == 1   # já rotacionou; não repete
    ar.engine.stop()


# ---------- mTLS (preparação) ----------
def test_mtls_modes(client, world, H, monkeypatch):
    a = new_signed_agent(client, world, H)
    c = sclient(client, a["token"], a["signingKey"])
    c.call("POST", "/register", {"nodeId": "node-sig-0001"})
    fp = "ab" * 32
    assert client.put(f"/api/arena/{world.A.id}/agents/{a['id']}/certificate", headers=H("root"), json={"fingerprint": fp}).json()["certificateBound"]
    assert client.put(f"/api/arena/{world.A.id}/agents/{a['id']}/certificate", headers=H("root"), json={"fingerprint": "xyz"}).status_code == 422
    body = b"{}"
    hdr = settings.agent_mtls_header
    # modo desligado: cabeçalho ignorado
    assert raw(client, a["token"], a["signingKey"], "POST", f"{BASE}/heartbeat", body).status_code == 200
    monkeypatch.setattr(settings, "agent_mtls_mode", "optional")
    assert raw(client, a["token"], a["signingKey"], "POST", f"{BASE}/heartbeat", body).status_code == 401  # tem fingerprint => exige certificado
    from urllib.parse import urlsplit

    def with_fp(value):
        h = {"Authorization": f"Bearer {a['token']}", "X-Agent-Node": "node-sig-0001", hdr: value, "Content-Type": "application/json",
             **sign_headers(a["signingKey"], "POST", f"{BASE}/heartbeat", "", body)}
        return client.post(f"{BASE}/heartbeat", content=body, headers=h)
    assert with_fp("cd" * 32).status_code == 401
    assert with_fp(":".join(fp[i:i + 2] for i in range(0, 64, 2)).upper()).status_code == 200  # formato com ':' aceito
    # modo obrigatório: agente sem certificado cadastrado também é barrado
    monkeypatch.setattr(settings, "agent_mtls_mode", "required")
    other = new_signed_agent(client, world, H, "ag-nocert")
    assert raw(client, other["token"], other["signingKey"], "POST", f"{BASE}/register", b'{"nodeId":"node-x-00001"}', node="node-x-00001").status_code == 401


# ---------- segredos fora de logs e auditoria ----------
def test_redaction_patterns():
    tok = "rag_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
    for raw_text in (f"falha com token {tok}", f"Authorization: Bearer {tok}", f"signing_key={'f' * 64}", f"X-Agent-Sig: {'a1' * 32}"):
        out = redact(raw_text)
        assert tok not in out and "f" * 64 not in out and "a1" * 32 not in out, out


def test_secrets_never_reach_logs_or_audit_or_listings(client, world, H, caplog):
    caplog.set_level(logging.DEBUG)
    a = new_signed_agent(client, world, H)
    c = sclient(client, a["token"], a["signingKey"])
    c.call("POST", "/register", {"nodeId": "node-sig-0001"})
    new = c.call("POST", "/credentials/rotate")
    logging.getLogger("replay_agent").error("erro ao falar com a central: token=%s key=%s", new["token"], new["signingKey"])
    logging.getLogger("app").error("Authorization: Bearer %s", a["token"])
    assert new["token"] not in caplog.text and new["signingKey"] not in caplog.text and a["token"] not in caplog.text
    assert repr(c) and a["token"] not in repr(c) and a["signingKey"] not in repr(c)
    db = SessionLocal()
    dump = json.dumps([[r.action, r.changed_fields] for r in db.query(AuditLog)])
    db.close()
    for secret in (a["token"], a["signingKey"], new["token"], new["signingKey"]):
        assert secret not in dump
    listing = json.dumps(client.get(f"/api/arena/{world.A.id}/agents", headers=H("root")).json())
    assert all(s not in listing for s in (a["token"], a["signingKey"], new["token"], new["signingKey"]))
    assert "agent.rotate" in dump
