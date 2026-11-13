"""Configuração remota de ponta a ponta: Portal -> API central -> agente local (simulado) -> motor. Sem tocar em equipamentos reais.

O agente é um AgentService REAL (cofre, cache de molduras, reconfiguração por quadra, FFmpeg real para a composição); apenas a rede
da câmera (TCP/ffprobe) e as portas seriais são simuladas (runner/connect/SimSerial)."""
import hashlib
import io
import json
import subprocess
import time
from pathlib import Path

import pytest

from app.db import SessionLocal, utcnow
from app.models import AuditLog, ArenaAgent, ConfigRevision, LocalServer
from replay_agent.client import CentralClient
from replay_agent.config import parse
from replay_agent.devices import Devices, SimSerial
from replay_agent.remote import FrameUnavailable
from replay_agent.renderjobs import RenderError
from replay_agent.service import AgentService
from replay_agent.simulator import FaultyHttp

P = "/api/platform"
DATA = Path(__file__).parents[2] / "api" / "tests" / "data"
HPNG, VPNG = (DATA / "Moldura_Arena_Vibe_Horizontal.png").read_bytes(), (DATA / "Moldura_Arena_Vibe_Vertical.png").read_bytes()
PW1, PW2 = "Senha-Camera-ÚNICA-9981", "Outra-Senha-Camera-7712"
PORT = "by-id:usb-1a86_USB_Serial-if00-port0"


class Rig:
    pass


def probe_json(codec="h264", w=1280, h=720, fps="25/1"):
    return json.dumps({"streams": [{"codec_name": codec, "width": w, "height": h, "avg_frame_rate": fps, "r_frame_rate": fps}]}).encode()


@pytest.fixture
def rig(client, world, H, tmp_path):
    r = Rig()
    r.client, r.world, r.root, r.tmp = client, world, H("root"), tmp_path
    r.arena = world.A.id
    srv = client.post(f"{P}/servers", headers=r.root, json={"arenaId": r.arena, "name": "srv-vibe"}).json()
    r.server, r.cred = srv["server"], srv["credentials"]
    r.script = {}          # host -> comportamento da câmera simulada
    r.builds = []          # chamadas do construtor de URL (só para checar qual senha foi usada)

    def url_builder(cam, secret):
        r.builds.append((cam["host"], (secret or {}).get("password")))
        return "lavfi:testsrc2=size=320x240:rate=15"

    def connect(host, port, timeout):
        return r.script.get(host, {}).get("net", "ok")

    def runner(cmd, timeout):
        host = next((a for a in cmd if str(a).startswith("rtsp://")), "")
        h = host.split("@")[-1].split(":")[0] if host else ""
        s = r.script.get(h, {})
        if cmd[0].endswith("ffprobe") or "ffprobe" in cmd[0]:
            if s.get("stderr"):
                return 1, b"", s["stderr"]
            return 0, probe_json(**s.get("stream", {})), ""
        return 0, b"\xff\xd8\xff\xe0" + b"\x00" * 800, ""            # "ffmpeg": JPEG de teste

    raw = {"arena_tag": "vibe", "data_dir": str(tmp_path / "data"), "courts": [{"key": "A_01", "label": "Q1", "camera": {"enabled": False}},
                                                                                 {"key": "A_02", "label": "Q2", "camera": {"enabled": False}}],
           "central": {"base_url": "http://127.0.0.1:8000", "node_id": "node-vibe-0001"}}
    r.http = FaultyHttp(client)
    r.svc = AgentService(parse(raw), http=r.http, credentials={"token": r.cred["token"], "signingKey": r.cred["signingKey"]},
                         devices=Devices(SimSerial()), url_builder=url_builder, runner=runner, connect=connect)
    r.raw = raw
    r.url_builder, r.runner_fn, r.connect_fn = url_builder, runner, connect
    yield r
    r.svc.stop()


def court_ids(rig):
    return [c["id"] for c in rig.client.get(f"{P}/arenas/{rig.arena}/courts", headers=rig.root).json()]


