"""Portal do Desenvolvedor — configuração remota (global_admin + MFA). Servidores, câmeras, controladores, quadras, revisões e tarefas.

Princípios: senha de câmera é write-only (cifrada, nunca devolvida); nenhum endpoint executa shell nem aceita URL/caminho do
sistema; toda referência cruzada (câmera↔quadra↔servidor↔moldura) é validada DENTRO da mesma arena; toda mutação gera auditoria.
"""
import json
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import audit, capacity, remote_config as rc, secretbox
from ..agent_security import issue_credentials
from ..db import get_db, utcnow
from ..deps import Auth, platform_auth
from ..models import (AgentTask, Arena, ArenaAgent, Camera, ConfigRevision, Controller, ControllerInput, Court, CourtSetting, LocalServer,
                      Plan, ServerEvent)
from ..security import hash_agent_token
from .serializers import iso

router = APIRouter(prefix="/api/platform", tags=["portal-dev-config"])
NAME_RE = r"^[^\x00-\x1f<>]{2,80}$"


def _arena(db: Session, arena_id: str) -> Arena:
    ar = db.get(Arena, arena_id)
    if not ar:
        raise HTTPException(404, "arena não encontrada")
    return ar


def _server(db: Session, server_id: str) -> LocalServer:
    s = db.get(LocalServer, server_id)
    if not s:
        raise HTTPException(404, "servidor não encontrado")
    return s


def _court(db: Session, court_id: str, arena_id: str | None = None) -> Court:
    c = db.get(Court, court_id)
    if not c or (arena_id and c.arena_id != arena_id):
        raise HTTPException(404, "quadra não encontrada nesta arena")
    return c


def _settings(db: Session, court: Court) -> CourtSetting:
    cs = db.get(CourtSetting, court.id)
    if not cs:
        cs = CourtSetting(court_id=court.id, arena_id=court.arena_id, retention_days=court.retention_days)
        db.add(cs)
        db.flush()
    return cs


# =========================================================================================== servidores
class ServerIn(BaseModel):
    arenaId: str
    name: str = Field(pattern=NAME_RE)
    description: str = Field("", max_length=300)


class ServerPatch(BaseModel):
    name: str | None = Field(None, pattern=NAME_RE)
    description: str | None = Field(None, max_length=300)


def server_out(db: Session, s: LocalServer, detail: bool = False) -> dict:
    ag = rc.server_agent(db, s)
    st = rc.record_state(db, s)
    sysd = json.loads(ag.system_json) if ag and ag.system_json else {}
    inv = json.loads(s.inventory_json) if s.inventory_json else {}
    lat = rc.latest(db, s.id)
    pending = db.query(func.count(ConfigRevision.id)).filter(ConfigRevision.server_id == s.id, ConfigRevision.status.in_(rc.ACTIVE_STATES)).scalar()
    out = {"id": s.id, "arenaId": s.arena_id, "arenaName": db.get(Arena, s.arena_id).name, "name": s.name, "description": s.description,
           "status": s.status, "state": st, "agentVersion": ag.agent_version if ag else None, "hostname": ag.hostname if ag else None,
           "lastHeartbeatAt": iso(ag.last_heartbeat_at) if ag else None, "createdAt": iso(s.created_at),
           "os": inv.get("os"), "ffmpeg": (inv.get("capabilities") or {}).get("ffmpeg"), "hw": (inv.get("capabilities") or {}).get("hw"),
           "system": {k: sysd.get(k) for k in ("cpuPct", "memUsedPct", "diskFreeBytes", "diskTotalBytes", "temperatureC") if k in sysd},
           "courts": db.query(func.count(CourtSetting.court_id)).filter_by(server_id=s.id).scalar(),
           "appliedVersion": s.applied_version, "latestVersion": lat.version if lat else 0, "latestStatus": lat.status if lat else None,
           "pendingRevisions": pending, "inventoryAt": iso(s.inventory_at) if s.inventory_at else None}
    if detail:
        out["events"] = [{"kind": e.kind, "detail": e.detail, "at": iso(e.created_at)} for e in
                         db.query(ServerEvent).filter_by(server_id=s.id).order_by(ServerEvent.created_at.desc()).limit(40)]
        out["inventory"] = inv
    return out


