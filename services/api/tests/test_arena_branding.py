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


def test_upload_exige_admin_global_e_nao_cruza_arenas(client, world):
    url_a = f"/api/platform/arenas/{world.A.id}/logo"
    up = {"logo": ("arena.png", png(), "image/png")}
    # gestor da própria arena, operador, jogador e gestor de outra arena: nenhum pode (o Portal é só do admin global)
    for who, app in (("a_admin", "arena"), ("a_op", "arena"), ("a_user", "user"), ("b_admin", "arena")):
        h = login(client, world.emails[who], app)
        assert client.post(url_a, files=up, headers=h).status_code in (401, 403), who
        assert client.delete(url_a, headers=h).status_code in (401, 403), who
    assert client.delete(url_a).status_code in (401, 403)
    p = platform_login(client, world.emails["root"])
    assert client.post(url_a, files=up, headers=p).status_code == 200
    # a logo de A não aparece em B nem no jogador de B
    assert client.get(f"/api/platform/arenas/{world.B.id}", headers=p).json()["logoUrl"] is None
    jb = login(client, world.emails["b_user"], "user")
    assert all(a["logoUrl"] is None for a in client.get("/api/arenas", headers=jb).json())
    assert client.post(f"/api/platform/arenas/inexistente/logo", files=up, headers=p).status_code == 404


def test_upload_rejeita_arquivos_que_nao_sao_png(client, world):
    p = platform_login(client, world.emails["root"])
    url = f"/api/platform/arenas/{world.A.id}/logo"
    jpg = io.BytesIO()
    Image.new("RGB", (120, 80), (200, 0, 0)).save(jpg, format="JPEG")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><script>alert(1)</script></svg>'
    for name, data, ctype in (("a.png", jpg.getvalue(), "image/png"), ("a.png", svg, "image/png"), ("a.svg", svg, "image/svg+xml"),
                              ("a.png", b"", "image/png"), ("a.png", png()[:40], "image/png")):
        r = client.post(url, files={"logo": (name, data, ctype)}, headers=p)
        assert r.status_code in (413, 422), (name, r.status_code)
    small = io.BytesIO()
    Image.new("RGBA", (10, 10), (0, 0, 0, 255)).save(small, format="PNG")
    assert client.post(url, files={"logo": ("s.png", small.getvalue(), "image/png")}, headers=p).status_code == 422  # < 32 px
    big = io.BytesIO()
    Image.effect_noise((1100, 1100), 100).convert("RGBA").save(big, format="PNG")
    assert len(big.getvalue()) > 1024 * 1024
    assert client.post(url, files={"logo": ("b.png", big.getvalue(), "image/png")}, headers=p).status_code == 413
    assert client.get(f"/api/platform/arenas/{world.A.id}", headers=p).json()["logoUrl"] is None  # nada gravado


def test_logo_recodificada_e_arena_suspensa_nao_publica(client, world):
    from app import storage
    from app.db import SessionLocal
    from app.models import Arena
    p = platform_login(client, world.emails["root"])
    url, image = f"/api/platform/arenas/{world.A.id}/logo", f"/api/arenas/{world.A.id}/logo"
    # PNG com bloco de texto embutido: o servidor recodifica (metadados/payload removidos)
    buf = io.BytesIO()
    from PIL.PngImagePlugin import PngInfo
    meta = PngInfo(); meta.add_text("segredo", "PAYLOAD-EMBUTIDO")
    Image.new("RGBA", (64, 64), (1, 2, 3, 255)).save(buf, format="PNG", pnginfo=meta)
    assert b"PAYLOAD-EMBUTIDO" in buf.getvalue()
    assert client.post(url, files={"logo": ("a.png", buf.getvalue(), "image/png")}, headers=p).status_code == 200
    body = client.get(image).content
    assert b"PAYLOAD-EMBUTIDO" not in body
    # trocar a logo remove o arquivo anterior do armazenamento
    db = SessionLocal(); old = db.get(Arena, world.A.id).logo_key; db.close()
    assert client.post(url, files={"logo": ("b.png", png(), "image/png")}, headers=p).status_code == 200
    assert not storage.path_for(old).exists()
    # arena suspensa não serve a imagem publicamente
    client.patch(f"/api/platform/arenas/{world.A.id}", json={"status": "suspended"}, headers=p)
    assert client.get(image).status_code == 404