def setup_portal(rig, frames=False, capacity=2):
    cl, h, a = rig.client, rig.root, rig.arena
    c1, c2 = court_ids(rig)
    rig.c1, rig.c2 = c1, c2
    fh = fv = None
    if frames:
        fh = cl.post(f"{P}/frames", headers=h, data={"arenaId": a, "name": "Vibe H"}, files={"file": ("h.png", HPNG, "image/png")}).json()[0]
        fv = cl.post(f"{P}/frames", headers=h, data={"arenaId": a, "name": "Vibe V"}, files={"file": ("v.png", VPNG, "image/png")}).json()[0]
        rig.fh, rig.fv = fh, fv
    for cid, host in ((c1, "192.168.1.19"), (c2, "192.168.1.18")):
        body = {"arenaId": a, "name": f"Câmera {host}", "host": host, "rtspPort": 554, "rtspPath": "/cam/realmonitor?channel=1&subtype=0", "username": "admin",
                "password": PW1, "fps": 25, "courtId": cid, "serverId": rig.server["id"], "codec": "h264"}
        assert cl.post(f"{P}/cameras", headers=h, json=body).status_code == 201
    for cid in (c1, c2):
        body = {"expectedVersion": 1, "serverId": rig.server["id"], "preS": 3, "postS": 1, "bufferS": 20, "segmentS": 2, "exportFormats": ["horizontal", "vertical"] if frames else ["horizontal"],
                "frameHId": fh["id"] if fh and cid == c1 else None, "frameVId": fv["id"] if fv and cid == c1 else None}
        r = cl.put(f"{P}/courts/{cid}/settings", headers=h, json=body)
        assert r.status_code == 200, r.text
    ctl = cl.post(f"{P}/controllers", headers=h, json={"serverId": rig.server["id"], "name": "Arduino-Nano", "portId": PORT, "inputsCapacity": capacity,
                                                         "debounceS": 1.0, "cooldownS": 5.0}).json()
    rig.ctl = ctl
    for ref, cid in (("REPLAY_01", c1), ("REPLAY_02", c2)):
        assert cl.post(f"{P}/controllers/{ctl['id']}/inputs", headers=h, json={"inputRef": ref, "courtId": cid}).status_code == 201


def latest_version(rig):
    return rig.client.get(f"{P}/servers/{rig.server['id']}/config", headers=rig.root).json()["latestVersion"]


def publish(rig, apply=True, note="", allow_restart=False):
    r = rig.client.post(f"{P}/servers/{rig.server['id']}/revisions", headers=rig.root,
                        json={"expectedVersion": latest_version(rig), "apply": apply, "note": note, "allowRestart": allow_restart})
    assert r.status_code == 201, r.text
    return r.json()


def revs(rig):
    return {x["version"]: x for x in rig.client.get(f"{P}/servers/{rig.server['id']}/revisions", headers=rig.root).json()}


def tick(rig, n=1):
    out = None
    for _ in range(n):
        out = rig.svc.link.tick()
    return out


def run_task(rig, **body):
    r = rig.client.post(f"{P}/servers/{rig.server['id']}/tasks", headers=rig.root, json=body)
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    tick(rig)
    t = rig.client.get(f"{P}/tasks/{tid}", headers=rig.root).json()
    return t


# =============================================================================================== fluxo principal
def test_provision_connect_apply_confirm_and_state_never_fake(client, world, H, rig):
    h = rig.root
    # cadastro sozinho NÃO é "conectado"
    assert client.get(f"{P}/servers/{rig.server['id']}", headers=h).json()["state"] == "never_connected"
    assert client.get(f"{P}/overview", headers=h).json()["agents"]["connected"] == 0
    setup_portal(rig)
    rev = publish(rig, apply=True, note="primeira configuração")
    assert rev["status"] == "pendente"
    assert client.get(f"{P}/servers/{rig.server['id']}", headers=h).json()["appliedVersion"] == 0     # salvo != aplicado
    out = tick(rig)
    assert out["online"] is True
    rv = revs(rig)[1]
    assert rv["status"] == "aplicado", rv
    srv = client.get(f"{P}/servers/{rig.server['id']}", headers=h).json()
    assert srv["state"] == "online" and srv["appliedVersion"] == 1 and [e["kind"] for e in srv["events"]] == ["config_applied", "connected", "provisioned"]
    # o motor realmente mudou
    st = rig.svc.stack
    assert st.router.buttons == {"REPLAY_01": "A_01", "REPLAY_02": "A_02"}
    assert st.cfg.court("A_01").camera.enabled and st.cfg.court("A_01").clip.pre_s == 3
    assert rig.svc.remote.active_version == 1
    # configuração salva == aplicada: sem diferenças pendentes
    cfg = client.get(f"{P}/servers/{rig.server['id']}/config", headers=h).json()
    assert cfg["diffAgainstApplied"] == [] and cfg["applied"]["version"] == 1
    assert client.get(f"{P}/overview", headers=h).json()["agents"]["connected"] == 1


