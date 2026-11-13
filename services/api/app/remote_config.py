"""Núcleo da configuração remota: validação (anti-SSRF), snapshot por servidor, revisões versionadas e ciclo de vida.

Fluxo: Editar -> Validar -> (Testar) -> Salvar [revisão 'salvo'] -> Aplicar ['pendente'] -> agente busca ['enviado'] ->
'recebido' -> 'aplicando' -> 'aplicado' | 'falha'. Reversão = nova revisão com o snapshot de uma versão anterior.
A configuração só é 'aplicada' quando o AGENTE confirma (ver routers/agent_remote.py).
"""
import ipaddress
import json
import re
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import capacity, framing
from .db import utcnow
from .models import (Arena, ArenaAgent, Camera, ConfigRevision, Controller, ControllerInput, Court, CourtSetting, Frame, FrameVersion,
                     LocalServer, ServerEvent)

SCHEMA = 1
HOST_RE = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*$")
PATH_RE = re.compile(r"^(/[A-Za-z0-9_\-./%+:=&?,]{0,198})?$")
USER_RE = re.compile(r"^[A-Za-z0-9_.@\-]{0,64}$")
PORT_ID_RE = re.compile(r"^(by-id:[A-Za-z0-9_.:\-]{1,150}|dev:tty(USB|ACM)\d{1,3})?$")   # só portas seriais; nunca caminho livre
REF_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
CODECS = ("h264", "h265")
TRANSPORTS = ("tcp", "udp")
CONTROLLER_KINDS = ("arduino_usb", "ip_button")
FINAL = ("aplicado", "falha", "revertido", "substituido")
ACTIVE_STATES = ("pendente", "enviado", "recebido", "aplicando")
REV_STATES = ("salvo", "pendente", "enviado", "recebido", "aplicando", "aplicado", "falha", "revertido", "substituido")


def bad(msg: str, code: int = 422, **extra) -> HTTPException:
    return HTTPException(code, {"message": msg, **extra} if extra else msg)


# ------------------------------------------------------------------ validação de entradas (anti-SSRF e injeção)
def validate_host(host: str) -> str:
    """Endereço de câmera/botoeira: IP ou hostname simples. Sem esquema, caminho, credenciais ou espaços.
    Bloqueia loopback, link-local (inclui metadados de nuvem 169.254.x), multicast e não especificado.
    Redes privadas (192.168/10/172.16) SÃO permitidas: câmeras ficam em LAN e quem conecta é o AGENTE local, nunca a central."""
    h = (host or "").strip()
    if not h or any(c in h for c in "/@:\\ ?#[]") and not _is_ip(h):
        raise bad("endereço inválido: informe somente o IP ou o nome do equipamento (sem rtsp://, usuário, porta ou caminho)")
    if _is_ip(h):
        ip = ipaddress.ip_address(h)
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise bad("endereço não permitido (loopback, link-local, multicast ou reservado)")
        return h
    if not HOST_RE.match(h) or h.lower() in ("localhost", "localhost.localdomain") or h.lower().endswith((".local.", ".internal")):
        raise bad("nome de equipamento inválido ou não permitido")
    return h.lower()


def _is_ip(h: str) -> bool:
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def validate_camera_fields(d: dict) -> dict:
    out = dict(d)
    if "host" in d:
        out["host"] = validate_host(d["host"])
    if "rtsp_port" in d and not (1 <= int(d["rtsp_port"]) <= 65535):
        raise bad("porta RTSP inválida (1–65535)")
    if "rtsp_path" in d:
        p = (d["rtsp_path"] or "").strip()
        if p and (not p.startswith("/") or ".." in p or "://" in p or "@" in p or not PATH_RE.match(p)):
            raise bad("caminho RTSP inválido (ex.: /cam/realmonitor?channel=1&subtype=0)")
        out["rtsp_path"] = p
    if "username" in d and not USER_RE.match(d["username"] or ""):
        raise bad("usuário inválido")
    if "transport" in d and d["transport"] not in TRANSPORTS:
        raise bad("transporte deve ser tcp ou udp")
    if "codec" in d and d["codec"] not in CODECS:
        raise bad("codec inválido")
    if d.get("fps") is not None and not (1 <= float(d["fps"]) <= 120):
        raise bad("FPS fora do intervalo (1–120)")
    for k in ("width", "height"):
        if d.get(k) is not None and not (160 <= int(d[k]) <= 7680):
            raise bad("resolução fora do intervalo")
    return out


