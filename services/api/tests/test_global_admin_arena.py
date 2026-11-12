"""global_admin (Portal + MFA) acessa a Gestão de QUALQUER arena sem Membership; demais perfis seguem isolados."""
from datetime import timedelta

from app.db import SessionLocal, utcnow
from app.models import AuditLog, Membership, OAuthFlow, User, Video
from conftest import PASSWORD, login, platform_login


def P(client, w):
    return platform_login(client, w.emails["root"])


def AH(client, w, who):
    return login(client, w.emails[who], "arena")


def ticket(client, p, arena_id):
    r = client.post(f"/api/platform/arenas/{arena_id}/gestao-ticket", headers=p)
    assert r.status_code == 200, r.text
    return r.json()["ticket"]


def gestao_headers(client, p, arena_id):
    r = client.post("/api/auth/arena/portal-exchange", json={"ticket": ticket(client, p, arena_id)})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_platform_session_manages_any_arena_without_membership(client, world):
    p = P(client, world)
    db = SessionLocal(); before = db.query(Membership).filter_by(user_id=world.uid["root"]).count(); db.close()
    assert before == 0
    for A in (world.A, world.B):
        base = f"/api/arena/{A.id}"
        assert client.get(base, headers=p).json()["myRole"] == "global_admin"
        assert client.get(f"{base}/dashboard", headers=p).status_code == 200
        assert client.get(f"{base}/courts", headers=p).status_code == 200
        vids = client.get(f"{base}/videos", headers=p).json()
        assert {v["id"] for v in vids} >= {A.pub, A.priv}
        # publicar e ocultar replay; excluir exige confirmação
        r = client.patch(f"{base}/videos/{A.priv}", json={"visibility": "arena"}, headers=p)
        assert r.status_code == 200 and r.json()["visibility"] == "arena"
        assert client.patch(f"{base}/videos/{A.priv}", json={"visibility": "private"}, headers=p).status_code == 200
        assert client.delete(f"{base}/videos/{A.mute}", headers=p).status_code == 422
        assert client.delete(f"{base}/videos/{A.mute}?confirm=true", headers=p).status_code == 204
    db = SessionLocal()
    assert db.query(Membership).filter_by(user_id=world.uid["root"]).count() == 0  # nenhum vínculo artificial
    acts = db.query(AuditLog).filter_by(action="platform.arena_act").all()
    assert len(acts) >= 6 and all(a.actor_id == world.uid["root"] and a.arena_id for a in acts)
    assert not any("token" in a.changed_fields.lower() for a in acts)
    db.close()


def test_only_full_platform_session_with_mfa_is_accepted(client, world):
    base = f"/api/arena/{world.A.id}"
    r = client.post("/api/auth/platform/login", json={"email": world.emails["root"], "password": PASSWORD})
    pre = {"Authorization": f"Bearer {r.json()['mfaToken']}"}          # token preliminar de MFA
    assert client.get(base, headers=pre).status_code == 401
    assert client.get(base).status_code == 401                          # anônimo
    assert client.get(base, headers={"Authorization": "Bearer lixo"}).status_code == 401
    # jogador (inclusive 'a_user', membro da própria arena) e demais papéis comuns
    assert client.get(base, headers=login(client, world.emails["a_user"], "user")).status_code == 403
    p = P(client, world)
    # sessão de plataforma não vale em rotas sem MFA habilitado: desabilitar o MFA invalida o acesso
    db = SessionLocal(); u = db.get(User, world.uid["root"]); u.mfa_enabled = False; db.commit(); db.close()
    assert client.get(base, headers=p).status_code == 403


def test_regular_roles_unchanged_and_isolated(client, world):
    a_admin, a_op, b_admin = (AH(client, world, w) for w in ("a_admin", "a_op", "b_admin"))
    assert client.get(f"/api/arena/{world.A.id}", headers=a_admin).json()["myRole"] == "arena_admin"
    assert client.get(f"/api/arena/{world.A.id}/agents", headers=a_op).status_code == 403      # operador não é gestor
    assert client.get(f"/api/arena/{world.A.id}/videos", headers=a_op).status_code == 200
    assert client.get(f"/api/arena/{world.B.id}", headers=a_admin).status_code == 404            # sem acesso lateral
    assert client.get(f"/api/arena/{world.A.id}", headers=b_admin).status_code == 404
    assert client.patch(f"/api/arena/{world.B.id}/videos/{world.B.pub}", json={"title": "x"}, headers=a_admin).status_code == 404
    # vídeo de B pelo caminho de A continua 404 (também para o global)
    p = P(client, world)
    assert client.patch(f"/api/arena/{world.A.id}/videos/{world.B.pub}", json={"title": "x"}, headers=p).status_code == 404
    # gestor não obtém ticket global nem acessa rotas do portal
    assert client.post(f"/api/platform/arenas/{world.A.id}/gestao-ticket", headers=a_admin).status_code in (401, 403)
    assert client.get("/api/platform/arenas", headers=a_admin).status_code in (401, 403)