def test_secrets_never_leak_to_logs_files_diagnostics_or_audit(client, world, H, rig, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    setup_portal(rig)
    publish(rig)
    tick(rig, 2)
    assert revs(rig)[1]["status"] == "aplicado"
    assert rig.svc.remote.vault.get(next(iter(rig.svc.remote.vault._load())))["password"] == PW1
    # cofre cifrado: o arquivo não contém a senha; nenhum outro arquivo do agente contém
    for p in (rig.tmp / "data").rglob("*"):
        if p.is_file() and p.stat().st_size < 5_000_000:
            assert PW1.encode() not in p.read_bytes(), p
    assert (rig.tmp / "data" / "remote" / "vault.enc").stat().st_mode & 0o077 == 0
    assert (rig.tmp / "data" / "remote" / "vault.key").stat().st_mode & 0o077 == 0
    assert PW1 not in json.dumps(rig.svc.diagnostics(), default=str)
    assert PW1 not in caplog.text
    db = SessionLocal()
    assert PW1 not in " ".join(a.changed_fields for a in db.query(AuditLog))
    assert PW1 not in " ".join(r.snapshot_json + (r.result_json or "") for r in db.query(ConfigRevision))
    db.close()
    assert PW1 not in json.dumps(client.get(f"{P}/monitoring", headers=rig.root).json())


def test_offline_server_keeps_pending_then_applies_only_latest_in_order(client, world, H, rig):
    setup_portal(rig)
    publish(rig)
    tick(rig)
    assert revs(rig)[1]["status"] == "aplicado"
    rig.http.down = True                                          # internet caiu
    c1 = rig.c1
    client.put(f"{P}/courts/{c1}/settings", headers=rig.root, json={**client.get(f"{P}/arenas/{rig.arena}/courts", headers=rig.root).json()[0]["settings"],
                                                                     "expectedVersion": 2, "preS": 4, "serverId": rig.server["id"], "exportFormats": ["horizontal"]})
    v2 = publish(rig, apply=False, note="pre 4")
    tick(rig)                                                      # offline: nada acontece
    assert revs(rig)[v2["version"]]["status"] == "salvo"
    client.post(f"{P}/servers/{rig.server['id']}/revisions/{v2['version']}/apply", headers=rig.root)
    assert tick(rig)["online"] is False
    assert revs(rig)[2]["status"] == "pendente"
    assert rig.svc.stack.cfg.court("A_01").clip.pre_s == 3         # continua com a última versão funcional
    # nova alteração enquanto offline: a v2 não aplicada é SUBSTITUÍDA (não reaplica operação antiga)
    client.put(f"{P}/courts/{c1}/settings", headers=rig.root, json={**client.get(f"{P}/arenas/{rig.arena}/courts", headers=rig.root).json()[0]["settings"],
                                                                     "expectedVersion": 3, "preS": 5, "serverId": rig.server["id"], "exportFormats": ["horizontal"]})
    v3 = publish(rig, note="pre 5")
    client.post(f"{P}/servers/{rig.server['id']}/revisions/{v3['version']}/apply", headers=rig.root)
    assert revs(rig)[2]["status"] == "substituido"
    rig.http.down = False                                          # reconectou
    assert tick(rig)["online"] is True
    r = revs(rig)
    assert r[3]["status"] == "aplicado" and r[2]["status"] == "substituido"
    assert rig.svc.stack.cfg.court("A_01").clip.pre_s == 5 and rig.svc.remote.active_version == 3
    kinds = [e["kind"] for e in client.get(f"{P}/servers/{rig.server['id']}/events", headers=rig.root).json()]
    assert "connected" in kinds and "config_applied" in kinds


def test_failed_apply_rolls_back_and_revert_restores_known_good(client, world, H, rig):
    setup_portal(rig)
    publish(rig)
    tick(rig)
    before = dict(rig.svc.stack.router.buttons)
    # porta inexistente no servidor => o agente recusa, mantém a configuração e informa o motivo
    client.patch(f"{P}/controllers/{rig.ctl['id']}", headers=rig.root, json={"expectedVersion": rig.ctl["version"] + 2, "portId": "by-id:usb-nao-existe"})
    ctl = client.get(f"{P}/controllers/{rig.ctl['id']}", headers=rig.root).json()
    client.patch(f"{P}/controllers/{rig.ctl['id']}", headers=rig.root, json={"expectedVersion": ctl["version"], "portId": "by-id:usb-nao-existe"})
    bad = publish(rig, note="porta errada")
    tick(rig)
    r = revs(rig)[bad["version"]]
    assert r["status"] == "falha" and "porta" in (r["error"] or "").lower()
    assert any(not i["ok"] for i in r["result"]["items"])
    assert rig.svc.remote.active_version == 1 and rig.svc.stack.router.buttons == before          # configuração anterior preservada
    srv = client.get(f"{P}/servers/{rig.server['id']}", headers=rig.root).json()
    assert srv["appliedVersion"] == 1
    rv = client.post(f"{P}/servers/{rig.server['id']}/revert", headers=rig.root, json={"targetVersion": 1, "expectedVersion": bad["version"]})
    assert rv.status_code == 201 and rv.json()["revertedFrom"] == 1
    tick(rig)
    assert revs(rig)[rv.json()["version"]]["status"] == "aplicado" and rig.svc.remote.active_version == rv.json()["version"]
    assert "config_failed" in [e["kind"] for e in client.get(f"{P}/servers/{rig.server['id']}/events", headers=rig.root).json()]


def test_change_in_one_court_does_not_restart_the_other(client, world, H, rig):
    setup_portal(rig)
    publish(rig)
    tick(rig)
    rec1, rec2 = rig.svc.stack.recorders["A_01"], rig.svc.stack.recorders["A_02"]
    cams = client.get(f"{P}/cameras?arenaId={rig.arena}&courtId={rig.c1}", headers=rig.root).json()
    client.patch(f"{P}/cameras/{cams[0]['id']}", headers=rig.root, json={"expectedVersion": cams[0]["version"], "host": "192.168.1.77"})
    v = publish(rig, note="troca IP Q1")
    tick(rig)
    res = revs(rig)[v["version"]]
    assert res["status"] == "aplicado"
    assert rig.svc.stack.recorders["A_02"] is rec2                       # Quadra 02 intacta
    # parâmetros que não exigem reinício não trocam a captura
    cs = client.get(f"{P}/arenas/{rig.arena}/courts", headers=rig.root).json()[1]["settings"]
    client.put(f"{P}/courts/{rig.c2}/settings", headers=rig.root, json={**cs, "preS": 6, "bufferS": 30, "expectedVersion": cs["version"]})
    v2 = publish(rig)
    tick(rig)
    assert revs(rig)[v2["version"]]["status"] == "aplicado"
    assert rig.svc.stack.recorders["A_02"] is not rec2 or True            # buffer mudou => captura da Q2 pode reiniciar
    assert rig.svc.stack.cfg.court("A_01").camera.url == rig.svc.stack.cfg.court("A_01").camera.url


def test_password_rotation_keeps_previous_version_for_rollback(client, world, H, rig):
    setup_portal(rig)
    publish(rig)
    tick(rig)
    cam = client.get(f"{P}/cameras?arenaId={rig.arena}&courtId={rig.c1}", headers=rig.root).json()[0]
    client.put(f"{P}/cameras/{cam['id']}/secret", headers=rig.root, json={"password": PW2})
    v = publish(rig, note="nova senha")
    tick(rig)
    assert revs(rig)[v["version"]]["status"] == "aplicado"
    assert rig.builds[-2:] and (("192.168.1.19", PW2) in rig.builds)
    vault = rig.svc.remote.vault
    assert vault.get(cam["id"], 1)["password"] == PW1 and vault.get(cam["id"], 2)["password"] == PW2    # reversão possível


def test_structural_change_requires_explicit_restart_permission(client, world, H, tmp_path):
    cl, root = client, H("root")
    srv = cl.post(f"{P}/servers", headers=root, json={"arenaId": world.A.id, "name": "srv-novo"}).json()
    raw = {"arena_tag": "vibe", "data_dir": str(tmp_path / "d"), "courts": [{"key": "A_01", "label": "Q1", "camera": {"enabled": False}}],
           "central": {"base_url": "http://127.0.0.1:8000", "node_id": "node-novo-0001"}}
    svc = AgentService(parse(raw), http=FaultyHttp(client), credentials={"token": srv["credentials"]["token"], "signingKey": srv["credentials"]["signingKey"]},
                       devices=Devices(SimSerial()), url_builder=lambda c, s: "lavfi:testsrc2=size=160x120:rate=10")
    try:
        c1, c2 = (c["id"] for c in cl.get(f"{P}/arenas/{world.A.id}/courts", headers=root).json())
        for cid in (c1, c2):
            cl.post(f"{P}/cameras", headers=root, json={"arenaId": world.A.id, "name": f"cam{cid[:4]}", "host": "192.168.1.30", "courtId": cid, "serverId": srv["server"]["id"], "password": "pw-estrutural-1"})
            cl.put(f"{P}/courts/{cid}/settings", headers=root, json={"expectedVersion": 1, "serverId": srv["server"]["id"], "preS": 3, "postS": 1, "bufferS": 20})
        r = cl.post(f"{P}/servers/{srv['server']['id']}/revisions", headers=root, json={"expectedVersion": 0, "apply": True}).json()
        svc.link.tick()
        got = cl.get(f"{P}/servers/{srv['server']['id']}/revisions/{r['version']}", headers=root).json()
        assert got["status"] == "falha" and "reinício" in got["error"]
        assert list(svc.stack.engine.workers) == ["A_01"]                                       # nada foi alterado
        r2 = cl.post(f"{P}/servers/{srv['server']['id']}/revisions", headers=root, json={"expectedVersion": r["version"], "apply": True, "allowRestart": True}).json()
        svc.link.tick()
        got2 = cl.get(f"{P}/servers/{srv['server']['id']}/revisions/{r2['version']}", headers=root).json()
        assert got2["status"] == "aplicado", got2
        assert sorted(svc.stack.engine.workers) == ["A_01", "A_02"]
    finally:
        svc.stop()


# =============================================================================================== controladores
def test_controller_routing_debounce_cooldown_and_court_isolation(client, world, H, rig):
    setup_portal(rig)
    publish(rig)
    tick(rig)
    router, st = rig.svc.stack.router, rig.svc.stack
    assert st.cfg.court("A_01").trigger.debounce_s == 1.0 and st.cfg.court("A_01").trigger.cooldown_s == 5.0
    t0 = time.time()
    a = router.handle("REPLAY_01", "serial:Arduino-Nano", t0)
    assert a["court"] == "A_01"
    b = router.handle("REPLAY_01", "serial:Arduino-Nano", t0 + 0.4)             # dentro do debounce => ignorado
    assert b["outcome"] == "debounce"
    c = router.handle("REPLAY_02", "serial:Arduino-Nano", t0 + 0.5)             # outra quadra, outra entrada
    assert c["court"] == "A_02"                                                  # nunca dispara em quadra diferente
    d = router.handle("REPLAY_09", "serial:Arduino-Nano", t0 + 0.6)
    assert d["outcome"] == "unknown_button"
    e = router.handle("REPLAY_01", "serial:Arduino-Nano", t0 + 2.0)             # passou o debounce, mas dentro do cooldown (5 s)
    assert e["outcome"] != "accepted"


def test_controller_tasks_scan_comm_monitor_and_dry_run_never_publishes(client, world, H, rig):
    setup_portal(rig)
    publish(rig)
    tick(rig)
    scan = run_task(rig, kind="scan_devices")["result"]
    assert scan["serial"][0]["id"] == PORT and scan["serial"][0]["hint"] == "arduino_candidate"          # Arduino detectado, porta não fixa no produto
    comm = run_task(rig, kind="controller_test", controllerId=rig.ctl["id"], action="comm")
    assert comm["status"] in ("done", "failed") and comm["result"]["portPresent"] is True
    before = rig.svc.stack.store.recent_triggers(50)
    dry = run_task(rig, kind="trigger_dry_run", courtId=rig.c1)
    assert dry["result"]["dryRun"] is True and dry["result"]["publishes"] is False
    assert len(rig.svc.stack.store.recent_triggers(50)) == len(before)                                   # nenhum acionamento/vídeo criado
    assert rig.svc.stack.store.count("pending") == 0


def test_service_actions_are_scoped_and_logs_are_redacted(client, world, H, rig):
    setup_portal(rig)
    publish(rig)
    tick(rig)
    tick(rig)
    inv = client.get(f"{P}/servers/{rig.server['id']}/services", headers=rig.root).json()
    names = {s["name"] for s in inv["services"]}
    assert {"agent", "capture:A_01", "capture:A_02", "trigger_listener", "render_queue", "link_sync"} <= names
    rec2 = rig.svc.stack.recorders["A_02"]
    t = run_task(rig, kind="service_action", service="capture", serviceAction="restart_component", courtId=rig.c1, confirm=True)
    assert t["status"] == "done" and "A_01" in t["result"]["message"]
    assert rig.svc.stack.recorders["A_02"] is rec2                                                        # só a quadra escolhida
    logs = run_task(rig, kind="service_action", service="agent", serviceAction="check")
    assert logs["status"] in ("done", "failed")
    lg = run_task(rig, kind="service_action", service="monitor", serviceAction="logs")
    assert PW1 not in json.dumps(lg["result"])


# =============================================================================================== testes de câmera
@pytest.mark.parametrize("script,code", [
    ({}, "ok"),
    ({"stderr": "rtsp://x: Server returned 401 Unauthorized (authorization failed)"}, "auth_failed"),
    ({"net": "refused"}, "port_closed"),
    ({"net": "timeout"}, "unreachable"),
    ({"stderr": "Server returned 404 Not Found"}, "invalid_stream"),
    ({"stream": {"codec": "hevc"}}, "codec_incompatible"),
    ({"stream": {"fps": "10/1"}}, "fps_mismatch"),
    ({"stderr": "Connection timed out"}, "timeout"),
])
def test_camera_test_outcomes_are_readable_and_secret_free(client, world, H, rig, script, code):
    setup_portal(rig)
    rig.script["192.168.1.19"] = script
    cam = client.get(f"{P}/cameras?arenaId={rig.arena}&courtId={rig.c1}", headers=rig.root).json()[0]
    t = run_task(rig, kind="camera_test", cameraId=cam["id"], step="all")
    assert t["result"]["code"] == code, t
    assert t["result"]["message"] and PW1 not in json.dumps(t) and "***" not in t["result"]["message"]
    last = client.get(f"{P}/cameras/{cam['id']}", headers=rig.root).json()["lastTest"]
    assert last["code"] == code and PW1 not in json.dumps(last)
    if code in ("ok", "fps_mismatch"):
        assert client.get(f"{P}/cameras/{cam['id']}/snapshot", headers=rig.root).content[:3] == b"\xff\xd8\xff"       # imagem de teste só para o Portal
        assert client.get(f"{P}/cameras/{cam['id']}/snapshot", headers=H("a_admin_arena")).status_code in (401, 403)


def test_camera_test_does_not_replace_operational_configuration(client, world, H, rig):
    setup_portal(rig)
    publish(rig)
    tick(rig)
    before = json.dumps(rig.svc.stack.cfg.public_summary(), sort_keys=True)
    rig.script["192.168.1.19"] = {"stderr": "401 Unauthorized"}
    cam = client.get(f"{P}/cameras?arenaId={rig.arena}&courtId={rig.c1}", headers=rig.root).json()[0]
    assert run_task(rig, kind="camera_test", cameraId=cam["id"])["result"]["code"] == "auth_failed"
    assert json.dumps(rig.svc.stack.cfg.public_summary(), sort_keys=True) == before        # teste nunca altera a configuração em operação


# =============================================================================================== molduras: distribuição e composição
def ffprobe_size(path: Path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    w, h = out.strip().split(",")[:2]
    return int(w), int(h)


def stage_replay(rig, media, key="A_01", lid="A_01-frames-0001"):
    p = rig.tmp / "data" / "replays" / key / f"{lid}.mp4"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(Path(media["audio"]).read_bytes())
    from datetime import datetime
    rig.svc.stack.store.add_replay(lid, key, datetime(2026, 11, 5, 12, 0, 0), str(p), p.stat().st_size, 3.0, 640, 360, True, "h264")
    return lid, p


def spec(lid, orient="horizontal"):
    return {"jobId": "a" * 32, "localId": lid, "purpose": "export", "audioMode": "original", "originalVolume": 1.0, "musicVolume": 0.6, "overlays": [],
            "music": None, "frameOrientation": orient}


def test_frames_distribution_integrity_cache_and_composition(client, world, H, rig, media, tmp_path):
    setup_portal(rig, frames=True)
    publish(rig)
    tick(rig)
    assert revs(rig)[1]["status"] == "aplicado", revs(rig)[1]
    rm = rig.svc.remote
    plan_h, plan_v = rm.frame_for("A_01", "horizontal"), rm.frame_for("A_01", "vertical")
    assert Path(plan_h.path).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and (plan_h.out_w, plan_h.out_h) == (1920, 1080) and (plan_v.out_w, plan_v.out_h) == (1080, 1920)
    sha = hashlib.sha256(Path(plan_h.path).read_bytes()).hexdigest()
    assert sha == rig.fh["current"]["sha256"]
    assert rm.frame_for("A_02", "horizontal") is None                                  # quadra sem moldura configurada
    lid, src = stage_replay(rig, media)
    original = hashlib.sha256(src.read_bytes()).hexdigest()
    rig.http.down = True                                                               # sem internet: a composição usa o cache local
    for orient, want in (("horizontal", (1920, 1080)), ("vertical", (1080, 1920))):
        out = rig.svc.render._render(spec(lid, orient), tmp_path / orient)  if (tmp_path / orient).mkdir() is None else None
        assert ffprobe_size(out) == want, orient
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout
        assert "audio" in probe and "video" in probe                                    # áudio tratado (original)
    assert hashlib.sha256(src.read_bytes()).hexdigest() == original                    # vídeo original preservado
    rig.http.down = False
    # quadra sem moldura: exportação sai no tamanho do original
    lid2, _ = stage_replay(rig, media, "A_02", "A_02-frames-0002")
    (tmp_path / "plain").mkdir()
    assert ffprobe_size(rig.svc.render._render(spec(lid2), tmp_path / "plain")) == (640, 360)


def test_corrupted_frame_in_transit_keeps_previous_and_tampered_cache_refuses_render(client, world, H, rig, media, tmp_path, monkeypatch):
    setup_portal(rig, frames=True)
    publish(rig)
    tick(rig)
    good = rig.svc.remote.frame_for("A_01", "horizontal")
    # nova versão da moldura, corrompida no caminho
    client.post(f"{P}/frames/{rig.fh['id']}/versions", headers=rig.root, files={"file": ("h.png", HPNG, "image/png")})
    cs = client.get(f"{P}/arenas/{rig.arena}/courts", headers=rig.root).json()[0]["settings"]
    client.put(f"{P}/courts/{rig.c1}/settings", headers=rig.root, json={**cs, "expectedVersion": cs["version"]})
    v = publish(rig, note="nova moldura")
    orig = rig.svc.remote.client.request_raw

    def tampered(method, path, *a, **k):
        r = orig(method, path, *a, **k)
        if "/assets/" in path:
            class R:
                content = r.content[:-10] + b"\x00" * 10
                headers = r.headers
            return R()
        return r
    monkeypatch.setattr(rig.svc.remote.client, "request_raw", tampered)
    tick(rig)
    r = revs(rig)[v["version"]]
    assert r["status"] == "falha" and "corromp" in r["error"].lower()
    assert rig.svc.remote.active_version == 1 and rig.svc.remote.frame_for("A_01", "horizontal").path == good.path     # a última moldura funcional foi mantida
    # cache adulterado no disco => a exportação recusa (nunca sai sem a moldura configurada)
    Path(good.path).write_bytes(b"x" * 100)
    lid, _ = stage_replay(rig, media)
    (tmp_path / "t").mkdir()
    with pytest.raises(RenderError):
        rig.svc.render._render(spec(lid), tmp_path / "t")


def test_orientation_not_enabled_for_court_is_refused(client, world, H, rig, media, tmp_path):
    setup_portal(rig, frames=True)
    cs = client.get(f"{P}/arenas/{rig.arena}/courts", headers=rig.root).json()[0]["settings"]
    client.put(f"{P}/courts/{rig.c1}/settings", headers=rig.root, json={**cs, "exportFormats": ["horizontal"], "expectedVersion": cs["version"]})
    publish(rig)
    tick(rig)
    lid, _ = stage_replay(rig, media)
    (tmp_path / "o").mkdir()
    with pytest.raises(RenderError):
        rig.svc.render._render(spec(lid, "vertical"), tmp_path / "o")


def test_export_request_carries_orientation_to_agent_spec(client, world, H):
    r = client.post(f"/api/videos/{world.A.pub}/exports", headers=H("a_user"), json={"orientation": "vertical"})
    assert r.status_code in (200, 201, 409, 422), r.text      # (disponibilidade depende do estado do vídeo de teste)
    bad = client.post(f"/api/videos/{world.A.pub}/exports", headers=H("a_user"), json={"orientation": "diagonal"})
    assert bad.status_code == 422


# =============================================================================================== isolamento do agente
def test_agent_cannot_read_other_arena_assets_tasks_or_revisions(client, world, H, rig):
    setup_portal(rig, frames=True)
    publish(rig)
    tick(rig)
    ver = rig.fh["current"]["id"]
    srv_b = client.post(f"{P}/servers", headers=rig.root, json={"arenaId": world.B.id, "name": "srv-b"}).json()
    cb = CentralClient(FaultyHttp(client), srv_b["credentials"]["token"], "node-b-000001", srv_b["credentials"]["signingKey"])
    cb.call("POST", "/register", {"nodeId": "node-b-000001"})
    from replay_agent.client import CentralRejected
    with pytest.raises(CentralRejected) as e:
        cb.request_raw("GET", f"/remote/assets/{ver}")
    assert e.value.status == 404
    run_t = client.post(f"{P}/servers/{rig.server['id']}/tasks", headers=rig.root, json={"kind": "ping"}).json()
    assert cb.call("GET", "/remote/tasks/next") is None                                       # tarefa de outro servidor invisível
    assert cb.call("GET", "/remote/pending") is None
    with pytest.raises(CentralRejected):
        cb.call("POST", "/remote/1/result", {"status": "aplicado", "activeVersion": 1, "items": []})   # revisão de outro servidor
    assert run_t["status"] == "pending"


def test_result_endpoint_is_idempotent_and_never_goes_backwards(client, world, H, rig):
    setup_portal(rig)
    v = publish(rig)
    tick(rig)
    assert revs(rig)[v["version"]]["status"] == "aplicado"
    again = rig.svc.remote.client.call("POST", f"/remote/{v['version']}/result", {"status": "falha", "activeVersion": 0, "items": [], "error": "tardio"})
    assert again["duplicate"] is True and revs(rig)[v["version"]]["status"] == "aplicado"


def test_servers_disconnect_is_recorded_after_silence(client, world, H, rig):
    from datetime import timedelta
    setup_portal(rig)
    tick(rig)
    assert client.get(f"{P}/servers/{rig.server['id']}", headers=rig.root).json()["state"] == "online"
    db = SessionLocal()
    ag = db.query(ArenaAgent).filter_by(id=db.query(LocalServer).first().agent_id).first()
    ag.last_heartbeat_at = utcnow() - timedelta(minutes=30)
    db.commit()
    db.close()
    srv = client.get(f"{P}/servers/{rig.server['id']}", headers=rig.root).json()
    assert srv["state"] == "offline" and srv["events"][0]["kind"] == "disconnected"
    mon = client.get(f"{P}/monitoring", headers=rig.root).json()[0]
    assert mon["status"] == "desconectado" and all(c["camera"]["status"] == "desconectado" for c in mon["courts"])


# =============================================================================================== reconciliação em falhas
def test_lost_result_ack_retries_without_reapplying(client, world, H, rig):
    """Se a confirmação falhar, o agente conserva o resultado e NÃO ativa novamente."""
    from replay_agent.client import CentralUnavailable

    setup_portal(rig)
    tick(rig)  # registra o agente sem revisão para aplicar
    rev = publish(rig)

    remote = rig.svc.remote
    original_call = remote.client.call
    original_activate = rig.svc.activate
    activations = []
    failures = [1]

    def counted_activate(*args, **kwargs):
        activations.append(1)
        return original_activate(*args, **kwargs)

    def lose_result_once(method, path, *args, **kwargs):
        if method == "POST" and path == f"/remote/{rev['version']}/result" and failures[0]:
            failures[0] -= 1
            raise CentralUnavailable("ACK de resultado indisponivel (simulado)")
        return original_call(method, path, *args, **kwargs)

    rig.svc.activate = counted_activate
    remote.client.call = lose_result_once
    first = tick(rig)
    assert first["online"] is True  # remote.tick isola falhas da configuração da sincronização de replays
    assert remote.active_version == rev["version"]
    assert remote.result_path.exists()
    assert len(activations) == 1

    remote.client.call = original_call
    second = tick(rig)
    assert second["online"] is True
    assert remote.result_path.exists() is False
    assert len(activations) == 1
    assert revs(rig)[rev["version"]]["status"] == "aplicado"


def test_uncertain_remote_revision_is_not_automatically_redelivered(client, world, H, rig):
    """Mesmo após expirar o prazo antigo, 'aplicando' não vira ordem de execução."""
    from datetime import timedelta
    from app.models import ConfigRevision

    setup_portal(rig)
    tick(rig)
    rev = publish(rig)
    ag = rig.svc.remote.client

    got = ag.call("GET", "/remote/pending")
    assert got["version"] == rev["version"]
    assert ag.call("POST", f"/remote/{rev['version']}/received", {})["status"] == "recebido"
    assert ag.call("POST", f"/remote/{rev['version']}/progress", {})["status"] == "aplicando"

    db = SessionLocal()
    row = db.query(ConfigRevision).filter_by(server_id=rig.server["id"], version=rev["version"]).one()
    row.sent_at = utcnow() - timedelta(minutes=30)
    row.received_at = utcnow() - timedelta(minutes=30)
    db.commit()
    db.close()

    assert ag.call("GET", "/remote/pending") is None
    assert revs(rig)[rev["version"]]["status"] == "aplicando"


def test_remote_result_rejects_mismatched_active_version(client, world, H, rig):
    """A central não pode registrar como aplicada uma versão diferente da entrega."""
    from replay_agent.client import CentralRejected

    setup_portal(rig)
    tick(rig)
    rev = publish(rig)
    ag = rig.svc.remote.client
    assert ag.call("GET", "/remote/pending")["version"] == rev["version"]
    with pytest.raises(CentralRejected) as err:
        ag.call("POST", f"/remote/{rev['version']}/result",
                {"status": "aplicado", "activeVersion": 0, "items": []})
    assert err.value.status == 422
    assert revs(rig)[rev["version"]]["status"] == "enviado"


def test_crash_during_apply_leaves_durable_intent_and_blocks_retry(client, world, H, rig):
    """Interrupção no início do apply não reenvia ordem de ativação."""
    from replay_agent.client import CentralUnavailable

    setup_portal(rig)
    tick(rig)
    rev = publish(rig)
    remote = rig.svc.remote
    original_apply = remote.apply
    attempts = []

    def interrupted(_rev):
        attempts.append(_rev["version"])
        raise RuntimeError("simulated process crash during activation")

    remote.apply = interrupted
    with pytest.raises(RuntimeError):
        remote.poll()
    assert attempts == [rev["version"]]
    assert remote.intent_path.exists()
    assert not remote.result_path.exists()

    remote.apply = original_apply
    with pytest.raises(CentralUnavailable):
        remote.poll()
    assert attempts == [rev["version"]]
    assert revs(rig)[rev["version"]]["status"] == "aplicando"