def validate_password(pw: str) -> str:
    if not pw or len(pw) > 128 or any(ord(c) < 32 or ord(c) == 127 for c in pw):
        raise bad("senha inválida (1–128 caracteres, sem caracteres de controle)")
    return pw


# ------------------------------------------------------------------ servidor ↔ agente
def server_agent(db: Session, server: LocalServer) -> ArenaAgent | None:
    return db.get(ArenaAgent, server.agent_id) if server.agent_id else None


def server_state(db: Session, server: LocalServer, now=None) -> str:
    """online|delayed|offline|never_connected|revoked — NUNCA 'conectado' só porque existe cadastro."""
    if server.status == "revoked":
        return "revoked"
    ag = server_agent(db, server)
    if not ag:
        return "never_connected"
    return capacity.connection_state(ag, now)


def record_state(db: Session, server: LocalServer, now=None) -> str:
    """Registra no histórico as transições de conexão (chamado no heartbeat e nas consultas do Portal)."""
    st = server_state(db, server, now)
    norm = "online" if st in ("online", "delayed") else st
    if server.last_state != norm and norm in ("online", "offline", "revoked"):
        if server.last_state is not None or norm == "online":
            db.add(ServerEvent(server_id=server.id, arena_id=server.arena_id, kind={"online": "connected", "offline": "disconnected", "revoked": "revoked"}[norm],
                               detail=f"estado: {norm}"))
        db.flush()
        server.last_state = norm
    return st


# ------------------------------------------------------------------ snapshot desejado
def court_key(c: Court) -> str:
    return c.local_court_key


def build_snapshot(db: Session, server: LocalServer) -> dict:
    """Configuração DESEJADA do servidor, sem segredos (a senha da câmera vai por canal separado, só na entrega ao agente)."""
    courts = []
    rows = (db.query(Court, CourtSetting).join(CourtSetting, CourtSetting.court_id == Court.id)
            .filter(Court.arena_id == server.arena_id, CourtSetting.server_id == server.id).order_by(Court.number).all())
    for ct, cs in rows:
        cam = (db.query(Camera).filter(Camera.court_id == ct.id, Camera.arena_id == server.arena_id, Camera.active.is_(True))
               .order_by(Camera.is_primary.desc(), Camera.created_at).first())
        camera = None
        if cam:
            camera = {"id": cam.id, "name": cam.name, "host": cam.host, "macAddress": cam.mac_address, "port": cam.rtsp_port, "transport": cam.transport, "path": cam.rtsp_path,
                      "username": cam.username, "hasSecret": bool(cam.secret_enc), "secretVersion": cam.secret_version,
                      "width": cam.width, "height": cam.height, "fps": cam.fps, "codec": cam.codec, "profile": cam.profile}
        frames = {"enabled": bool(cs.frames_enabled), "horizontal": None, "vertical": None}
        for orient, fid, out in (("horizontal", cs.frame_h_id, cs.out_h), ("vertical", cs.frame_v_id, cs.out_v)):
            fr = db.get(Frame, fid) if fid else None
            ver = db.get(FrameVersion, fr.current_version_id) if fr and fr.available and not fr.deleted_at and fr.current_version_id else None
            if ver and ver.arena_id == server.arena_id:
                frames[orient] = {"frameId": fr.id, "name": fr.name, "versionId": ver.id, "version": ver.version, "sha256": ver.sha256, "bytes": ver.bytes,
                                  "width": ver.width, "height": ver.height, "window": json.loads(ver.window_json), "layout": json.loads(ver.layout_json),
                                  "out": out}
        courts.append({"key": court_key(ct), "courtId": ct.id, "label": ct.label, "active": ct.status == "active", "camera": camera,
                       "replay": {"preS": cs.pre_s, "postS": cs.post_s, "closeCountdownS": cs.close_countdown_s, "bufferS": cs.buffer_s, "segmentS": cs.segment_s,
                                  "storageLimitMb": cs.storage_limit_mb if cs.storage_limit_mb is not None else ct.storage_limit_mb,
                                  "retentionDays": cs.retention_days, "priority": cs.priority, "reconnectMinS": cs.reconnect_min_s,
                                  "reconnectMaxS": cs.reconnect_max_s, "audioMode": cs.audio_mode, "formats": [f for f in cs.export_formats.split(",") if f]},
                       "frames": frames})
    controllers = []
    for c in db.query(Controller).filter_by(server_id=server.id, active=True).order_by(Controller.name).all():
        ins = (db.query(ControllerInput, Court).outerjoin(Court, Court.id == ControllerInput.court_id)
               .filter(ControllerInput.controller_id == c.id).order_by(ControllerInput.input_ref).all())
        controllers.append({"id": c.id, "kind": c.kind, "name": c.name, "portId": c.port_id, "baud": c.baud, "address": c.address,
                            "inputsCapacity": c.inputs_capacity, "debounceS": c.debounce_s, "cooldownS": c.cooldown_s, "minIntervalS": c.min_interval_s,
                            "reconnect": c.reconnect_behavior,
                            "inputs": [{"ref": i.input_ref, "label": i.label, "courtKey": ct.local_court_key if ct else None, "enabled": i.enabled} for i, ct in ins]})
    return {"schema": SCHEMA, "serverId": server.id, "arenaId": server.arena_id, "courts": courts, "controllers": controllers}


