"""Separação das três interfaces: nenhum token vale fora do seu aplicativo; autorização no servidor."""
import pytest

from app.main import app
from app.security import _totp_code, totp_now_step, verify_totp
from conftest import PASSWORD, login


def _routes(prefix):
    paths = app.openapi()["paths"]  # independe de como o FastAPI agrupa os routers
    return [(m.upper(), p) for p, ops in paths.items() if p.startswith(prefix) for m in ops]


def _fill(path, w):
    return path.replace("{arena_id}", w.A.id).replace("{video_id}", w.A.pub).replace("{export_id}", "x" * 32).replace(
        "{sponsor_id}", w.A.sponsor).replace("{campaign_id}", w.A.campaign).replace("{track_id}", "x" * 32).replace(
        "{court_id}", w.A.c1).replace("{member_id}", "x" * 32).replace("{agent_id}", "x" * 32).replace(
        "{plan_id}", "x" * 32).replace("{user_id}", w.uid["a_user"]).replace("{share_id}", "x" * 32).replace("{token}", "x")


def test_route_inventory_not_empty():
    assert len(_routes("/api/arena/")) > 25 and len(_routes("/api/platform/")) > 10


@pytest.mark.parametrize("prefix", ["/api/arena/", "/api/platform/"])
def test_admin_routes_reject_anonymous_and_user_token(client, world, H, prefix):
    """Varredura: TODA rota administrativa bloqueia anônimo (401) e token do app do usuário (403)."""
    for method, path in _routes(prefix):
        url = _fill(path, world)
        r = client.request(method, url)
        assert r.status_code == 401, (method, path, r.status_code)
        r = client.request(method, url, headers=H("a_user"))
        assert r.status_code == 403, (method, path, r.status_code)


def test_arena_token_blocked_from_platform_and_user_app_and_vice_versa(client, world, H):
    for method, path in _routes("/api/platform/"):
        r = client.request(method, _fill(path, world), headers=H("a_admin_arena"))
        assert r.status_code == 403, (method, path)
    for path in ("/api/videos", "/api/exports", "/api/arenas"):
        assert client.get(path, headers=H("a_admin_arena")).status_code == 403
        assert client.get(path, headers=H("root")).status_code == 403
    assert client.get(f"/api/arena/{world.A.id}", headers=H("root")).status_code == 403  # global admin não é gestor


def test_platform_token_cannot_reach_arena_data_even_for_global_admin(client, world, H):
    assert client.get(f"/api/arena/{world.A.id}/videos", headers=H("root")).status_code == 403


def test_logins_are_separate(client, world):
    # atleta não entra no app da arena nem no portal
    assert client.post("/api/auth/arena/login", json={"email": world.emails["a_user"], "password": PASSWORD}).status_code == 403
    assert client.post("/api/auth/platform/login", json={"email": world.emails["a_user"], "password": PASSWORD}).status_code == 403
    # gestor de arena não entra no portal
    assert client.post("/api/auth/platform/login", json={"email": world.emails["a_admin"], "password": PASSWORD}).status_code == 403
    # gestor entra no app da arena e também (como conta) no app do usuário
    assert client.post("/api/auth/arena/login", json={"email": world.emails["a_admin"], "password": PASSWORD}).status_code == 200
    assert client.post("/api/auth/user/login", json={"email": world.emails["a_admin"], "password": PASSWORD}).status_code == 200
    assert client.post("/api/auth/user/login", json={"email": world.emails["a_user"], "password": "errada-errada"}).status_code == 401


def test_platform_requires_mfa(client, world):
    r = client.post("/api/auth/platform/login", json={"email": world.emails["root"], "password": PASSWORD})
    assert r.json()["mfaStep"] == "enroll"
    h = {"Authorization": f"Bearer {r.json()['mfaToken']}"}
    # token pré-MFA não acessa nada do portal
    assert client.get("/api/platform/arenas", headers=h).status_code == 401
    secret = client.post("/api/auth/platform/mfa/setup", headers=h).json()["secret"]
    assert client.post("/api/auth/platform/mfa/verify", headers=h, json={"code": "000000"}).status_code == 401
    step = totp_now_step()
    ok = client.post("/api/auth/platform/mfa/verify", headers=h, json={"code": _totp_code(secret, step)})
    assert ok.status_code == 200
    full = {"Authorization": f"Bearer {ok.json()['token']}"}
    assert client.get("/api/platform/arenas", headers=full).status_code == 200
    # replay do mesmo código é recusado e o próximo login exige o passo "verify"
    r2 = client.post("/api/auth/platform/login", json={"email": world.emails["root"], "password": PASSWORD})
    assert r2.json()["mfaStep"] == "verify"
    h2 = {"Authorization": f"Bearer {r2.json()['mfaToken']}"}
    assert client.post("/api/auth/platform/mfa/verify", headers=h2, json={"code": _totp_code(secret, step)}).status_code == 401


def test_totp_rfc6238_vector():
    # RFC 6238: segredo ASCII "12345678901234567890", T=59s => 94287082 (8 dígitos) => 287082 com 6 dígitos
    import base64
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert verify_totp(secret, "287082", 0, now=59) == 1


def test_expired_or_forged_tokens_rejected(client, world):
    import jwt as pyjwt
    assert client.get("/api/arenas", headers={"Authorization": "Bearer lixo"}).status_code == 401
    forged = pyjwt.encode({"typ": "access", "sub": world.uid["a_user"], "app": "user", "scope": "full", "exp": 9999999999}, "outra-chave", "HS256")
    assert client.get("/api/arenas", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_login_rate_limit(client, world):
    from app.routers import auth
    auth.limiter.max = 3
    try:
        codes = [client.post("/api/auth/user/login", json={"email": "x@y.test", "password": "zzzzzzzzzzz"}).status_code for _ in range(5)]
    finally:
        auth.limiter.max = 1000
    assert codes[:3] == [401, 401, 401] and 429 in codes[3:]


def test_inactive_user_and_suspended_arena(client, world, H):
    h, adm = H("a_user"), H("a_admin_arena")
    assert client.get("/api/videos", headers=h).json()["items"]
    from app.db import SessionLocal
    from app.models import Arena
    db = SessionLocal()
    db.get(Arena, world.A.id).status = "suspended"
    db.commit()
    db.close()
    assert client.get("/api/videos", headers=h).json()["items"] == []
    assert client.get(f"/api/videos/{world.A.pub}", headers=h).status_code == 404
    assert client.get(f"/api/arena/{world.A.id}", headers=adm).status_code == 403
    # e um novo login no app da arena é recusado
    assert client.post("/api/auth/arena/login", json={"email": world.emails["a_admin"], "password": PASSWORD}).status_code == 403
