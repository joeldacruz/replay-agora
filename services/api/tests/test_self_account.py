"""Conta do próprio usuário: dados pessoais e senha em qualquer app; sem autoelevação; troca de senha encerra outras sessões."""
from conftest import PASSWORD, login, platform_login

NEWPW = "Outra-Senha-Forte-77"


def test_user_and_manager_edit_only_name_and_phone(client, world):
    for who, app in (("a_user", "user"), ("a_admin", "arena")):
        h = login(client, world.emails[who], app)
        assert client.get("/api/auth/account", headers=h).json()["email"] == world.emails[who]
        r = client.patch("/api/auth/account", headers=h, json={"name": "Nome Novo", "phone": "(47) 99999-0000"})
        assert r.status_code == 200 and r.json()["name"] == "Nome Novo" and r.json()["phone"] == "(47) 99999-0000"
        # campos sensíveis são ignorados: não há autoelevação
        r = client.patch("/api/auth/account", headers=h, json={"globalRole": "global_admin", "role": "arena_admin", "email": "x@x.test", "active": False})
        assert r.status_code == 200 and r.json()["email"] == world.emails[who]
        assert client.get("/api/auth/user/me" if app == "user" else "/api/auth/arena/me", headers=h).json()["globalRole"] is None
    assert client.patch("/api/auth/account", json={"name": "Sem Login"}).status_code == 401
    assert client.patch("/api/auth/account", headers=login(client, world.emails["a_user"], "user"), json={"name": "x"}).status_code == 422


def test_password_change_requires_current_revokes_other_sessions_and_keeps_current(client, world):
    old = login(client, world.emails["a_user"], "user")
    cur = login(client, world.emails["a_user"], "user")
    assert client.post("/api/auth/account/password", headers=cur, json={"current": "errada-errada", "new": NEWPW}).status_code == 403
    assert client.post("/api/auth/account/password", headers=cur, json={"current": PASSWORD, "new": "curta"}).status_code == 422
    assert client.post("/api/auth/account/password", headers=cur, json={"current": PASSWORD, "new": PASSWORD}).status_code == 422
    r = client.post("/api/auth/account/password", headers=cur, json={"current": PASSWORD, "new": NEWPW})
    assert r.status_code == 200
    fresh = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get("/api/auth/user/me", headers=old).status_code == 401      # sessões anteriores encerradas
    assert client.get("/api/auth/user/me", headers=cur).status_code == 401
    assert client.get("/api/auth/user/me", headers=fresh).status_code == 200    # a sessão que trocou continua
    assert client.post("/api/auth/user/login", json={"email": world.emails["a_user"], "password": PASSWORD}).status_code == 401
    login(client, world.emails["a_user"], "user", NEWPW)


def test_platform_session_changes_own_password_but_portal_handoff_session_cannot(client, world):
    p = platform_login(client, world.emails["root"])
    assert client.get("/api/auth/account", headers=p).status_code == 200
    # token preliminar de MFA não acessa a conta
    r = client.post("/api/auth/platform/login", json={"email": world.emails["root"], "password": PASSWORD})
    assert client.get("/api/auth/account", headers={"Authorization": f"Bearer {r.json()['mfaToken']}"}).status_code == 401
    t = client.post(f"/api/platform/arenas/{world.A.id}/gestao-ticket", headers=p).json()["ticket"]
    g = {"Authorization": f"Bearer {client.post('/api/auth/arena/portal-exchange', json={'ticket': t}).json()['token']}"}
    assert client.patch("/api/auth/account", headers=g, json={"name": "Hack Hack"}).status_code == 403
    assert client.post("/api/auth/account/password", headers=g, json={"current": PASSWORD, "new": NEWPW}).status_code == 403
    r = client.post("/api/auth/account/password", headers=p, json={"current": PASSWORD, "new": NEWPW})
    assert r.status_code == 200 and client.get("/api/platform/arenas", headers={"Authorization": f"Bearer {r.json()['token']}"}).status_code == 200


def test_google_only_account_cannot_change_password_here(client, world):
    from app.db import SessionLocal
    from app.models import User
    from app import userman
    db = SessionLocal(); u = db.get(User, world.uid["a_user"]); u.password_hash = userman.UNUSABLE; u.auth_method = "google"; db.commit(); db.close()
    from app.security import create_access_token
    h = {"Authorization": f"Bearer {create_access_token(world.uid['a_user'], 'user')}"}
    assert client.post("/api/auth/account/password", headers=h, json={"current": "qualquer", "new": NEWPW}).status_code == 409