def validate_snapshot(snap: dict) -> list[str]:
    """Valida o conjunto ANTES de salvar/aplicar. Retorna lista de erros em português (vazia = ok)."""
    errs: list[str] = []
    keys = [c["key"] for c in snap["courts"]]
    if len(set(keys)) != len(keys):
        errs.append("há quadras com a mesma chave local")
    for c in snap["courts"]:
        w, r = f"Quadra {c['label']}", c["replay"]
        if c["active"] and not c["camera"]:
            errs.append(f"{w}: ativa, mas sem câmera associada")
        if c["camera"] and not c["camera"]["hasSecret"] and c["camera"]["username"]:
            errs.append(f"{w}: câmera com usuário, mas sem senha cadastrada")
        if not (1 <= r["preS"] <= 120) or not (0 <= r["postS"] <= 60):
            errs.append(f"{w}: duração do replay fora dos limites (antes 1–120 s, depois 0–60 s)")
        if not (1 <= r["segmentS"] <= 10) or r["bufferS"] < r["preS"] + r["postS"] + 2 * r["segmentS"]:
            errs.append(f"{w}: buffer menor que antes + depois + 2 segmentos")
        if not (1 <= r["priority"] <= 10) or r["reconnectMinS"] > r["reconnectMaxS"] or r["reconnectMinS"] <= 0:
            errs.append(f"{w}: política de reconexão/prioridade inválida")
        if r["audioMode"] not in ("original", "music", "mix", "mute"):
            errs.append(f"{w}: tratamento de áudio inválido")
        for orient in ("horizontal", "vertical"):
            f = c["frames"][orient]
            if f:
                try:
                    framing.parse_size(f["out"])
                    g = framing.compute(f["layout"], *framing.parse_size(f["out"]), f["width"], f["height"], f["window"])
                    if g["window"]["w"] < 64 or g["window"]["h"] < 64:
                        errs.append(f"{w}: janela do vídeo ficou pequena demais na moldura {orient}")
                except ValueError as e:
                    errs.append(f"{w}: moldura {orient}: {e}")
    refs, courts = set(), set(keys)
    for k in snap["controllers"]:
        if len(k["inputs"]) > k["inputsCapacity"]:
            errs.append(f"Controlador {k['name']}: {len(k['inputs'])} entradas configuradas, mas o firmware suporta {k['inputsCapacity']}")
        if not (0 <= k["debounceS"] <= 5) or not (0 <= k["cooldownS"] <= 120) or not (0 <= k["minIntervalS"] <= 600):
            errs.append(f"Controlador {k['name']}: debounce/cooldown/intervalo fora dos limites")
        if k["kind"] == "arduino_usb" and not k["portId"]:
            errs.append(f"Controlador {k['name']}: selecione a porta USB detectada pelo agente")
        for i in k["inputs"]:
            if i["enabled"] and not i["courtKey"]:
                errs.append(f"Controlador {k['name']}: entrada {i['ref']} sem quadra associada")
            if i["courtKey"] and i["courtKey"] not in courts:
                errs.append(f"Controlador {k['name']}: entrada {i['ref']} aponta para quadra de outro servidor")
            if i["ref"] in refs:
                errs.append(f"entrada '{i['ref']}' usada em mais de um controlador")
            refs.add(i["ref"])
    return errs


