"""Configuração remota — API do Portal: acesso, isolamento entre arenas, segredos, validação anti-SSRF, concorrência, molduras (upload seguro)."""
import io
import json
import zipfile
from pathlib import Path

import pytest

from app.db import SessionLocal
from app.models import AuditLog, Camera, ConfigRevision

DATA = Path(__file__).parent / "data"
HPNG, VPNG = (DATA / "Moldura_Arena_Vibe_Horizontal.png").read_bytes(), (DATA / "Moldura_Arena_Vibe_Vertical.png").read_bytes()
P = "/api/platform"


def mk_server(client, h, arena_id, name="srv-vibe"):
    r = client.post(f"{P}/servers", headers=h, json={"arenaId": arena_id, "name": name})
    assert r.status_code == 201, r.text
    return r.json()


def mk_camera(client, h, arena_id, **kw):
    body = {"arenaId": arena_id, "name": "Câmera Q1", "host": "192.168.1.19", "rtspPort": 554, "rtspPath": "/cam/realmonitor?channel=1&subtype=0",
            "username": "admin", "password": "S3nh@-Secreta-123", "fps": 25, **kw}
    r = client.post(f"{P}/cameras", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def court_id(client, h, arena_id, n=0):
    return client.get(f"{P}/arenas/{arena_id}/courts", headers=h).json()[n]["id"]


# ---------------------------------------------------------------- acesso e isolamento
def test_portal_config_requires_global_admin_with_mfa(client, world, H):
    for who in ("a_admin_arena", "a_op_arena"):
        h = H(who)
        for path in ("/servers", f"/cameras?arenaId={world.A.id}", "/overview", "/monitoring", f"/frames?arenaId={world.A.id}", "/camera-profiles"):
            assert client.get(P + path, headers=h).status_code in (401, 403), (who, path)
    assert client.get(P + "/servers", headers=H("a_user")).status_code in (401, 403)
    assert client.get(P + "/servers").status_code == 401
    assert client.get(P + "/servers", headers=H("root")).status_code == 200


def test_arena_manager_cannot_reach_camera_secrets_or_remote_apis(client, world, H):
    root = H("root")
    cam = mk_camera(client, root, world.A.id)
    ha = H("a_admin_arena")
    assert client.get(f"{P}/cameras/{cam['id']}", headers=ha).status_code in (401, 403)
    assert client.put(f"{P}/cameras/{cam['id']}/secret", headers=ha, json={"password": "x1234567"}).status_code in (401, 403)
    # o App da Arena nunca recebe campos técnicos de câmera
    for path in ("", "/courts", "/dashboard"):
        txt = client.get(f"/api/arena/{world.A.id}{path}", headers=ha).text
        assert "rtsp" not in txt.lower() and "S3nh@" not in txt and "secret" not in txt.lower()


def test_cross_arena_references_are_rejected(client, world, H):
    root = H("root")
    sa, sb = mk_server(client, root, world.A.id, "srv-a")["server"], mk_server(client, root, world.B.id, "srv-b")["server"]
    cb = court_id(client, root, world.B.id)
    # câmera da arena A não pode apontar para quadra/servidor da arena B
    assert client.post(f"{P}/cameras", headers=root, json={"arenaId": world.A.id, "name": "x1", "host": "192.168.1.5", "courtId": cb}).status_code == 404
    assert client.post(f"{P}/cameras", headers=root, json={"arenaId": world.A.id, "name": "x2", "host": "192.168.1.5", "serverId": sb["id"]}).status_code == 404
    # controlador: quadra de outra arena
    ctl = client.post(f"{P}/controllers", headers=root, json={"serverId": sa["id"], "name": "Arduino", "portId": "dev:ttyUSB0"}).json()
    r = client.post(f"{P}/controllers/{ctl['id']}/inputs", headers=root, json={"inputRef": "REPLAY_01", "courtId": cb})
    assert r.status_code == 404
    # moldura da arena B não pode ser usada por quadra da arena A
    fb = client.post(f"{P}/frames", headers=root, data={"arenaId": world.B.id, "name": "Moldura B"}, files={"file": ("h.png", HPNG, "image/png")}).json()[0]
    ca = court_id(client, root, world.A.id)
    cs = client.put(f"{P}/courts/{ca}/settings", headers=root, json={"expectedVersion": 1, "serverId": sa["id"], "frameHId": fb["id"]})
    assert cs.status_code == 404


# ---------------------------------------------------------------- segredos
def test_camera_password_is_write_only_everywhere(client, world, H):
    root = H("root")
    cam = mk_camera(client, root, world.A.id)
    secret = "S3nh@-Secreta-123"
    assert cam["hasSecret"] is True and secret not in json.dumps(cam)
    for url in (f"{P}/cameras/{cam['id']}", f"{P}/cameras?arenaId={world.A.id}"):
        assert secret not in client.get(url, headers=root).text
    r = client.put(f"{P}/cameras/{cam['id']}/secret", headers=root, json={"password": "Nova-Senha-456!"})
    assert r.status_code == 200 and "Nova-Senha-456" not in r.text and secret not in r.text and r.json()["secretVersion"] == 2
    db = SessionLocal()
    row = db.get(Camera, cam["id"])
    assert row.secret_enc and secret not in row.secret_enc and "Nova-Senha" not in row.secret_enc       # cifrado em repouso
    blob = " ".join(a.changed_fields for a in db.query(AuditLog))
    db.close()
    assert secret not in blob and "Nova-Senha-456" not in blob                                            # auditoria sem senha
    assert client.put(f"{P}/cameras/{cam['id']}/secret", headers=root, json={"password": "a\x01b"}).status_code == 422


# ---------------------------------------------------------------- validação (SSRF e injeção)
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "169.254.169.254", "0.0.0.0", "::1", "224.0.0.1", "rtsp://cam", "10.0.0.1/x", "a b", "user@10.0.0.2", "10.0.0.2:554"])
def test_camera_host_rejects_ssrf_targets(client, world, H, host):
    r = client.post(f"{P}/cameras", headers=H("root"), json={"arenaId": world.A.id, "name": "Cam", "host": host})
    assert r.status_code == 422, (host, r.text)


