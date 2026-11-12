"""Login com Google com provedor SIMULADO (chave RSA local). Nenhuma credencial/rede real. Não prova homologação com projeto OAuth real."""
import time
import urllib.parse as up

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import google_oidc as g
from app.db import SessionLocal
from app.models import IntegrationSetting, OAuthFlow, User, UserIdentity
from app.routers import google_auth
from conftest import PASSWORD, login, platform_login

CID = "cliente-teste.apps.googleusercontent.com"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Fake(g.GoogleProvider):
    def __init__(self):
        self.claims = {}
        self.signer = KEY
        self.fail_exchange = False

    def auth_url(self, cfg, state, nonce, challenge, login_hint=None):
        return f"https://fake.google/auth?state={state}"

    def exchange(self, cfg, code, verifier):
        if self.fail_exchange:
            raise g.GoogleError("x")
        db = SessionLocal()
        nonce = db.query(OAuthFlow).filter_by(code_verifier=verifier).one().nonce
        db.close()
        now = int(time.time())
        c = {"iss": "https://accounts.google.com", "aud": CID, "sub": "sub-1", "email": "x@g.test", "email_verified": True,
             "iat": now, "exp": now + 300, "nonce": nonce, "name": "Fulano G", **self.claims}
        if c.get("nonce") == "__wrong__":
            c["nonce"] = "outro"
        return jwt.encode(c, self.signer, algorithm="RS256", headers={"kid": "k1"})

    def signing_key(self, id_token):
        return KEY.public_key()


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(g, "provider", f)
    google_auth.limiter.reset()
    db = SessionLocal()
    from app import secretbox
    db.add_all([IntegrationSetting(key=g.KEY_ID, value_enc=secretbox.seal(CID), updated_by="t"),
                IntegrationSetting(key=g.KEY_SECRET, value_enc=secretbox.seal("segredo-de-teste"), updated_by="t")])
    db.commit()
    db.close()
    return f


def run(client, app, **claims):
    """start -> (simulado) -> callback. Retorna (resposta do callback, fragmento como dict, state)."""
    r = client.get("/api/auth/google/start", params={"app": app}, follow_redirects=False)
    assert r.status_code == 303, r.text
    state = up.parse_qs(up.urlparse(r.headers["location"]).query)["state"][0]
    return callback(client, state, **claims), state


def callback(client, state, claims=None, **extra):
    r = client.get("/api/auth/google/callback", params={"state": state, "code": "abc", **extra}, follow_redirects=False)
    assert r.status_code == 303
    frag = up.parse_qs(up.urlparse(r.headers["location"]).fragment.split("?", 1)[1])
    return {k: v[0] for k, v in frag.items()}


def exch(client, frag, app):
    return client.post("/api/auth/google/exchange", json={"ticket": frag["ticket"], "app": app})


def set_user(world, who, **kw):
    db = SessionLocal()
    u = db.get(User, world.uid[who])
    for k, v in kw.items():
        setattr(u, k, v)
    db.commit()
    db.close()


def test_status_disabled_without_config(client):
    assert client.get("/api/auth/google/status").json() == {"enabled": False}
    r = client.get("/api/auth/google/start", params={"app": "user"}, follow_redirects=False)
    assert "err=google_nao_configurado" in r.headers["location"]


def test_new_player_created_via_google_without_admin_roles(client, world, fake):
    fake.claims = {"email": "novo@g.test", "sub": "sub-novo"}
    frag, _ = run(client, "user")
    r = exch(client, frag, "user")
    assert r.status_code == 200 and r.json()["user"]["email"] == "novo@g.test" and r.json()["user"]["globalRole"] is None
    # ticket de uso único
    assert exch(client, frag, "user").status_code == 401
    # mesmo sub depois: mesma conta, sem duplicar
    frag, _ = run(client, "user")
    assert exch(client, frag, "user").status_code == 200
    db = SessionLocal()
    assert db.query(User).filter_by(email="novo@g.test").count() == 1
    db.close()


def test_same_email_without_link_is_not_linked_automatically(client, world, fake):
    fake.claims = {"email": world.emails["a_user"], "sub": "sub-atacante"}
    frag, _ = run(client, "user")
    assert frag["err"] == "conta_existente"
    db = SessionLocal()
    assert db.query(UserIdentity).count() == 0
    db.close()


def test_invited_manager_links_via_google_in_arena_app_only_once(client, world, fake):
    p = platform_login(client, world.emails["root"])
    r = client.post(f"/api/platform/arenas/{world.A.id}/members", headers=p, json={
        "name": "Gestor Google", "email": "gestor@g.test", "role": "arena_admin", "authMethod": "google"})
    assert r.status_code == 201
    fake.claims = {"email": "gestor@g.test", "sub": "sub-gestor"}
    frag, _ = run(client, "arena")
    r = exch(client, frag, "arena")
    assert r.status_code == 200, frag
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get(f"/api/arena/{world.A.id}", headers=h).status_code == 200
    # outro sub com o mesmo e-mail: convite já consumido -> negado
    fake.claims = {"email": "gestor@g.test", "sub": "sub-outro"}
    frag, _ = run(client, "arena")
    assert frag["err"] == "conta_existente"


def test_google_does_not_grant_access_to_other_apps_or_roles(client, world, fake):
    # jogador com Google vinculado não entra no App Gestão nem no Portal
    fake.claims = {"email": "so-jogador@g.test", "sub": "sub-j"}
    frag, _ = run(client, "user")
    assert exch(client, frag, "user").status_code == 200
    for app in ("arena", "platform"):
        frag, _ = run(client, app)
        assert frag.get("err") == "sem_acesso"
    # ticket do app user não vale para outro app
    frag, _ = run(client, "user")
    assert exch(client, frag, "arena").status_code == 401