# ------------------------------------------------------------------ revisões
def latest(db: Session, server_id: str) -> ConfigRevision | None:
    return db.query(ConfigRevision).filter_by(server_id=server_id).order_by(ConfigRevision.version.desc()).first()


def active_applied(db: Session, server_id: str) -> ConfigRevision | None:
    return (db.query(ConfigRevision).filter_by(server_id=server_id, status="aplicado").order_by(ConfigRevision.version.desc()).first())


def create_revision(db: Session, server: LocalServer, user_id: str, expected: int, note: str = "", apply: bool = False,
                    allow_restart: bool = False, snapshot: dict | None = None, reverted_from: int | None = None) -> ConfigRevision:
    cur = latest(db, server.id)
    cur_v = cur.version if cur else 0
    if expected != cur_v:
        raise bad("A configuração mudou desde que você abriu a tela. Recarregue para ver a versão mais recente.", 409, currentVersion=cur_v)
    snap = snapshot if snapshot is not None else build_snapshot(db, server)
    errs = validate_snapshot(snap)
    if errs:
        raise HTTPException(422, {"message": "Configuração inválida", "errors": errs})
    rev = ConfigRevision(arena_id=server.arena_id, server_id=server.id, version=cur_v + 1, status="pendente" if apply else "salvo",
                         snapshot_json=json.dumps(snap, sort_keys=True), base_version=expected, allow_restart=allow_restart, note=note[:200],
                         created_by=user_id, reverted_from=reverted_from)
    db.add(rev)
    if apply:
        supersede_pending(db, server.id, keep_version=rev.version)
    db.flush()
    return rev


def supersede_pending(db: Session, server_id: str, keep_version: int) -> None:
    """Uma versão mais nova pendente substitui as anteriores ainda NÃO aplicadas (evita reaplicar operações antigas)."""
    for r in db.query(ConfigRevision).filter(ConfigRevision.server_id == server_id, ConfigRevision.status.in_(("pendente", "enviado")),
                                             ConfigRevision.version < keep_version).all():
        r.status, r.error, r.finished_at = "substituido", "substituída por versão mais nova antes de ser aplicada", utcnow()


def apply_revision(db: Session, server: LocalServer, rev: ConfigRevision, expected_latest: int | None = None) -> ConfigRevision:
    if rev.server_id != server.id:
        raise HTTPException(404, "revisão não encontrada")
    if rev.status != "salvo":
        raise bad(f"somente revisões salvas podem ser aplicadas (estado atual: {rev.status})", 409)
    cur = latest(db, server.id)
    if cur and cur.version != rev.version:
        raise bad("existe uma versão mais nova; aplique a mais recente ou reverta de forma explícita", 409, currentVersion=cur.version)
    rev.status = "pendente"
    return rev


def revert_to(db: Session, server: LocalServer, user_id: str, target_version: int, expected: int, apply: bool = True) -> ConfigRevision:
    target = db.query(ConfigRevision).filter_by(server_id=server.id, version=target_version).first()
    if not target or target.status in ("pendente", "enviado", "recebido", "aplicando"):
        raise HTTPException(404, "versão não encontrada")
    snap = json.loads(target.snapshot_json)
    rev = create_revision(db, server, user_id, expected, note=f"Reversão para a versão {target_version}", apply=apply, snapshot=snap, reverted_from=target_version)
    # a versão aplicada que está sendo desfeita passa a 'revertido' (histórico preserva o snapshot)
    cur = active_applied(db, server.id)
    if cur and cur.version != rev.version and cur.version != target_version:
        cur.status = "revertido"
    return rev