@pytest.mark.parametrize("path", ["cam/x", "/a/../b", "/x@evil", "rtsp://evil/x", "/x y", "/" + "a" * 250])
def test_camera_path_rejects_injection(client, world, H, path):
    r = client.post(f"{P}/cameras", headers=H("root"), json={"arenaId": world.A.id, "name": "Cam", "host": "192.168.1.9", "rtspPath": path})
    assert r.status_code == 422, (path, r.text)


def test_private_lan_addresses_and_hostnames_are_allowed(client, world, H):
    root = H("root")
    for h in ("192.168.1.18", "10.20.30.40", "camera-q2.arena.lan"):
        assert client.post(f"{P}/cameras", headers=root, json={"arenaId": world.A.id, "name": f"c-{h}", "host": h}).status_code == 201


def test_camera_conflict_detection_on_concurrent_edit(client, world, H):
    root = H("root")
    cam = mk_camera(client, root, world.A.id)
    ok = client.patch(f"{P}/cameras/{cam['id']}", headers=root, json={"expectedVersion": cam["version"], "name": "Novo nome"})
    assert ok.status_code == 200 and ok.json()["version"] == cam["version"] + 1
    stale = client.patch(f"{P}/cameras/{cam['id']}", headers=root, json={"expectedVersion": cam["version"], "name": "Outro nome"})
    assert stale.status_code == 409 and stale.json()["detail"]["currentVersion"] == cam["version"] + 1


# ---------------------------------------------------------------- controladores
def test_controller_respects_firmware_capacity_and_unique_inputs(client, world, H):
    root = H("root")
    s = mk_server(client, root, world.A.id)["server"]
    c1, c2 = (court_id(client, root, world.A.id, i) for i in (0, 1))
    for c in (c1, c2):
        client.put(f"{P}/courts/{c}/settings", headers=root, json={"expectedVersion": 1, "serverId": s["id"]})
    ctl = client.post(f"{P}/controllers", headers=root, json={"serverId": s["id"], "name": "Nano", "portId": "by-id:usb-1a86_USB_Serial-if00-port0", "inputsCapacity": 1}).json()
    ok = client.post(f"{P}/controllers/{ctl['id']}/inputs", headers=root, json={"inputRef": "REPLAY_01", "courtId": c1})
    assert ok.status_code == 201
    over = client.post(f"{P}/controllers/{ctl['id']}/inputs", headers=root, json={"inputRef": "REPLAY_02", "courtId": c2})
    assert over.status_code == 422 and "firmware" in json.dumps(over.json(), ensure_ascii=False)    # não presume mais entradas que o firmware
    assert client.patch(f"{P}/controllers/{ctl['id']}", headers=root, json={"expectedVersion": 99, "name": "Zzz"}).status_code == 409
    bad = client.post(f"{P}/controllers", headers=root, json={"serverId": s["id"], "name": "Ruim", "portId": "/etc/passwd"})
    assert bad.status_code == 422