def test_global_admin_blocked_on_suspended_or_unknown_arena(client, world):
    p = P(client, world)
    assert client.get("/api/arena/nao-existe", headers=p).status_code == 404
    client.patch(f"/api/platform/arenas/{world.B.id}", json={"status": "suspended"}, headers=p)
    assert client.get(f"/api/arena/{world.B.id}", headers=p).status_code == 403
    assert client.post(f"/api/platform/arenas/{world.B.id}/gestao-ticket", headers=p).status_code == 409


def test_global_admin_does_not_play_private_content_and_cannot_create_global_admins(client, world):
    p = P(client, world)
    base = f"/api/arena/{world.A.id}"
    assert client.get(f"{base}/videos/{world.A.priv}/play", headers=p).status_code == 403
    pm = f"/api/platform/arenas/{world.A.id}/members"
    assert client.post(pm, json={"name": "X Y", "email": "x@t.test", "role": "global_admin"}, headers=p).status_code == 422
    assert client.post(pm, json={"name": "Novo Op", "email": "novo.op@t.test", "role": "arena_operator"}, headers=p).status_code == 201
    mid = next(m["id"] for m in client.get(pm, headers=p).json() if m["email"] == world.emails["a_admin"])
    assert client.delete(f"{pm}/{mid}", headers=p).status_code == 409                   # último gestor continua protegido


def test_portal_handoff_ticket_is_single_use_scoped_and_not_a_token(client, world):
    p = P(client, world)
    t = ticket(client, p, world.A.id)
    r = client.post("/api/auth/arena/portal-exchange", json={"ticket": t})
    assert r.status_code == 200 and r.json()["arenaId"] == world.A.id
    g = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.post("/api/auth/arena/portal-exchange", json={"ticket": t}).status_code == 401   # uso único
    assert client.get(f"/api/arena/{world.A.id}/dashboard", headers=g).status_code == 200
    me = client.get("/api/auth/arena/me", headers=g).json()
    assert [m["arenaId"] for m in me["memberships"]] == [world.A.id] and me["memberships"][0]["role"] == "global_admin"
    # o ticket não serve como Bearer; a sessão emitida NÃO vale em outra arena nem no Portal, e não é sessão de jogador
    assert client.get(f"/api/arena/{world.A.id}", headers={"Authorization": f"Bearer {t}"}).status_code == 401
    assert client.get(f"/api/arena/{world.B.id}", headers=g).status_code == 403
    assert client.get("/api/platform/arenas", headers=g).status_code == 403
    assert client.get("/api/auth/user/me", headers=g).status_code == 403
    assert client.patch(f"/api/arena/{world.A.id}/videos/{world.A.pub}", json={"title": "ok"}, headers=g).status_code == 200


def test_handoff_ticket_expiry_and_bad_values(client, world):
    p = P(client, world)
    t = ticket(client, p, world.A.id)
    db = SessionLocal()
    for f in db.query(OAuthFlow).filter_by(intent="portal"):
        f.ticket_expires_at = utcnow() - timedelta(seconds=1)
    db.commit(); db.close()
    assert client.post("/api/auth/arena/portal-exchange", json={"ticket": t}).status_code == 401
    assert client.post("/api/auth/arena/portal-exchange", json={"ticket": "x" * 40}).status_code == 401
    assert client.post("/api/platform/arenas/inexistente/gestao-ticket", headers=p).status_code == 404
    assert client.post(f"/api/platform/arenas/{world.A.id}/gestao-ticket").status_code in (401, 403)


def test_handoff_session_dies_when_admin_loses_global_status_or_mfa(client, world):
    p = P(client, world)
    for mutate in (lambda u: setattr(u, "active", False), lambda u: setattr(u, "global_role", None), lambda u: setattr(u, "mfa_enabled", False)):
        g = gestao_headers(client, p, world.A.id) if client.get("/api/platform/arenas", headers=p).status_code == 200 else None
        if g is None:
            break
        assert client.get(f"/api/arena/{world.A.id}", headers=g).status_code == 200
        db = SessionLocal(); u = db.get(User, world.uid["root"]); snap = (u.active, u.global_role, u.mfa_enabled); mutate(u); db.commit(); db.close()
        assert client.get(f"/api/arena/{world.A.id}", headers=g).status_code in (401, 403)
        db = SessionLocal(); u = db.get(User, world.uid["root"]); u.active, u.global_role, u.mfa_enabled = snap; db.commit(); db.close()


def test_handoff_audited_without_secrets(client, world):
    p = P(client, world)
    g = gestao_headers(client, p, world.A.id)
    client.patch(f"/api/arena/{world.A.id}/videos/{world.A.pub}", json={"title": "t"}, headers=g)
    db = SessionLocal()
    acts = {a.action for a in db.query(AuditLog)}
    assert {"platform.gestao_ticket", "platform.gestao_session", "platform.arena_act"} <= acts
    raw = " ".join(a.changed_fields for a in db.query(AuditLog))
    assert g["Authorization"].split()[1] not in raw
    db.close()