@router.get("/servers")
def servers(arenaId: str | None = None, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    q = db.query(LocalServer).order_by(LocalServer.name)
    if arenaId:
        q = q.filter(LocalServer.arena_id == arenaId)
    rows = [server_out(db, s) for s in q]
    db.commit()
    return rows


@router.post("/servers", status_code=201)
def create_server(body: ServerIn, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    """Provisiona o servidor: cria a identidade do agente (token + chave de assinatura mostrados UMA vez) e a vincula arena↔servidor."""
    ar = _arena(db, body.arenaId)
    if db.query(LocalServer).filter_by(arena_id=ar.id, name=body.name.strip()).first():
        raise HTTPException(409, "já existe um servidor com este nome na arena")
    ag = ArenaAgent(arena_id=ar.id, label=f"srv-{body.name.strip()}"[:80], token_hash="pending-" + hash_agent_token(body.name + str(utcnow())))
    db.add(ag)
    db.flush()
    token, key = issue_credentials(ag)
    s = LocalServer(arena_id=ar.id, name=body.name.strip(), description=body.description.strip(), agent_id=ag.id, created_by=a.user.id)
    db.add(s)
    db.flush()
    db.add(ServerEvent(server_id=s.id, arena_id=ar.id, kind="provisioned", detail="agente provisionado"))
    audit.record(db, a.user.id, ar.id, "platform.server_create", "server", s.id, {"name": s.name})
    db.commit()
    return {"server": server_out(db, s), "credentials": {"token": token, "signingKey": key, "expiresAt": iso(ag.token_expires_at)},
            "notice": "Estas credenciais são exibidas somente agora. Entregue-as ao agente do servidor local; elas não podem ser consultadas depois."}


@router.get("/servers/{server_id}")
def get_server(server_id: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    s = _server(db, server_id)
    out = server_out(db, s, detail=True)
    db.commit()
    return out


@router.patch("/servers/{server_id}")
def patch_server(server_id: str, body: ServerPatch, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    s = _server(db, server_id)
    ch = body.model_dump(exclude_unset=True)
    if "name" in ch:
        if db.query(LocalServer).filter(LocalServer.arena_id == s.arena_id, LocalServer.name == ch["name"].strip(), LocalServer.id != s.id).first():
            raise HTTPException(409, "já existe um servidor com este nome na arena")
        s.name = ch["name"].strip()
    if "description" in ch:
        s.description = ch["description"].strip()
    audit.record(db, a.user.id, s.arena_id, "platform.server_update", "server", s.id, ch)
    db.commit()
    return server_out(db, s)


@router.post("/servers/{server_id}/revoke")
def revoke_server(server_id: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    """Revoga o agente (comprometido): o token deixa de valer IMEDIATAMENTE. Configurações pendentes ficam retidas."""
    s = _server(db, server_id)
    ag = rc.server_agent(db, s)
    if ag:
        ag.active = False
        ag.prev_token_hash = ag.prev_sig_key = ag.prev_valid_until = None
    s.status, s.revoked_at = "revoked", utcnow()
    db.add(ServerEvent(server_id=s.id, arena_id=s.arena_id, kind="revoked", detail="agente revogado pelo Portal"))
    audit.record(db, a.user.id, s.arena_id, "platform.server_revoke", "server", s.id)
    db.commit()
    return server_out(db, s)


@router.post("/servers/{server_id}/credentials")
def reissue_credentials(server_id: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    """Reemite credenciais (reinstalação/reativação). A anterior deixa de valer imediatamente; as novas aparecem uma única vez."""
    s = _server(db, server_id)
    ag = rc.server_agent(db, s)
    if not ag:
        raise HTTPException(409, "servidor sem agente vinculado")
    from ..agent_security import force_rotate
    ag.active = True
    ag.node_id = None   # a nova instalação registra o seu próprio computador
    token, key = force_rotate(ag)
    s.status, s.revoked_at = "active", None
    db.add(ServerEvent(server_id=s.id, arena_id=s.arena_id, kind="provisioned", detail="credenciais reemitidas"))
    audit.record(db, a.user.id, s.arena_id, "platform.server_reissue", "server", s.id)
    db.commit()
    return {"credentials": {"token": token, "signingKey": key, "expiresAt": iso(ag.token_expires_at)},
            "notice": "Exibidas somente agora. O agente anterior foi desconectado."}


@router.get("/servers/{server_id}/events")
def server_events(server_id: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    s = _server(db, server_id)
    return [{"kind": e.kind, "detail": e.detail, "at": iso(e.created_at)} for e in
            db.query(ServerEvent).filter_by(server_id=s.id).order_by(ServerEvent.created_at.desc()).limit(200)]


# =========================================================================================== câmeras
class CameraIn(BaseModel):
    arenaId: str
    name: str = Field(pattern=NAME_RE)
    courtId: str | None = None
    serverId: str | None = None
    vendor: str = Field("", max_length=40)
    model: str = Field("", max_length=60)
    host: str = Field(max_length=253)
    macAddress: str | None = Field(None, pattern=r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
    rtspPort: int = Field(554, ge=1, le=65535)
    transport: str = "tcp"
    rtspPath: str = Field("", max_length=200)
    username: str = Field("", max_length=64)
    password: str | None = Field(None, max_length=128)
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    codec: str = "h264"
    profile: str = Field("", max_length=40)
    active: bool = True
    isPrimary: bool = True


class CameraPatch(BaseModel):
    expectedVersion: int
    name: str | None = Field(None, pattern=NAME_RE)
    courtId: str | None = None
    serverId: str | None = None
    vendor: str | None = Field(None, max_length=40)
    model: str | None = Field(None, max_length=60)
    host: str | None = Field(None, max_length=253)
    macAddress: str | None = Field(None, pattern=r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
    rtspPort: int | None = Field(None, ge=1, le=65535)
    transport: str | None = None
    rtspPath: str | None = Field(None, max_length=200)
    username: str | None = Field(None, max_length=64)
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    codec: str | None = None
    profile: str | None = Field(None, max_length=40)
    active: bool | None = None
    isPrimary: bool | None = None


class SecretIn(BaseModel):
    password: str = Field(max_length=128)
    expectedVersion: int | None = None


KNOWN_PROFILES = [
    {"id": "intelbras-realmonitor", "label": "Intelbras (cam/realmonitor)", "vendor": "Intelbras", "port": 554,
     "path": "/cam/realmonitor?channel=1&subtype=0", "models": ["VIP-1430-D-G2", "VIP-5460-Z-IA"], "note": "subtype=0 principal, subtype=1 secundário"},
    {"id": "hikvision", "label": "Hikvision (Streaming/Channels)", "vendor": "Hikvision", "port": 554, "path": "/Streaming/Channels/101", "models": [], "note": ""},
    {"id": "onvif-generic", "label": "Genérico (informar caminho)", "vendor": "", "port": 554, "path": "", "models": [], "note": "descoberta ONVIF quando o agente suportar"},
]


def camera_out(db: Session, c: Camera) -> dict:
    court = db.get(Court, c.court_id) if c.court_id else None
    return {"id": c.id, "arenaId": c.arena_id, "courtId": c.court_id, "courtLabel": court.label if court else None, "serverId": c.server_id,
            "name": c.name, "vendor": c.vendor, "model": c.model, "host": c.host, "macAddress": c.mac_address, "rtspPort": c.rtsp_port, "transport": c.transport,
            "rtspPath": c.rtsp_path, "username": c.username, "hasSecret": bool(c.secret_enc), "secretVersion": c.secret_version,
            "width": c.width, "height": c.height, "fps": c.fps, "codec": c.codec, "profile": c.profile, "active": c.active, "isPrimary": c.is_primary,
            "version": c.row_version, "lastTest": json.loads(c.last_test_json) if c.last_test_json else None, "updatedAt": iso(c.updated_at)}


def _check_refs(db: Session, arena_id: str, court_id: str | None, server_id: str | None) -> None:
    if court_id:
        _court(db, court_id, arena_id)
    if server_id:
        s = _server(db, server_id)
        if s.arena_id != arena_id:
            raise HTTPException(404, "servidor não pertence a esta arena")


@router.get("/camera-profiles")
def camera_profiles(a: Auth = Depends(platform_auth)):
    return KNOWN_PROFILES


@router.get("/cameras")
def cameras(arenaId: str, courtId: str | None = None, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    q = db.query(Camera).filter_by(arena_id=arenaId).order_by(Camera.name)
    if courtId:
        q = q.filter(Camera.court_id == courtId)
    return [camera_out(db, c) for c in q]


@router.post("/cameras", status_code=201)
def create_camera(body: CameraIn, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    _arena(db, body.arenaId)
    _check_refs(db, body.arenaId, body.courtId, body.serverId)
    f = rc.validate_camera_fields({"host": body.host, "rtsp_port": body.rtspPort, "rtsp_path": body.rtspPath, "username": body.username,
                                   "transport": body.transport, "codec": body.codec, "fps": body.fps, "width": body.width, "height": body.height})
    cam = Camera(arena_id=body.arenaId, court_id=body.courtId, server_id=body.serverId, name=body.name.strip(), vendor=body.vendor, model=body.model,
                 host=f["host"], mac_address=body.macAddress.upper() if body.macAddress else None, rtsp_port=body.rtspPort, transport=body.transport, rtsp_path=f["rtsp_path"], username=body.username,
                 width=body.width, height=body.height, fps=body.fps, codec=body.codec, profile=body.profile, active=body.active, is_primary=body.isPrimary)
    if body.password:
        cam.secret_enc, cam.secret_version = secretbox.seal(rc.validate_password(body.password)), 1
    db.add(cam)
    db.flush()
    audit.record(db, a.user.id, cam.arena_id, "platform.camera_create", "camera", cam.id,
                 {"name": cam.name, "host": cam.host, "court": cam.court_id, "passwordSet": bool(body.password)})
    db.commit()
    return camera_out(db, cam)


def _cam(db: Session, cid: str) -> Camera:
    c = db.get(Camera, cid)
    if not c:
        raise HTTPException(404, "câmera não encontrada")
    return c


@router.get("/cameras/{cid}")
def get_camera(cid: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    return camera_out(db, _cam(db, cid))


@router.patch("/cameras/{cid}")
def patch_camera(cid: str, body: CameraPatch, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    c = _cam(db, cid)
    if body.expectedVersion != c.row_version:
        raise HTTPException(409, {"message": "A câmera foi alterada por outra pessoa. Recarregue antes de salvar.", "currentVersion": c.row_version})
    ch = body.model_dump(exclude_unset=True, exclude={"expectedVersion"})
    _check_refs(db, c.arena_id, ch.get("courtId"), ch.get("serverId"))
    mapping = {"courtId": "court_id", "serverId": "server_id", "rtspPort": "rtsp_port", "rtspPath": "rtsp_path", "isPrimary": "is_primary", "macAddress": "mac_address"}
    val = rc.validate_camera_fields({mapping.get(k, k): v for k, v in ch.items() if k in ("host", "rtspPort", "rtspPath", "username", "transport", "codec", "fps", "width", "height")})
    for k, v in ch.items():
        attr = mapping.get(k, k)
        if attr == "mac_address" and v is not None:
            v = v.upper()
        setattr(c, attr, val.get(attr, v) if attr in val else v)
    c.row_version += 1
    c.updated_at = utcnow()
    audit.record(db, a.user.id, c.arena_id, "platform.camera_update", "camera", c.id, ch)
    db.commit()
    return camera_out(db, c)


@router.put("/cameras/{cid}/secret")
def set_camera_secret(cid: str, body: SecretIn, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    """Cadastra/substitui a senha. A resposta NUNCA contém a senha (nova ou antiga)."""
    c = _cam(db, cid)
    if body.expectedVersion is not None and body.expectedVersion != c.row_version:
        raise HTTPException(409, {"message": "A câmera foi alterada por outra pessoa. Recarregue antes de salvar.", "currentVersion": c.row_version})
    c.secret_enc = secretbox.seal(rc.validate_password(body.password))
    c.secret_version += 1
    c.row_version += 1
    c.updated_at = utcnow()
    audit.record(db, a.user.id, c.arena_id, "platform.camera_secret_set", "camera", c.id, {"secretVersion": c.secret_version})
    db.commit()
    return camera_out(db, c)


@router.delete("/cameras/{cid}", status_code=204)
def delete_camera(cid: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    c = _cam(db, cid)
    audit.record(db, a.user.id, c.arena_id, "platform.camera_delete", "camera", c.id, {"name": c.name})
    db.delete(c)
    db.commit()


# =========================================================================================== controladores
class ControllerIn(BaseModel):
    serverId: str
    kind: str = "arduino_usb"
    name: str = Field(pattern=NAME_RE)
    portId: str = Field("", max_length=160)
    baud: int = Field(9600, ge=1200, le=115200)
    address: str = Field("", max_length=253)
    inputsCapacity: int = Field(1, ge=1, le=64)
    debounceS: float = Field(0.3, ge=0, le=5)
    cooldownS: float = Field(3.0, ge=0, le=120)
    minIntervalS: float = Field(0.0, ge=0, le=600)
    reconnect: str = "discard_pending"
    active: bool = True


class ControllerPatch(BaseModel):
    expectedVersion: int
    name: str | None = Field(None, pattern=NAME_RE)
    portId: str | None = Field(None, max_length=160)
    baud: int | None = Field(None, ge=1200, le=115200)
    address: str | None = Field(None, max_length=253)
    inputsCapacity: int | None = Field(None, ge=1, le=64)
    debounceS: float | None = Field(None, ge=0, le=5)
    cooldownS: float | None = Field(None, ge=0, le=120)
    minIntervalS: float | None = Field(None, ge=0, le=600)
    reconnect: str | None = None
    active: bool | None = None


class InputIn(BaseModel):
    inputRef: str = Field(pattern=rc.REF_RE.pattern)
    label: str = Field("", max_length=60)
    courtId: str | None = None
    enabled: bool = True


def controller_out(db: Session, c: Controller) -> dict:
    ins = db.query(ControllerInput).filter_by(controller_id=c.id).order_by(ControllerInput.input_ref).all()
    courts = {x.id: x.label for x in db.query(Court).filter(Court.id.in_([i.court_id for i in ins if i.court_id]))} if ins else {}
    return {"id": c.id, "arenaId": c.arena_id, "serverId": c.server_id, "kind": c.kind, "name": c.name, "portId": c.port_id, "baud": c.baud,
            "address": c.address, "inputsCapacity": c.inputs_capacity, "debounceS": c.debounce_s, "cooldownS": c.cooldown_s,
            "minIntervalS": c.min_interval_s, "reconnect": c.reconnect_behavior, "active": c.active, "version": c.row_version,
            "inputs": [{"id": i.id, "inputRef": i.input_ref, "label": i.label, "courtId": i.court_id, "courtLabel": courts.get(i.court_id),
                        "enabled": i.enabled, "lastEventAt": iso(i.last_event_at) if i.last_event_at else None} for i in ins]}


def _check_controller_fields(kind: str, reconnect: str | None, address: str | None, port_id: str | None) -> None:
    if kind not in rc.CONTROLLER_KINDS:
        raise rc.bad("tipo de controlador inválido")
    if reconnect is not None and reconnect not in ("discard_pending", "process_pending"):
        raise rc.bad("comportamento após reconexão inválido")
    if port_id and not rc.PORT_ID_RE.match(port_id):
        raise rc.bad("identificador de porta inválido")
    if address:
        rc.validate_host(address)


@router.get("/controllers")
def controllers(serverId: str | None = None, arenaId: str | None = None, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    q = db.query(Controller).order_by(Controller.name)
    if serverId:
        q = q.filter(Controller.server_id == serverId)
    if arenaId:
        q = q.filter(Controller.arena_id == arenaId)
    return [controller_out(db, c) for c in q]


@router.post("/controllers", status_code=201)
def create_controller(body: ControllerIn, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    s = _server(db, body.serverId)
    _check_controller_fields(body.kind, body.reconnect, body.address, body.portId)
    if db.query(Controller).filter_by(server_id=s.id, name=body.name.strip()).first():
        raise HTTPException(409, "já existe um controlador com este nome neste servidor")
    c = Controller(arena_id=s.arena_id, server_id=s.id, kind=body.kind, name=body.name.strip(), port_id=body.portId, baud=body.baud, address=body.address,
                   inputs_capacity=body.inputsCapacity, debounce_s=body.debounceS, cooldown_s=body.cooldownS, min_interval_s=body.minIntervalS,
                   reconnect_behavior=body.reconnect, active=body.active)
    db.add(c)
    db.flush()
    audit.record(db, a.user.id, s.arena_id, "platform.controller_create", "controller", c.id, {"name": c.name, "kind": c.kind})
    db.commit()
    return controller_out(db, c)


def _ctl(db: Session, cid: str) -> Controller:
    c = db.get(Controller, cid)
    if not c:
        raise HTTPException(404, "controlador não encontrado")
    return c


@router.get("/controllers/{cid}")
def get_controller(cid: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    return controller_out(db, _ctl(db, cid))


@router.patch("/controllers/{cid}")
def patch_controller(cid: str, body: ControllerPatch, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    c = _ctl(db, cid)
    if body.expectedVersion != c.row_version:
        raise HTTPException(409, {"message": "O controlador foi alterado por outra pessoa. Recarregue antes de salvar.", "currentVersion": c.row_version})
    ch = body.model_dump(exclude_unset=True, exclude={"expectedVersion"})
    _check_controller_fields(c.kind, ch.get("reconnect"), ch.get("address"), ch.get("portId"))
    mp = {"portId": "port_id", "inputsCapacity": "inputs_capacity", "debounceS": "debounce_s", "cooldownS": "cooldown_s", "minIntervalS": "min_interval_s",
          "reconnect": "reconnect_behavior"}
    if "inputsCapacity" in ch:
        used = db.query(func.count(ControllerInput.id)).filter_by(controller_id=c.id).scalar()
        if ch["inputsCapacity"] < used:
            raise rc.bad(f"o controlador já tem {used} entradas configuradas; remova entradas antes de reduzir a capacidade")
    for k, v in ch.items():
        setattr(c, mp.get(k, k), v.strip() if isinstance(v, str) and k == "name" else v)
    c.row_version += 1
    audit.record(db, a.user.id, c.arena_id, "platform.controller_update", "controller", c.id, ch)
    db.commit()
    return controller_out(db, c)


@router.delete("/controllers/{cid}", status_code=204)
def delete_controller(cid: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    c = _ctl(db, cid)
    audit.record(db, a.user.id, c.arena_id, "platform.controller_delete", "controller", c.id, {"name": c.name})
    db.delete(c)
    db.commit()


@router.post("/controllers/{cid}/inputs", status_code=201)
def add_input(cid: str, body: InputIn, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    c = _ctl(db, cid)
    n = db.query(func.count(ControllerInput.id)).filter_by(controller_id=c.id).scalar()
    if n >= c.inputs_capacity:
        raise rc.bad(f"este controlador suporta {c.inputs_capacity} entrada(s) (capacidade do firmware); não é possível adicionar mais")
    if db.query(ControllerInput).filter_by(controller_id=c.id, input_ref=body.inputRef).first():
        raise HTTPException(409, "entrada já cadastrada neste controlador")
    dup = (db.query(ControllerInput).join(Controller, Controller.id == ControllerInput.controller_id)
           .filter(Controller.server_id == c.server_id, ControllerInput.input_ref == body.inputRef).first())
    if dup:
        raise HTTPException(409, "a mesma entrada já está em uso por outro controlador deste servidor")
    _input_court_ok(db, c, body.courtId)
    i = ControllerInput(controller_id=c.id, arena_id=c.arena_id, input_ref=body.inputRef, label=body.label, court_id=body.courtId, enabled=body.enabled)
    db.add(i)
    c.row_version += 1
    audit.record(db, a.user.id, c.arena_id, "platform.input_add", "controller", c.id, {"input": body.inputRef, "court": body.courtId})
    db.commit()
    return controller_out(db, c)


def _input_court_ok(db: Session, c: Controller, court_id: str | None) -> None:
    if not court_id:
        return
    _court(db, court_id, c.arena_id)
    cs = db.get(CourtSetting, court_id)
    if not cs or cs.server_id != c.server_id:
        raise rc.bad("a quadra precisa estar vinculada ao mesmo servidor do controlador")


@router.patch("/controllers/{cid}/inputs/{iid}")
def patch_input(cid: str, iid: str, body: InputIn, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    c = _ctl(db, cid)
    i = db.get(ControllerInput, iid)
    if not i or i.controller_id != c.id:
        raise HTTPException(404, "entrada não encontrada")
    _input_court_ok(db, c, body.courtId)
    i.label, i.court_id, i.enabled = body.label, body.courtId, body.enabled
    c.row_version += 1
    audit.record(db, a.user.id, c.arena_id, "platform.input_update", "controller", c.id, {"input": i.input_ref, "court": body.courtId})
    db.commit()
    return controller_out(db, c)


@router.delete("/controllers/{cid}/inputs/{iid}", status_code=204)
def delete_input(cid: str, iid: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    c = _ctl(db, cid)
    i = db.get(ControllerInput, iid)
    if not i or i.controller_id != c.id:
        raise HTTPException(404, "entrada não encontrada")
    db.delete(i)
    c.row_version += 1
    audit.record(db, a.user.id, c.arena_id, "platform.input_delete", "controller", c.id, {"input": i.input_ref})
    db.commit()


# =========================================================================================== quadras
class CourtCreate(BaseModel):
    label: str = Field(pattern=NAME_RE)
    number: int = Field(1, ge=1, le=99)
    localCourtKey: str | None = Field(None, pattern=r"^[A-Za-z0-9_\-]{1,60}$")
    serverId: str | None = None


class CourtPatch(BaseModel):
    label: str | None = Field(None, pattern=NAME_RE)
    status: str | None = Field(None, pattern=r"^(active|maintenance|inactive)$")
    number: int | None = Field(None, ge=1, le=99)


class SettingsIn(BaseModel):
    expectedVersion: int
    serverId: str | None = None
    preS: float = Field(15.0, ge=1, le=120)
    postS: float = Field(5.0, ge=0, le=60)
    closeCountdownS: float = Field(2.0, ge=0, le=30)
    bufferS: float = Field(120.0, ge=10, le=3600)
    segmentS: float = Field(2.0, ge=1, le=10)
    storageLimitMb: int | None = Field(None, ge=100)
    retentionDays: int = Field(30, ge=1, le=3650)
    audioMode: str = "original"
    exportFormats: list[str] = Field(default_factory=lambda: ["horizontal"])
    priority: int = Field(5, ge=1, le=10)
    reconnectMinS: float = Field(1.0, gt=0, le=60)
    reconnectMaxS: float = Field(30.0, gt=0, le=600)
    frameHId: str | None = None
    frameVId: str | None = None
    framesEnabled: bool = True
    outH: str = "1920x1080"
    outV: str = "1080x1920"
    primaryCameraId: str | None = None


def settings_out(cs: CourtSetting) -> dict:
    return {"serverId": cs.server_id, "preS": cs.pre_s, "postS": cs.post_s, "closeCountdownS": cs.close_countdown_s, "bufferS": cs.buffer_s,
            "segmentS": cs.segment_s, "storageLimitMb": cs.storage_limit_mb, "retentionDays": cs.retention_days, "audioMode": cs.audio_mode,
            "exportFormats": [f for f in cs.export_formats.split(",") if f], "priority": cs.priority, "reconnectMinS": cs.reconnect_min_s,
            "reconnectMaxS": cs.reconnect_max_s, "frameHId": cs.frame_h_id, "frameVId": cs.frame_v_id, "framesEnabled": cs.frames_enabled,
            "outH": cs.out_h, "outV": cs.out_v, "version": cs.row_version}


def court_out(db: Session, c: Court) -> dict:
    cs = db.get(CourtSetting, c.id)
    cams = db.query(Camera).filter_by(court_id=c.id).order_by(Camera.is_primary.desc(), Camera.name).all()
    rt = None
    from ..models import CourtRuntime
    r = db.get(CourtRuntime, c.id)
    if r:
        rt = {"state": r.state, "updatedAt": iso(r.updated_at), "diag": json.loads(r.diag_json) if r.diag_json else None}
    ins = (db.query(ControllerInput, Controller).join(Controller, Controller.id == ControllerInput.controller_id).filter(ControllerInput.court_id == c.id).all())
    return {"id": c.id, "arenaId": c.arena_id, "label": c.label, "number": c.number, "status": c.status, "localCourtKey": c.local_court_key,
            "settings": settings_out(cs) if cs else None, "cameras": [{"id": x.id, "name": x.name, "host": x.host, "isPrimary": x.is_primary, "active": x.active,
                                                                        "hasSecret": bool(x.secret_enc)} for x in cams],
            "inputs": [{"controllerId": k.id, "controller": k.name, "inputRef": i.input_ref, "enabled": i.enabled} for i, k in ins], "runtime": rt}


@router.get("/arenas/{arena_id}/courts")
def arena_courts(arena_id: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    _arena(db, arena_id)
    return [court_out(db, c) for c in db.query(Court).filter_by(arena_id=arena_id).order_by(Court.number)]


@router.post("/arenas/{arena_id}/courts", status_code=201)
def create_court(arena_id: str, body: CourtCreate, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    ar = _arena(db, arena_id)
    plan = db.get(Plan, ar.plan_id) if ar.plan_id else None
    n = db.query(func.count(Court.id)).filter_by(arena_id=ar.id).scalar()
    if plan and n >= plan.max_courts:
        raise HTTPException(409, f"limite de {plan.max_courts} quadras do plano atingido")
    key = body.localCourtKey or f"QUADRA_{body.number:02d}"
    if db.query(Court).filter_by(arena_id=ar.id, local_court_key=key).first():
        raise HTTPException(409, "chave de quadra local já utilizada")
    if body.serverId:
        s = _server(db, body.serverId)
        if s.arena_id != ar.id:
            raise HTTPException(404, "servidor não pertence a esta arena")
    c = Court(arena_id=ar.id, label=body.label.strip(), number=body.number, local_court_key=key)
    db.add(c)
    db.flush()
    db.add(CourtSetting(court_id=c.id, arena_id=ar.id, server_id=body.serverId))
    audit.record(db, a.user.id, ar.id, "platform.court_create", "court", c.id, {"label": c.label, "key": key})
    db.commit()
    return court_out(db, c)


@router.patch("/courts/{court_id}")
def patch_court(court_id: str, body: CourtPatch, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    c = _court(db, court_id)
    ch = body.model_dump(exclude_unset=True)
    for k, v in ch.items():
        setattr(c, k, v.strip() if k == "label" else v)
    audit.record(db, a.user.id, c.arena_id, "platform.court_update", "court", c.id, ch)
    db.commit()
    return court_out(db, c)


@router.put("/courts/{court_id}/settings")
def put_settings(court_id: str, body: SettingsIn, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    from ..models import Frame
    from .. import framing
    c = _court(db, court_id)
    cs = _settings(db, c)
    if body.expectedVersion != cs.row_version:
        raise HTTPException(409, {"message": "A configuração da quadra foi alterada por outra pessoa. Recarregue antes de salvar.", "currentVersion": cs.row_version})
    if body.serverId:
        s = _server(db, body.serverId)
        if s.arena_id != c.arena_id:
            raise HTTPException(404, "servidor não pertence a esta arena")
    if body.audioMode not in ("original", "music", "mix", "mute"):
        raise rc.bad("tratamento de áudio inválido")
    fmts = [f for f in body.exportFormats if f in ("horizontal", "vertical")]
    if not fmts or len(fmts) != len(set(body.exportFormats)):
        raise rc.bad("formatos de exportação inválidos (horizontal e/ou vertical)")
    try:
        framing.parse_size(body.outH), framing.parse_size(body.outV)
    except ValueError as e:
        raise rc.bad(str(e)) from None
    for fid, orient in ((body.frameHId, "horizontal"), (body.frameVId, "vertical")):
        if fid:
            fr = db.get(Frame, fid)
            if not fr or fr.arena_id != c.arena_id or fr.deleted_at:
                raise HTTPException(404, "moldura não encontrada nesta arena")
            if fr.orientation not in (orient, "other"):
                raise rc.bad(f"a moldura '{fr.name}' é {fr.orientation}; selecione uma moldura {orient}")
    if body.primaryCameraId:
        cam = db.get(Camera, body.primaryCameraId)
        if not cam or cam.arena_id != c.arena_id:
            raise HTTPException(404, "câmera não encontrada nesta arena")
        for other in db.query(Camera).filter_by(court_id=c.id):
            other.is_primary = False
        cam.court_id, cam.is_primary = c.id, True
    cs.server_id, cs.pre_s, cs.post_s, cs.close_countdown_s, cs.buffer_s, cs.segment_s = body.serverId, body.preS, body.postS, body.closeCountdownS, body.bufferS, body.segmentS
    cs.storage_limit_mb, cs.retention_days, cs.audio_mode, cs.export_formats, cs.priority = body.storageLimitMb, body.retentionDays, body.audioMode, ",".join(fmts), body.priority
    cs.reconnect_min_s, cs.reconnect_max_s, cs.frame_h_id, cs.frame_v_id = body.reconnectMinS, body.reconnectMaxS, body.frameHId, body.frameVId
    cs.frames_enabled, cs.out_h, cs.out_v = body.framesEnabled, body.outH, body.outV
    c.retention_days = body.retentionDays
    cs.row_version += 1
    cs.updated_at = utcnow()
    audit.record(db, a.user.id, c.arena_id, "platform.court_settings", "court", c.id, body.model_dump(exclude={"expectedVersion"}))
    db.commit()
    return court_out(db, c)


# =========================================================================================== configuração: rascunho, revisões, reversão
class RevisionIn(BaseModel):
    expectedVersion: int
    note: str = Field("", max_length=200)
    apply: bool = False
    allowRestart: bool = False


class RevertIn(BaseModel):
    targetVersion: int
    expectedVersion: int
    allowRestart: bool = False


@router.get("/servers/{server_id}/config")
def server_config(server_id: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    """Rascunho atual (montado das telas) x última versão aplicada: diferenças, erros de validação e estado de cada versão."""
    s = _server(db, server_id)
    snap = rc.build_snapshot(db, s)
    lat, app = rc.latest(db, s.id), rc.active_applied(db, s.id)
    out = {"serverId": s.id, "draft": snap, "errors": rc.validate_snapshot(snap), "latestVersion": lat.version if lat else 0,
           "latest": rc.rev_out(lat) if lat else None, "applied": rc.rev_out(app) if app else None,
           "diffAgainstApplied": rc.diff_snapshots(json.loads(app.snapshot_json) if app else None, snap),
           "diffAgainstLatest": rc.diff_snapshots(json.loads(lat.snapshot_json) if lat else None, snap),
           "state": rc.record_state(db, s)}
    db.commit()
    return out


@router.post("/servers/{server_id}/revisions", status_code=201)
def create_revision(server_id: str, body: RevisionIn, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    s = _server(db, server_id)
    if s.status == "revoked":
        raise HTTPException(409, "servidor revogado: reemita as credenciais antes de aplicar configurações")
    rev = rc.create_revision(db, s, a.user.id, body.expectedVersion, body.note, body.apply, body.allowRestart)
    audit.record(db, a.user.id, s.arena_id, "platform.config_apply" if body.apply else "platform.config_save", "server", s.id, {"version": rev.version})
    db.commit()
    return rc.rev_out(rev, full=True)


@router.post("/servers/{server_id}/revisions/{version}/apply")
def apply_revision(server_id: str, version: int, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    s = _server(db, server_id)
    rev = db.query(ConfigRevision).filter_by(server_id=s.id, version=version).first()
    if not rev:
        raise HTTPException(404, "versão não encontrada")
    rc.apply_revision(db, s, rev)
    audit.record(db, a.user.id, s.arena_id, "platform.config_apply", "server", s.id, {"version": rev.version})
    db.commit()
    return rc.rev_out(rev)


@router.post("/servers/{server_id}/revert", status_code=201)
def revert(server_id: str, body: RevertIn, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    s = _server(db, server_id)
    rev = rc.revert_to(db, s, a.user.id, body.targetVersion, body.expectedVersion, apply=True)
    rev.allow_restart = body.allowRestart
    audit.record(db, a.user.id, s.arena_id, "platform.config_revert", "server", s.id, {"to": body.targetVersion, "version": rev.version})
    db.add(ServerEvent(server_id=s.id, arena_id=s.arena_id, kind="config_reverted", detail=f"reversão para v{body.targetVersion} (nova v{rev.version})"))
    db.commit()
    return rc.rev_out(rev, full=True)


@router.get("/servers/{server_id}/revisions")
def revisions(server_id: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    s = _server(db, server_id)
    return [rc.rev_out(r) for r in db.query(ConfigRevision).filter_by(server_id=s.id).order_by(ConfigRevision.version.desc()).limit(100)]


@router.get("/servers/{server_id}/revisions/{version}")
def get_revision(server_id: str, version: int, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    s = _server(db, server_id)
    rev = db.query(ConfigRevision).filter_by(server_id=s.id, version=version).first()
    if not rev:
        raise HTTPException(404, "versão não encontrada")
    return rc.rev_out(rev, full=True)


# =========================================================================================== tarefas de diagnóstico/operação (lista fechada)
SERVICES = {"agent": ("check",), "trigger_listener": ("check", "reconnect_device", "restart_component", "logs"),
            "render_queue": ("check", "restart_component", "logs"), "link_sync": ("check", "restart_component", "logs"),
            "storage_manager": ("check", "logs"), "monitor": ("check", "logs"), "capture": ("check", "restart_component", "logs")}
DISRUPTIVE = {("capture", "restart_component"), ("trigger_listener", "restart_component"), ("trigger_listener", "reconnect_device"),
              ("render_queue", "restart_component"), ("link_sync", "restart_component")}
TASK_KINDS = ("ping", "scan_devices", "camera_test", "controller_test", "trigger_dry_run", "service_action")


class TaskIn(BaseModel):
    kind: str
    cameraId: str | None = None
    step: str = "all"
    controllerId: str | None = None
    action: str = "comm"
    courtId: str | None = None
    service: str | None = None
    serviceAction: str | None = None
    confirm: bool = False
    seconds: int = Field(10, ge=1, le=30)


def task_out(t: AgentTask) -> dict:
    return {"id": t.id, "serverId": t.server_id, "kind": t.kind, "status": t.status, "createdAt": iso(t.created_at), "finishedAt": iso(t.finished_at) if t.finished_at else None,
            "result": json.loads(t.result_json) if t.result_json else None, "error": t.error}


@router.post("/servers/{server_id}/tasks", status_code=201)
def create_task(server_id: str, body: TaskIn, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    s = _server(db, server_id)
    if s.status == "revoked":
        raise HTTPException(409, "servidor revogado")
    if body.kind not in TASK_KINDS:
        raise HTTPException(422, "tipo de tarefa não permitido")
    params: dict = {}
    if body.kind == "camera_test":
        cam = _cam(db, body.cameraId or "")
        if cam.arena_id != s.arena_id:
            raise HTTPException(404, "câmera não encontrada nesta arena")
        if body.step not in ("connectivity", "auth", "rtsp", "stream", "snapshot", "all"):
            raise HTTPException(422, "etapa de teste inválida")
        params = {"cameraId": cam.id, "step": body.step}
    elif body.kind in ("controller_test",):
        c = _ctl(db, body.controllerId or "")
        if c.server_id != s.id:
            raise HTTPException(404, "controlador não pertence a este servidor")
        if body.action not in ("comm", "monitor"):
            raise HTTPException(422, "ação inválida")
        params = {"controllerId": c.id, "action": body.action, "seconds": body.seconds}
    elif body.kind == "trigger_dry_run":
        ct = _court(db, body.courtId or "", s.arena_id)
        params = {"courtKey": ct.local_court_key}
    elif body.kind == "service_action":
        if body.service not in SERVICES or body.serviceAction not in SERVICES[body.service]:
            raise HTTPException(422, "serviço/ação não permitidos")
        if (body.service, body.serviceAction) in DISRUPTIVE and not body.confirm:
            raise HTTPException(409, {"message": "Operação potencialmente disruptiva: confirme o impacto antes de executar.", "requiresConfirm": True})
        params = {"service": body.service, "action": body.serviceAction}
        if body.courtId:
            params["courtKey"] = _court(db, body.courtId, s.arena_id).local_court_key
    t = AgentTask(arena_id=s.arena_id, server_id=s.id, kind=body.kind, params_json=json.dumps(params), created_by=a.user.id,
                  expires_at=utcnow() + timedelta(minutes=5))
    db.add(t)
    db.flush()
    audit.record(db, a.user.id, s.arena_id, "platform.task", "server", s.id, {"kind": body.kind, **{k: v for k, v in params.items()}})
    db.commit()
    return task_out(t)


@router.get("/tasks/{task_id}")
def get_task(task_id: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    t = db.get(AgentTask, task_id)
    if not t:
        raise HTTPException(404, "tarefa não encontrada")
    if t.status in ("pending", "delivered") and t.expires_at < utcnow():
        t.status, t.finished_at, t.error = "expired", utcnow(), "o agente não respondeu a tempo (servidor offline?)"
        db.commit()
    return task_out(t)


@router.get("/servers/{server_id}/services")
def server_services(server_id: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    s = _server(db, server_id)
    inv = json.loads(s.inventory_json) if s.inventory_json else {}
    return {"state": rc.record_state(db, s), "services": inv.get("services", []), "inventoryAt": iso(s.inventory_at) if s.inventory_at else None,
            "allowedActions": {k: list(v) for k, v in SERVICES.items()}}