# ---------------------------------------------------------------- molduras: upload seguro
def _zip(entries: dict[str, bytes], symlink: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in entries.items():
            z.writestr(n, b)
        if symlink:
            zi = zipfile.ZipInfo(symlink)
            zi.external_attr = (0o120777 << 16)
            z.writestr(zi, "/etc/passwd")
    return buf.getvalue()


def upload(client, h, arena_id, data, name="Moldura", fname="m.png", ctype="image/png"):
    return client.post(f"{P}/frames", headers=h, data={"arenaId": arena_id, "name": name}, files={"file": (fname, data, ctype)})


def test_import_vibe_beach_frames_png_and_zip(client, world, H):
    root = H("root")
    r = upload(client, root, world.A.id, HPNG, "Vibe Beach Horizontal")
    assert r.status_code == 201, r.text
    f = r.json()[0]
    assert f["orientation"] == "horizontal" and (f["current"]["width"], f["current"]["height"]) == (1118, 904)
    assert f["current"]["window"]["w"] > 1000 and f["current"]["window"]["h"] > 400                      # janela do vídeo detectada
    z = _zip({"Moldura_Arena_Vibe_Horizontal.png": HPNG, "Moldura_Arena_Vibe_Vertical.png": VPNG})
    r2 = client.post(f"{P}/frames", headers=root, data={"arenaId": world.B.id, "name": "Vibe"}, files={"file": ("pack.zip", z, "application/zip")})
    assert r2.status_code == 201 and sorted(x["orientation"] for x in r2.json()) == ["horizontal", "vertical"]
    # transparência preservada (arquivo servido ainda tem alfa)
    from PIL import Image
    img = client.get(f"{P}/frames/{f['id']}/image", headers=root)
    assert img.status_code == 200 and Image.open(io.BytesIO(img.content)).mode == "RGBA"
    assert Image.open(io.BytesIO(img.content)).getchannel("A").getextrema()[0] == 0


@pytest.mark.parametrize("label,data", [
    ("traversal", _zip({"../../evil.png": HPNG})),
    ("absoluto", _zip({"/etc/evil.png": HPNG})),
    ("windows", _zip({"C:\\x\\evil.png": HPNG})),
    ("não-png no zip", _zip({"a.png": HPNG, "script.sh": b"#!/bin/sh"})),
    ("png falso", _zip({"a.png": b"not a png at all" * 20})),
    ("vazio", _zip({})),
    ("symlink", _zip({"ok.png": HPNG}, symlink="link.png")),
    ("zip corrompido", b"PK\x03\x04" + b"\x00" * 40),
])
def test_zip_upload_rejects_dangerous_content(client, world, H, label, data):
    r = client.post(f"{P}/frames", headers=H("root"), data={"arenaId": world.A.id, "name": "x"}, files={"file": ("p.zip", data, "application/zip")})
    assert r.status_code == 422, (label, r.status_code, r.text)


def test_png_upload_validation(client, world, H):
    from PIL import Image
    root = H("root")
    opaque = io.BytesIO()
    Image.new("RGB", (400, 400), (10, 20, 30)).save(opaque, "PNG")
    assert upload(client, root, world.A.id, opaque.getvalue(), "sem alfa").status_code == 422             # precisa de área transparente
    tiny = io.BytesIO()
    Image.new("RGBA", (40, 40), (0, 0, 0, 0)).save(tiny, "PNG")
    assert upload(client, root, world.A.id, tiny.getvalue(), "pequena").status_code == 422
    assert upload(client, root, world.A.id, b"GIF89a" + b"0" * 100, "gif").status_code == 422
    assert upload(client, root, world.A.id, HPNG[:5000], "truncado").status_code == 422
    assert upload(client, root, world.A.id, b"", "vazio").status_code == 422
    assert upload(client, root, world.A.id, HPNG, "").status_code == 422                                  # nome obrigatório
    assert upload(client, root, world.A.id, HPNG, "ok").status_code == 201
    assert upload(client, root, world.A.id, HPNG, "ok").status_code == 409                                # nome repetido na arena


def test_frame_library_is_per_arena_versioned_and_protected(client, world, H):
    root = H("root")
    f = upload(client, root, world.A.id, HPNG, "Vibe H").json()[0]
    # outra arena não enxerga
    assert client.get(f"{P}/frames?arenaId={world.B.id}", headers=root).json() == []
    # nova versão (substituir arquivo) e composição versionada
    v2 = client.post(f"{P}/frames/{f['id']}/versions", headers=root, data={"note": "troca"}, files={"file": ("h.png", HPNG, "image/png")})
    assert v2.status_code == 201 and v2.json()["current"]["version"] == 2
    lay = client.put(f"{P}/frames/{f['id']}/layout", headers=root, json={"layout": {"frameFit": "contain", "frameScale": 0.9, "background": "black"}})
    assert lay.status_code == 201 and lay.json()["current"]["version"] == 3 and lay.json()["current"]["layout"]["background"] == "black"
    assert len(lay.json()["versions"]) == 3
    assert client.put(f"{P}/frames/{f['id']}/layout", headers=root, json={"layout": {"frameFit": "stretch"}}).status_code == 422
    assert client.put(f"{P}/frames/{f['id']}/layout", headers=root, json={"layout": {"naoExiste": 1}}).status_code == 422
    rest = client.post(f"{P}/frames/{f['id']}/restore/1", headers=root)
    assert rest.status_code == 201 and rest.json()["current"]["version"] == 4
    # em uso por quadra: não exclui
    s = mk_server(client, root, world.A.id)["server"]
    cid = court_id(client, root, world.A.id)
    assert client.put(f"{P}/courts/{cid}/settings", headers=root, json={"expectedVersion": 1, "serverId": s["id"], "frameHId": f["id"]}).status_code == 200
    assert client.delete(f"{P}/frames/{f['id']}", headers=root).status_code == 409
    assert client.put(f"{P}/courts/{cid}/settings", headers=root, json={"expectedVersion": 2, "serverId": s["id"], "frameHId": None}).status_code == 200
    assert client.delete(f"{P}/frames/{f['id']}", headers=root).status_code == 204
    # moldura vertical não pode ser a horizontal da quadra
    fv = upload(client, root, world.A.id, VPNG, "Vibe V").json()[0]
    assert client.put(f"{P}/courts/{cid}/settings", headers=root, json={"expectedVersion": 3, "serverId": s["id"], "frameHId": fv["id"]}).status_code == 422


def test_frame_preview_is_real_ffmpeg_and_keeps_aspect(client, world, H):
    from PIL import Image
    root = H("root")
    f = upload(client, root, world.A.id, HPNG, "Vibe H").json()[0]
    r = client.post(f"{P}/frames/{f['id']}/preview", headers=root, json={"size": "1920x1080"})
    assert r.status_code == 200, r.text
    img = Image.open(io.BytesIO(r.content))
    assert img.size == (1920, 1080)
    geom = json.loads(r.headers["x-geometry"])
    ratio = geom["frame"]["w"] / geom["frame"]["h"]
    assert abs(ratio - 1118 / 904) < 0.01, "a moldura não pode ser esticada"
    # vertical em 1080x1920
    fv = upload(client, root, world.A.id, VPNG, "Vibe V").json()[0]
    rv = client.post(f"{P}/frames/{fv['id']}/preview", headers=root, json={"size": "1080x1920"})
    assert rv.status_code == 200 and Image.open(io.BytesIO(rv.content)).size == (1080, 1920)
    assert abs(json.loads(rv.headers["x-geometry"])["frame"]["w"] / json.loads(rv.headers["x-geometry"])["frame"]["h"] - 553 / 940) < 0.01
    assert client.post(f"{P}/frames/{f['id']}/preview", headers=root, json={"size": "99999x10"}).status_code == 422
    assert client.post(f"{P}/frames/{f['id']}/preview", headers=root, json={"layout": {"background": "javascript:1"}}).status_code == 422


# ---------------------------------------------------------------- revisões, concorrência e validação do conjunto
def test_revision_validation_and_stale_editor_conflict(client, world, H):
    root = H("root")
    s = mk_server(client, root, world.A.id)["server"]
    cid = court_id(client, root, world.A.id)
    client.put(f"{P}/courts/{cid}/settings", headers=root, json={"expectedVersion": 1, "serverId": s["id"]})
    # quadra ativa sem câmera => inválido
    r = client.post(f"{P}/servers/{s['id']}/revisions", headers=root, json={"expectedVersion": 0, "apply": True})
    assert r.status_code == 422 and any("sem câmera" in e for e in r.json()["detail"]["errors"])
    cam = mk_camera(client, root, world.A.id, courtId=cid, serverId=s["id"])
    ok = client.post(f"{P}/servers/{s['id']}/revisions", headers=root, json={"expectedVersion": 0, "note": "primeira"})
    assert ok.status_code == 201 and ok.json()["version"] == 1 and ok.json()["status"] == "salvo"
    assert "S3nh@" not in json.dumps(ok.json())                                                         # snapshot sem segredo
    # editor com versão antiga não sobrescreve
    stale = client.post(f"{P}/servers/{s['id']}/revisions", headers=root, json={"expectedVersion": 0})
    assert stale.status_code == 409 and stale.json()["detail"]["currentVersion"] == 1
    d = client.get(f"{P}/servers/{s['id']}/config", headers=root).json()
    assert d["latestVersion"] == 1 and d["applied"] is None and d["errors"] == []
    # ajuste inválido de captura
    bad = client.put(f"{P}/courts/{cid}/settings", headers=root, json={"expectedVersion": 2, "serverId": s["id"], "preS": 60, "postS": 30, "bufferS": 30})
    assert bad.status_code == 200
    r2 = client.post(f"{P}/servers/{s['id']}/revisions", headers=root, json={"expectedVersion": 1})
    assert r2.status_code == 422 and any("buffer" in e for e in r2.json()["detail"]["errors"])
    assert cam["id"]


def test_revoked_server_blocks_config_and_agent(client, world, H):
    root = H("root")
    out = mk_server(client, root, world.A.id)
    s, cred = out["server"], out["credentials"]
    assert set(cred) == {"token", "signingKey", "expiresAt"} and "credentials" not in client.get(f"{P}/servers/{s['id']}", headers=root).text
    assert client.post(f"{P}/servers/{s['id']}/revoke", headers=root).json()["state"] == "revoked"
    r = client.post(f"{P}/servers/{s['id']}/revisions", headers=root, json={"expectedVersion": 0})
    assert r.status_code == 409
    # token do agente revogado deixa de valer
    from replay_agent.client import CentralClient, CentralRejected
    c = CentralClient(client, cred["token"], "node-revog-0001", cred["signingKey"])
    with pytest.raises(CentralRejected):
        c.call("POST", "/register", {"nodeId": "node-revog-0001"})
    # reemissão: novas credenciais valem; as antigas não
    new = client.post(f"{P}/servers/{s['id']}/credentials", headers=root).json()["credentials"]
    assert new["token"] != cred["token"]
    assert CentralClient(client, new["token"], "node-revog-0002", new["signingKey"]).call("POST", "/register", {"nodeId": "node-revog-0002"})["arenaId"] == world.A.id
    with pytest.raises(CentralRejected):
        CentralClient(client, cred["token"], "node-revog-0001", cred["signingKey"]).call("POST", "/register", {"nodeId": "node-revog-0001"})


def test_tasks_are_closed_list_and_disruptive_needs_confirmation(client, world, H):
    root = H("root")
    s = mk_server(client, root, world.A.id)["server"]
    for kind in ("shell", "exec", "reboot", "ssh"):
        assert client.post(f"{P}/servers/{s['id']}/tasks", headers=root, json={"kind": kind}).status_code == 422
    assert client.post(f"{P}/servers/{s['id']}/tasks", headers=root, json={"kind": "service_action", "service": "sshd", "serviceAction": "restart_component"}).status_code == 422
    assert client.post(f"{P}/servers/{s['id']}/tasks", headers=root, json={"kind": "service_action", "service": "agent", "serviceAction": "restart_component"}).status_code == 422
    need = client.post(f"{P}/servers/{s['id']}/tasks", headers=root, json={"kind": "service_action", "service": "capture", "serviceAction": "restart_component"})
    assert need.status_code == 409 and need.json()["detail"]["requiresConfirm"] is True
    ok = client.post(f"{P}/servers/{s['id']}/tasks", headers=root, json={"kind": "service_action", "service": "capture", "serviceAction": "restart_component", "confirm": True})
    assert ok.status_code == 201
    assert client.post(f"{P}/servers/{s['id']}/tasks", headers=root, json={"kind": "ping"}).status_code == 201


def test_audit_records_portal_mutations(client, world, H):
    root = H("root")
    s = mk_server(client, root, world.A.id)["server"]
    mk_camera(client, root, world.A.id, serverId=s["id"])
    db = SessionLocal()
    acts = {a.action for a in db.query(AuditLog)}
    db.close()
    assert {"platform.server_create", "platform.camera_create"} <= acts
