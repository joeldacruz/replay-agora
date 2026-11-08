"""Identidade visual da arena, autorização, isolamento e entrega ao jogador."""
import io

from PIL import Image
from conftest import login, platform_login


def png():
    out = io.BytesIO()
    Image.new("RGBA", (120, 80), (0, 90, 180, 255)).save(out, format="PNG")
    return out.getvalue()


def test_logo_da_arena_upload_visibilidade_e_remocao(client, world):
    p = platform_login(client, world.emails["root"])
    jogador = login(client, world.emails["a_user"], "user")
    url = f"/api/platform/arenas/{world.A.id}/logo"
    image = f"/api/arenas/{world.A.id}/logo"

    assert client.get(image).status_code == 404
    assert client.post(url, files={"logo": ("arena.png", png(), "image/png")}).status_code in (401, 403)
    assert client.post(url, files={"logo": ("arena.png", png(), "image/png")}, headers=p).status_code == 200

    r = client.get(image)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert r.headers["cache-control"] == "no-store"
    assert client.get(f"/api/arenas/{world.B.id}/logo").status_code == 404

    my = client.get("/api/arenas", headers=jogador).json()
    assert next(a for a in my if a["id"] == world.A.id)["logoUrl"] == image
    assert client.get(f"/api/platform/arenas/{world.A.id}", headers=p).json()["logoUrl"] == image

    bad = client.post(url, files={"logo": ("arena.png", b"not-png", "image/png")}, headers=p)
    assert bad.status_code == 422
    assert client.get(image).status_code == 200  # logo anterior mantida

    assert client.delete(url, headers=p).status_code == 204
    assert client.get(image).status_code == 404
    assert client.get(f"/api/platform/arenas/{world.A.id}", headers=p).json()["logoUrl"] is None
