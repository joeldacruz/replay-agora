"""Gestão de usuários por arena: cadastro, vínculo, perfil, situação, isolamento, elevação, convites e migração."""
import re

import pytest

from app.db import SessionLocal
from app.models import AccountToken, Arena, AuditLog, Membership, OutboxMail, User
from conftest import PASSWORD, login, platform_login

NEW = {"name": "Maria Gestora", "email": "maria@vibe.test", "role": "arena_admin", "authMethod": "password"}


def P(client, w):
    return platform_login(client, w.emails["root"])


def A(client, w, who="a_admin"):
    return login(client, w.emails[who], "arena")


def last_token(email):
    db = SessionLocal()
    try:
        m = db.query(OutboxMail).filter_by(to_email=email).order_by(OutboxMail.created_at.desc()).first()
        return re.search(r"token=([\w\-]+)", m.body).group(1) if m else None
    finally:
        db.close()


def test_portal_creates_manager_and_user_sets_password(client, world):
    h = P(client, world)
    r = client.post(f"/api/platform/arenas/{world.A.id}/members", json=NEW, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["created"] and r.json()["passwordSet"] is False
    assert "hash" not in r.text.lower() and "token" not in r.text.lower()
    # sem senha: ainda não entra
    assert client.post("/api/auth/arena/login", json={"email": NEW["email"], "password": PASSWORD}).status_code == 401
    tok = last_token(NEW["email"])
    assert tok
    assert client.get("/api/auth/password/check", params={"token": tok}).json()["kind"] == "invite"
    assert client.post("/api/auth/password/set", json={"token": tok, "password": "curta"}).status_code == 422
    assert client.post("/api/auth/password/set", json={"token": tok, "password": "Nova-senha-segura-1"}).status_code == 200
    assert client.post("/api/auth/password/set", json={"token": tok, "password": "Nova-senha-segura-2"}).status_code == 400  # uso único
    hh = login(client, NEW["email"], "arena", "Nova-senha-segura-1")
    assert client.get(f"/api/arena/{world.A.id}/members", headers=hh).status_code == 200
    # gestor da A não enxerga B
    assert client.get(f"/api/arena/{world.B.id}/members", headers=hh).status_code in (403, 404)


def test_invite_link_never_returned_to_arena_manager(client, world):
    r = client.post(f"/api/arena/{world.A.id}/members", json={**NEW, "role": "arena_operator"}, headers=A(client, world))
    assert r.status_code == 201
    assert "token" not in r.text.lower()
    assert client.get("/api/platform/outbox", headers=A(client, world)).status_code in (401, 403)
    assert any("definir-senha" in o["body"] for o in client.get("/api/platform/outbox", headers=P(client, world)).json())


def test_existing_email_requires_confirmation_then_links_without_duplicate(client, world):
    h = A(client, world)
    r = client.post(f"/api/arena/{world.A.id}/members", json={"name": "Outro Nome", "email": world.emails["b_user"], "role": "user"}, headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "email_exists"
    assert "B User" not in r.text and "B***" in r.text  # nome mascarado
    r = client.post(f"/api/arena/{world.A.id}/members", json={"name": "x", "email": world.emails["b_user"], "role": "user", "linkExisting": True}, headers=h)
    assert r.status_code == 201 and r.json()["created"] is False
    db = SessionLocal()
    assert db.query(User).filter_by(email=world.emails["b_user"]).count() == 1
    assert db.query(Membership).filter_by(user_id=world.uid["b_user"]).count() == 2  # várias arenas
    db.close()


def test_membership_inactive_in_one_arena_does_not_block_other(client, world):
    h = A(client, world)
    client.post(f"/api/arena/{world.A.id}/members", json={"name": "x", "email": world.emails["b_user"], "role": "user", "linkExisting": True}, headers=h)
    mid = next(m["id"] for m in client.get(f"/api/arena/{world.A.id}/members", headers=h).json() if m["email"] == world.emails["b_user"])
    assert client.patch(f"/api/arena/{world.A.id}/members/{mid}", json={"status": "suspended"}, headers=h).status_code == 200
    ub = login(client, world.emails["b_user"], "user")
    assert client.get("/api/auth/user/me", headers=ub).status_code == 200
    db = SessionLocal()
    assert db.query(Membership).filter_by(user_id=world.uid["b_user"], arena_id=world.B.id).one().status == "active"
    db.close()


def test_role_change_and_privilege_escalation_blocked(client, world):
    h = A(client, world)
    mid = next(m["id"] for m in client.get(f"/api/arena/{world.A.id}/members", headers=h).json() if m["email"] == world.emails["a_user"])
    assert client.patch(f"/api/arena/{world.A.id}/members/{mid}", json={"role": "arena_operator"}, headers=h).status_code == 200
    assert client.patch(f"/api/arena/{world.A.id}/members/{mid}", json={"role": "global_admin"}, headers=h).status_code == 422
    assert client.post(f"/api/arena/{world.A.id}/members", json={**NEW, "role": "global_admin"}, headers=h).status_code == 422
    # operador não gerencia equipe
    ho = A(client, world, "a_op")
    assert client.get(f"/api/arena/{world.A.id}/members", headers=ho).status_code == 403
    assert client.post(f"/api/arena/{world.A.id}/members", json=NEW, headers=ho).status_code == 403
    # sem autoelevação / autorebaixamento / autoinativação
    me = next(m["id"] for m in client.get(f"/api/arena/{world.A.id}/members", headers=h).json() if m["email"] == world.emails["a_admin"])
    assert client.patch(f"/api/arena/{world.A.id}/members/{me}", json={"role": "arena_operator"}, headers=h).status_code == 409
    assert client.delete(f"/api/arena/{world.A.id}/members/{me}", headers=h).status_code == 409
    # gestor não usa rotas do portal
    assert client.get("/api/platform/users", headers=h).status_code in (401, 403)


def test_idor_other_arena_member_ids_and_profile(client, world):
    h = A(client, world)
    db = SessionLocal()
    mb = db.query(Membership).filter_by(user_id=world.uid["b_admin"]).one().id
    db.close()
    assert client.patch(f"/api/arena/{world.A.id}/members/{mb}", json={"status": "suspended"}, headers=h).status_code == 404
    assert client.delete(f"/api/arena/{world.A.id}/members/{mb}", headers=h).status_code == 404
    assert client.get(f"/api/arena/{world.A.id}/members/{mb}/history", headers=h).status_code == 404


def test_manager_cannot_edit_profile_of_multi_arena_user_or_global_admin(client, world):
    h = A(client, world)
    client.post(f"/api/arena/{world.A.id}/members", json={"name": "x", "email": world.emails["b_user"], "role": "user", "linkExisting": True}, headers=h)
    mid = next(m["id"] for m in client.get(f"/api/arena/{world.A.id}/members", headers=h).json() if m["email"] == world.emails["b_user"])
    assert client.patch(f"/api/arena/{world.A.id}/members/{mid}", json={"name": "Hack"}, headers=h).status_code == 403
    r = client.post(f"/api/arena/{world.A.id}/members", json={"email": world.emails["root"], "role": "user"}, headers=h)
    assert r.status_code == 422  # global_admin não é vinculável pelo gestor


def test_last_manager_guard_everywhere(client, world):
    p = P(client, world)
    mid = next(m["id"] for m in client.get(f"/api/platform/arenas/{world.A.id}/members", headers=p).json() if m["role"] == "arena_admin")
    assert client.patch(f"/api/platform/arenas/{world.A.id}/members/{mid}", json={"role": "user"}, headers=p).status_code == 409
    assert client.patch(f"/api/platform/arenas/{world.A.id}/members/{mid}", json={"status": "suspended"}, headers=p).status_code == 409
    assert client.delete(f"/api/platform/arenas/{world.A.id}/members/{mid}", headers=p).status_code == 409
    assert client.patch(f"/api/platform/users/{world.uid['a_admin']}", json={"active": False}, headers=p).status_code == 409
    # com substituto, pode
    client.post(f"/api/platform/arenas/{world.A.id}/members", json=NEW, headers=p)
    assert client.patch(f"/api/platform/users/{world.uid['a_admin']}", json={"active": False}, headers=p).status_code == 200


def test_inactivation_revokes_session_keeps_history_and_reactivates(client, world):
    p = P(client, world)
    hu = login(client, world.emails["a_user"], "user")
    assert client.get("/api/auth/user/me", headers=hu).status_code == 200
    assert client.patch(f"/api/platform/users/{world.uid['a_user']}", json={"active": False}, headers=p).status_code == 200
    assert client.get("/api/auth/user/me", headers=hu).status_code == 401
    assert client.post("/api/auth/user/login", json={"email": world.emails["a_user"], "password": PASSWORD}).status_code == 401
    db = SessionLocal()
    assert db.query(User).filter_by(id=world.uid["a_user"]).one().active is False
    assert db.query(Membership).filter_by(user_id=world.uid["a_user"]).count() == 1  # vínculo preservado
    db.close()
    hist = client.get(f"/api/platform/users/{world.uid['a_user']}/history", headers=p).json()
    assert hist[0]["action"] == "account_inactivate" and hist[0]["actor"] == world.emails["root"]
    assert client.patch(f"/api/platform/users/{world.uid['a_user']}", json={"active": True}, headers=p).status_code == 200
    assert client.post("/api/auth/user/login", json={"email": world.emails["a_user"], "password": PASSWORD}).status_code == 200


def test_reset_access_revokes_old_sessions_and_never_exposes_password(client, world):
    p = P(client, world)
    old = login(client, world.emails["a_user"], "user")
    r = client.post(f"/api/platform/users/{world.uid['a_user']}/reset-access", headers=p)
    assert r.status_code == 200 and r.json() == {"sent": True}
    tok = last_token(world.emails["a_user"])
    assert client.post("/api/auth/password/set", json={"token": tok, "password": "Outra-senha-segura-9"}).status_code == 200
    assert client.get("/api/auth/user/me", headers=old).status_code == 401
    login(client, world.emails["a_user"], "user", "Outra-senha-segura-9")
    assert client.post("/api/auth/password/set", json={"token": "x" * 30, "password": "Outra-senha-segura-9"}).status_code == 400


def test_arena_with_new_manager_is_transactional(client, world):
    p = P(client, world)
    body = {"slug": "vibe-beach", "name": "Arena Vibe Beach", "manager": {"mode": "new", "name": "Joel Gestor", "email": "gestor@vibe.test"}}
    r = client.post("/api/platform/arenas", json=body, headers=p)
    assert r.status_code == 201, r.text
    assert r.json()["admins"][0]["email"] == "gestor@vibe.test"
    # falha no gestor (e-mail inválido) não deixa arena órfã
    r = client.post("/api/platform/arenas", json={"slug": "outra-arena", "name": "Outra", "manager": {"mode": "new", "name": "Z Z", "email": "invalido"}}, headers=p)
    assert r.status_code == 422
    db = SessionLocal()
    assert db.query(Arena).filter_by(slug="outra-arena").count() == 0
    db.close()
    # gestor existente
    r = client.post("/api/platform/arenas", json={"slug": "terceira", "name": "Terceira", "manager": {"mode": "existing", "userId": world.uid["nobody"]}}, headers=p)
    assert r.status_code == 201 and r.json()["admins"][0]["email"] == world.emails["nobody"]
    # e-mail duplicado em modo new: conflito e rollback
    r = client.post("/api/platform/arenas", json={"slug": "quarta", "name": "Quarta", "manager": {"mode": "new", "name": "Dup Dup", "email": world.emails["b_user"]}}, headers=p)
    assert r.status_code == 409
    db = SessionLocal()
    assert db.query(Arena).filter_by(slug="quarta").count() == 0
    db.close()


def test_filters_and_search(client, world):
    p = P(client, world)
    rows = client.get(f"/api/platform/arenas/{world.A.id}/members", params={"role": "arena_operator"}, headers=p).json()
    assert [r["email"] for r in rows] == [world.emails["a_op"]]
    assert client.get(f"/api/platform/arenas/{world.A.id}/members", params={"q": "user2"}, headers=p).json()[0]["email"] == world.emails["a_user2"]
    glob = client.get("/api/platform/users", params={"role": "global_admin"}, headers=p).json()
    assert [u["email"] for u in glob] == [world.emails["root"]]


def test_history_records_actor_and_changes(client, world):
    h = A(client, world)
    mid = next(m["id"] for m in client.get(f"/api/arena/{world.A.id}/members", headers=h).json() if m["email"] == world.emails["a_user"])
    client.patch(f"/api/arena/{world.A.id}/members/{mid}", json={"role": "arena_operator"}, headers=h)
    hist = client.get(f"/api/arena/{world.A.id}/members/{mid}/history", headers=h).json()
    assert hist[0]["action"] == "membership_update" and hist[0]["changes"]["role"]["para"] == "arena_operator"
    assert hist[0]["actor"] == world.emails["a_admin"]


def test_google_only_account_rejects_password_login(client, world):
    p = P(client, world)
    client.post(f"/api/platform/arenas/{world.A.id}/members", json={**NEW, "authMethod": "google"}, headers=p)
    assert client.post("/api/auth/arena/login", json={"email": NEW["email"], "password": PASSWORD}).status_code == 401


def test_global_admin_cannot_be_created_via_arena_routes_and_platform_mfa_unchanged(client, world):
    p = P(client, world)
    r = client.post("/api/platform/users", json={"name": "Novo Admin", "email": "novo@t.test", "globalRole": "global_admin"}, headers=p)
    assert r.status_code == 201 and r.json()["mfaEnabled"] is False
    tok = last_token("novo@t.test")
    client.post("/api/auth/password/set", json={"token": tok, "password": "Senha-do-admin-123"})
    r = client.post("/api/auth/platform/login", json={"email": "novo@t.test", "password": "Senha-do-admin-123"})
    assert r.json()["mfaStep"] == "enroll"  # continua exigindo MFA