def rev_out(r: ConfigRevision, full: bool = False) -> dict:
    out = {"id": r.id, "serverId": r.server_id, "version": r.version, "status": r.status, "note": r.note, "baseVersion": r.base_version,
           "allowRestart": r.allow_restart, "revertedFrom": r.reverted_from, "error": r.error, "createdBy": r.created_by,
           "createdAt": r.created_at.isoformat() + "Z", "sentAt": r.sent_at and r.sent_at.isoformat() + "Z",
           "receivedAt": r.received_at and r.received_at.isoformat() + "Z", "finishedAt": r.finished_at and r.finished_at.isoformat() + "Z",
           "result": json.loads(r.result_json) if r.result_json else None}
    if full:
        out["snapshot"] = json.loads(r.snapshot_json)
    return out


def diff_snapshots(a: dict | None, b: dict) -> list[dict]:
    """Diferenças legíveis entre duas configurações (aplicada x desejada) por quadra/controlador/campo."""
    out: list[dict] = []

    def flat(prefix, v, acc):
        if isinstance(v, dict):
            for k, x in v.items():
                flat(f"{prefix}.{k}" if prefix else k, x, acc)
        else:
            acc[prefix] = v
    for kind, ident in (("courts", "key"), ("controllers", "id")):
        old = {i[ident]: i for i in (a or {}).get(kind, [])}
        new = {i[ident]: i for i in b.get(kind, [])}
        for k in sorted(set(old) | set(new)):
            label = (new.get(k) or old.get(k)).get("label") or (new.get(k) or old.get(k)).get("name") or k
            if k not in old:
                out.append({"scope": kind, "item": label, "change": "adicionado"})
            elif k not in new:
                out.append({"scope": kind, "item": label, "change": "removido"})
            else:
                fa, fb = {}, {}
                flat("", {x: y for x, y in old[k].items() if x not in ("inputs",)}, fa)
                flat("", {x: y for x, y in new[k].items() if x not in ("inputs",)}, fb)
                if kind == "controllers":
                    flat("entradas", {i["ref"]: i["courtKey"] for i in old[k]["inputs"]}, fa)
                    flat("entradas", {i["ref"]: i["courtKey"] for i in new[k]["inputs"]}, fb)
                for f in sorted(set(fa) | set(fb)):
                    if fa.get(f) != fb.get(f) and not f.endswith(("sha256", "bytes", "versionId")):
                        out.append({"scope": kind, "item": label, "field": f, "before": fa.get(f), "after": fb.get(f)})
    return out


def sweep_stale(db: Session, server: LocalServer, now=None) -> None:
    """Revisão presa em 'enviado/recebido/aplicando' por muito tempo (agente caiu no meio) volta a 'pendente' para reentrega."""
    now = now or utcnow()
    for r in db.query(ConfigRevision).filter(ConfigRevision.server_id == server.id, ConfigRevision.status.in_(("enviado", "recebido", "aplicando"))).all():
        ref = r.received_at or r.sent_at
        if ref and now - ref > timedelta(minutes=10):
            r.status = "pendente"


# ------------------------------------------------------------------ saneamento de resultados do agente
_URL_CRED = re.compile(r"(rtsps?|https?)://[^\s'\"@/]+@")


def redact(text: str, secrets: list[str] | None = None) -> str:
    """Remove credenciais de qualquer texto vindo do agente antes de guardar/mostrar (URL com usuário:senha e senhas conhecidas)."""
    out = _URL_CRED.sub(r"\1://***@", str(text))
    for sec in secrets or []:
        if sec and len(sec) >= 3:
            out = out.replace(sec, "***")
    return out


def redact_obj(obj, secrets: list[str] | None = None, depth: int = 0):
    if depth > 8:
        return None
    if isinstance(obj, str):
        return redact(obj, secrets)[:500]
    if isinstance(obj, dict):
        return {str(k)[:60]: ("***" if str(k).lower() in ("password", "senha", "secret", "token") else redact_obj(v, secrets, depth + 1)) for k, v in list(obj.items())[:80]}
    if isinstance(obj, list):
        return [redact_obj(v, secrets, depth + 1) for v in obj[:80]]
    return obj