def test_inactive_or_suspended_cannot_use_google(client, world, fake):
    fake.claims = {"email": "novo2@g.test", "sub": "sub-n2"}
    assert exch(client, run(client, "user")[0], "user").status_code == 200
    db = SessionLocal()
    u = db.query(User).filter_by(email="novo2@g.test").one()
    u.active = False
    db.commit()
    db.close()
    assert run(client, "user")[0]["err"] == "inativo"


def test_platform_google_still_requires_totp(client, world, fake):
    p = platform_login(client, world.emails["root"])  # prepara MFA do root
    db = SessionLocal()
    from app.models import UserIdentity as UI
    db.add(UI(user_id=world.uid["root"], provider="google", subject="sub-root", email=world.emails["root"]))
    root = db.get(User, world.uid["root"])
    root.auth_method = "both"
    db.commit()
    db.close()
    fake.claims = {"email": world.emails["root"], "sub": "sub-root"}
    frag, _ = run(client, "platform")
    r = exch(client, frag, "platform")
    assert r.status_code == 200 and "token" not in r.json() and r.json()["mfaStep"] == "verify"
    # o mfaToken sozinho não acessa o Portal
    h = {"Authorization": f"Bearer {r.json()['mfaToken']}"}
    assert client.get("/api/platform/users", headers=h).status_code == 401


@pytest.mark.parametrize("claims,signer", [
    ({"aud": "outro-cliente"}, KEY), ({"iss": "https://evil.test"}, KEY), ({"nonce": "__wrong__"}, KEY),
    ({"exp": 1000, "iat": 900}, KEY), ({"email_verified": False}, KEY), ({}, OTHER),
])
def test_invalid_id_tokens_rejected(client, world, fake, claims, signer):
    fake.claims, fake.signer = {"email": "x@g.test", **claims}, signer
    frag, _ = run(client, "user")
    assert frag.get("err") == "falha" and "ticket" not in frag


def test_cancelled_and_exchange_failure(client, world, fake):
    r = client.get("/api/auth/google/start", params={"app": "user"}, follow_redirects=False)
    state = up.parse_qs(up.urlparse(r.headers["location"]).query)["state"][0]
    r = client.get("/api/auth/google/callback", params={"state": state, "error": "access_denied"}, follow_redirects=False)
    assert "err=cancelado" in r.headers["location"]
    fake.fail_exchange = True
    assert run(client, "user")[0]["err"] == "falha"


def test_state_is_single_use_and_unknown_state_rejected(client, world, fake):
    frag, state = run(client, "user")
    assert "ticket" in frag
    again = client.get("/api/auth/google/callback", params={"state": state, "code": "abc"}, follow_redirects=False)
    assert "err=falha" in again.headers["location"]
    bad = client.get("/api/auth/google/callback", params={"state": "inventado", "code": "abc"}, follow_redirects=False)
    assert "err=falha" in bad.headers["location"]


def test_link_and_unlink_for_logged_user(client, world, fake):
    h = login(client, world.emails["a_user"], "user")
    r = client.post("/api/auth/google/link/start", json={"app": "user"}, headers=h)
    assert r.status_code == 200
    state = up.parse_qs(up.urlparse(r.json()["url"]).query)["state"][0]
    fake.claims = {"email": "pessoal@g.test", "sub": "sub-link"}  # e-mail Google pode diferir; vínculo é pelo sub
    assert callback(client, state) == {"linked": "1"}
    assert client.get("/api/auth/google/identity", headers=h).json()["linked"] is True
    frag, _ = run(client, "user")
    assert exch(client, frag, "user").json()["user"]["id"] == world.uid["a_user"]
    # mesmo Google em outra conta é recusado
    h2 = login(client, world.emails["b_user"], "user")
    state = up.parse_qs(up.urlparse(client.post("/api/auth/google/link/start", json={"app": "user"}, headers=h2).json()["url"]).query)["state"][0]
    assert callback(client, state)["err"] == "falha"
    # desvincular (há senha) e senha continua funcionando
    assert client.delete("/api/auth/google/link", headers=h).status_code == 200
    login(client, world.emails["a_user"], "user", PASSWORD)
    # sem identidade: Google deixa de logar
    fake.claims = {"email": world.emails["a_user"], "sub": "sub-link"}
    assert run(client, "user")[0]["err"] == "conta_existente"


def test_cannot_unlink_when_google_is_only_method(client, world, fake):
    fake.claims = {"email": "so@g.test", "sub": "sub-so"}
    r = exch(client, run(client, "user")[0], "user")
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.delete("/api/auth/google/link", headers=h).status_code == 409


def test_config_is_write_only_and_manager_cannot_touch_it(client, world):
    p = platform_login(client, world.emails["root"])
    r = client.put("/api/platform/integrations/google", headers=p, json={"clientId": CID, "clientSecret": "super-segredo-123"})
    assert r.status_code == 200 and r.json()["secretSet"] and "super-segredo" not in r.text
    assert "super-segredo" not in client.get("/api/platform/integrations/google", headers=p).text
    db = SessionLocal()
    assert "super-segredo" not in db.get(IntegrationSetting, g.KEY_SECRET).value_enc  # cifrado em repouso
    db.close()
    from app.models import AuditLog
    db = SessionLocal()
    assert all("super-segredo" not in a.changed_fields for a in db.query(AuditLog))
    db.close()
    h = login(client, world.emails["a_admin"], "arena")
    assert client.get("/api/platform/integrations/google", headers=h).status_code in (401, 403)
    assert client.put("/api/platform/integrations/google", headers=h, json={"clientId": CID, "clientSecret": "xxxxxxxxxx"}).status_code in (401, 403)
    assert client.get("/api/auth/google/status").json() == {"enabled": True}
