"""Matriz de permissões por operação: operador (leitura), gestor (áudio/músicas/patrocínio/consulta), global (tudo), jogador (nada)."""
import io

from PIL import Image

from app.db import SessionLocal
from app.models import Arena
from app.security import _encode
from conftest import login, platform_login


NEWM = {"email": "n@t.test", "role": "user"}


def png():
    b = io.BytesIO(); Image.new("RGBA", (64, 64), (0, 0, 0, 255)).save(b, "PNG"); return b.getvalue()


def hdr(client, w, who):
    return platform_login(client, w.emails["root"]) if who == "root" else login(client, w.emails[who], "user" if who.endswith("user") else "arena")


def test_operator_can_only_read(client, world):
    op, B = hdr(client, world, "a_op"), f"/api/arena/{world.A.id}"
    for path in ("", "/dashboard", "/courts", "/videos", "/sponsors", "/campaigns", "/music", "/music/usage"):
        assert client.get(B + path, headers=op).status_code == 200, path
    for path in ("/diagnostics", "/runtime", "/storage", "/agent-resources"):                # técnico: só o administrador global
        assert client.get(B + path, headers=op).status_code == 403, path
    assert client.post(f"{B}/courts/{world.A.c1}/test-trigger", headers=op).status_code == 403
    denied = [("patch", "", {"json": {"name": "X Y Z"}}), ("post", "/courts", {"json": {"label": "Q9", "localCourtKey": "A_09"}}),
              ("post", "/sponsors", {"data": {"name": "N"}, "files": {"logo": ("l.png", png(), "image/png")}}), ("post", "/music", {}),
              ("get", "/agents", {}), ("post", "/agents", {"json": {"label": "x"}}), ("get", "/audit", {}),
              ("patch", f"/videos/{world.A.pub}", {"json": {"visibility": "private"}}), ("delete", f"/videos/{world.A.pub}?confirm=true", {}),
              ("put", "/agent-resources", {"json": {"maxFfmpeg": 2}}), ("put", f"/courts/{world.A.c1}/storage-policy", {"json": {"storageLimitMb": 10}})]
    for m, path, kw in denied:
        assert getattr(client, m)(B + path, headers=op, **kw).status_code == 403, (m, path)


def test_manager_scope_audio_music_sponsors_consultation_only(client, world):
    ga, B = hdr(client, world, "a_admin"), f"/api/arena/{world.A.id}"
    # pode: personalização de áudio, biblioteca, patrocínio e consulta
    assert client.patch(B, headers=ga, json={"defaultMode": "music", "allowedModes": ["original", "music"]}).status_code == 200
    assert client.patch(f"{B}/music/{world.A.tracks['ok']}", headers=ga, json={"status": "disabled"}).status_code == 200
    assert client.post(f"{B}/sponsors", headers=ga, data={"name": "Marca Gestor"}, files={"logo": ("l.png", png(), "image/png")}).status_code == 201
    assert client.get(f"{B}/videos/{world.A.pub}/personalization", headers=ga).status_code == 200
    # não pode: quadras, servidores/agentes, políticas técnicas, publicar/ocultar/excluir, auditoria global, motor de captura
    for m, path, kw in [("post", "/courts", {"json": {"label": "Q9", "localCourtKey": "A_09"}}), ("patch", f"/courts/{world.A.c1}", {"json": {"label": "Z Z"}}),
                        ("post", "/agents", {"json": {"label": "x"}}), ("get", "/agents", {}), ("get", "/audit", {}),
                        ("patch", f"/videos/{world.A.priv}", {"json": {"visibility": "arena"}}), ("delete", f"/videos/{world.A.priv}?confirm=true", {}),
                        ("get", "/diagnostics", {}), ("get", "/runtime", {}), ("get", "/storage", {}), ("post", f"/courts/{world.A.c1}/test-trigger", {}),
                        ("get", "/members", {}), ("post", "/members", {"json": NEWM}),
                        ("put", "/agent-resources", {"json": {"maxFfmpeg": 2}}), ("put", f"/courts/{world.A.c1}/storage-policy", {"json": {"storageLimitMb": 10}}),
                        ("post", f"/videos/{world.A.pub}/protection", {"json": {"protected": True}})]:
        assert getattr(client, m)(B + path, headers=ga, **kw).status_code in (403, 404, 405), (m, path)
    # nem enxerga outra arena
    assert client.get(f"/api/arena/{world.B.id}/music", headers=ga).status_code == 404
    assert client.get(f"/api/arena/{world.B.id}/videos/{world.B.pub}/personalization", headers=ga).status_code == 404


def test_player_has_no_admin_access_and_expired_or_wrong_tokens_fail(client, world):
    ju, B = hdr(client, world, "a_user"), f"/api/arena/{world.A.id}"
    assert client.get(B, headers=ju).status_code == 403 and client.get("/api/platform/arenas", headers=ju).status_code == 403
    expired = _encode({"typ": "access", "sub": world.uid["root"], "app": "platform", "mfa": True, "scope": "full"}, -5)
    assert client.get(B, headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    assert client.get(B, headers={"Authorization": "Bearer abc.def.ghi"}).status_code == 401


def test_suspended_arena_blocks_manager_and_global(client, world):
    ga, root = hdr(client, world, "a_admin"), hdr(client, world, "root")
    db = SessionLocal(); db.get(Arena, world.A.id).status = "suspended"; db.commit(); db.close()
    B = f"/api/arena/{world.A.id}"
    assert client.get(B, headers=ga).status_code == 403 and client.get(B, headers=root).status_code == 403


def test_global_admin_does_everything_the_gestao_forbids_to_managers(client, world):
    root, B = hdr(client, world, "root"), f"/api/arena/{world.A.id}"
    assert client.post(f"{B}/courts", headers=root, json={"label": "Q9", "localCourtKey": "A_09"}).status_code in (201, 409)
    assert client.post(f"{B}/agents", headers=root, json={"label": "motor"}).status_code == 201
    assert client.get(f"{B}/audit", headers=root).status_code == 200
    assert client.put(f"{B}/courts/{world.A.c1}/storage-policy", headers=root, json={"storageLimitMb": 400, "retentionDays": 10}).status_code == 200
    assert client.patch(f"{B}/videos/{world.A.pub}", headers=root, json={"visibility": "private", "reason": "teste"}).status_code == 200
