"""QR de acesso da arena: só equipe da própria arena gera; o QR apenas identifica a arena."""
import io

from PIL import Image


def _decode_png(content: bytes):
    img = Image.open(io.BytesIO(content))
    img.load()
    return img


def test_qr_requires_https_public_url(client, world, H, monkeypatch):
    monkeypatch.delenv("PUBLIC_APP_URL", raising=False)
    assert client.get(f"/api/arena/{world.A.id}/access-qr", headers=H("a_admin_arena")).status_code == 503
    monkeypatch.setenv("PUBLIC_APP_URL", "http://exemplo.test")
    assert client.get(f"/api/arena/{world.A.id}/access-qr", headers=H("a_admin_arena")).status_code == 503


def test_staff_gets_png_qr_for_own_arena(client, world, H, monkeypatch):
    monkeypatch.setenv("PUBLIC_APP_URL", "https://replay.example.test")
    for who in ("a_admin_arena", "a_op_arena"):
        r = client.get(f"/api/arena/{world.A.id}/access-qr", headers=H(who))
        assert r.status_code == 200, who
        assert r.headers["content-type"] == "image/png"
        assert r.headers["cache-control"] == "no-store"
        assert _decode_png(r.content).format == "PNG"


def test_athlete_and_anonymous_cannot_generate_qr(client, world, H):
    assert client.get(f"/api/arena/{world.A.id}/access-qr", headers=H("a_user")).status_code in (403, 404)
    assert client.get(f"/api/arena/{world.A.id}/access-qr").status_code == 401


def test_qr_is_isolated_between_arenas(client, world, H):
    assert client.get(f"/api/arena/{world.A.id}/access-qr", headers=H("b_admin_arena")).status_code == 404


def test_qr_does_not_grant_access_to_private_videos(client, world, H):
    """Conhecer o link/slug da arena não abre replays privados nem de outra arena."""
    hb = H("b_user")
    assert client.get(f"/api/videos/{world.A.priv}", headers=hb).status_code == 404
    ha = H("a_user")
    ids = [v["id"] for v in client.get("/api/videos?limit=50", headers=ha).json()["items"]]
    assert world.A.priv not in ids and world.A.pub in ids
