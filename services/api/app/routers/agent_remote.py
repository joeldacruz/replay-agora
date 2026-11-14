"""Contrato Central <-> agente local para CONFIGURAÇÃO REMOTA (v1). O agente sempre inicia a conexão (funciona atrás de NAT).

Autenticação/assinatura/anti-replay: as mesmas do restante de /api/agent/v1 (token por agente + HMAC + nonce + epoch).
O agente só enxerga o PRÓPRIO servidor, as PRÓPRIAS revisões e arquivos da PRÓPRIA arena. A senha da câmera é entregue somente
aqui, por TLS, ao agente autenticado, junto da revisão; nunca por consulta do Portal.
"""
import base64
import json

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import audit, remote_config as rc, secretbox, storage
from ..db import get_db, utcnow
from ..models import AgentTask, ArenaAgent, Camera, ConfigRevision, FrameVersion, LocalServer, ServerEvent
from .agent import bound

router = APIRouter(prefix="/api/agent/v1/remote", tags=["agent-remote"])
MAX_RESULT = 60_000


def _server(db: Session, ag: ArenaAgent) -> LocalServer:
    s = db.query(LocalServer).filter_by(agent_id=ag.id).first()
    if not s or s.status == "revoked":
        raise HTTPException(409, "este agente não está vinculado a um servidor ativo no Portal")
    return s


def _rev(db: Session, s: LocalServer, version: int) -> ConfigRevision:
    r = db.query(ConfigRevision).filter_by(server_id=s.id, version=version).first()
    if not r:
        raise HTTPException(404, "revisão não encontrada")
    return r


@router.get("/pending")
def pending(ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    """Próxima configuração a aplicar (a mais nova; versões antigas não aplicadas já foram substituídas) + segredos + arquivos."""
    s = _server(db, ag)
    rc.sweep_stale(db, s)
    rev = (db.query(ConfigRevision).filter(ConfigRevision.server_id == s.id, ConfigRevision.status == "pendente")
           .order_by(ConfigRevision.version.desc()).first())
    if not rev:
        db.commit()
        return Response(status_code=204)
    if rev.status == "pendente":
        rev.status, rev.sent_at = "enviado", utcnow()
    snap = json.loads(rev.snapshot_json)
    secrets, assets = {}, []
    for c in snap["courts"]:
        cam = c.get("camera")
        if cam and cam["hasSecret"]:
            row = db.get(Camera, cam["id"])
            pw = secretbox.unseal(row.secret_enc) if row and row.arena_id == s.arena_id else None
            secrets[cam["id"]] = {"username": cam["username"], "password": pw, "version": cam["secretVersion"], "ok": pw is not None}
        for orient in ("horizontal", "vertical"):
            f = c["frames"].get(orient)
            if f:
                assets.append({"versionId": f["versionId"], "sha256": f["sha256"], "bytes": f["bytes"], "url": f"/remote/assets/{f['versionId']}"})
    db.commit()
    return Response(json.dumps({"id": rev.id, "version": rev.version, "allowRestart": rev.allow_restart, "snapshot": snap, "secrets": secrets, "assets": assets}),
                    media_type="application/json", headers={"Cache-Control": "no-store"})


@router.post("/{version}/received")
def received(version: int, ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    s = _server(db, ag)
    r = _rev(db, s, version)
    if r.status in ("pendente", "enviado"):
        r.status, r.received_at = "recebido", utcnow()
        db.commit()
    return {"ok": True, "status": r.status}


@router.post("/{version}/progress")
def progress(version: int, ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    s = _server(db, ag)
    r = _rev(db, s, version)
    if r.status in ("pendente", "enviado", "recebido"):
        r.status = "aplicando"
        db.commit()
    return {"ok": True, "status": r.status}


class Item(BaseModel):
    kind: str = Field(max_length=20)
    key: str = Field("", max_length=80)
    ok: bool
    message: str = Field("", max_length=300)
    restartRequired: bool = False


class ResultIn(BaseModel):
    status: str = Field(pattern=r"^(aplicado|falha)$")
    activeVersion: int = Field(ge=0)
    items: list[Item] = Field(default_factory=list, max_length=200)
    error: str | None = Field(None, max_length=400)
    rolledBack: bool = False


@router.post("/{version}/result")
def result(version: int, body: ResultIn, ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    """Confirmação do AGENTE: somente aqui a versão passa a 'aplicada'. Idempotente para o mesmo desfecho; nunca volta de estado final."""
    s = _server(db, ag)
    r = _rev(db, s, version)
    if r.status in ("aplicado", "falha"):
        return {"ok": True, "status": r.status, "duplicate": True}
    if r.status in ("substituido", "revertido"):
        raise HTTPException(409, "esta versão foi substituída; o resultado foi ignorado")
    if r.status not in ("enviado", "recebido", "aplicando"):
        raise HTTPException(409, "revisão ainda não foi entregue ao agente")
    if version < s.applied_version:
        raise HTTPException(409, "resultado atrasado: versão mais recente já aplicada")
    if body.status == "aplicado" and body.activeVersion != version:
        raise HTTPException(422, "resultado aplicado exige activeVersion igual à versão confirmada")
    if body.status == "falha" and body.activeVersion != s.applied_version:
        raise HTTPException(409, "versão de rollback divergente do estado conhecido pela central")
    top = rc.latest(db, s.id)
    if body.activeVersion > (top.version if top else 0):
        raise HTTPException(422, "versão ativa informada não existe")
    r.status, r.finished_at = body.status, utcnow()
    r.error = rc.redact(body.error)[:400] if body.error else None
    r.result_json = json.dumps({"items": [i.model_dump() for i in body.items], "rolledBack": body.rolledBack, "activeVersion": body.activeVersion})[:MAX_RESULT]
    s.applied_version = body.activeVersion
    if body.status == "aplicado":
        for old in db.query(ConfigRevision).filter(ConfigRevision.server_id == s.id, ConfigRevision.status == "aplicado", ConfigRevision.version < r.version):
            old.status = "substituido"
    kind = "config_applied" if body.status == "aplicado" else "config_failed"
    db.add(ServerEvent(server_id=s.id, arena_id=s.arena_id, kind=kind, detail=f"v{version}" + ("" if body.status == "aplicado" else f": {r.error or 'falha'}")[:200]))
    audit.record(db, None, s.arena_id, "agent.config_" + body.status, "server", s.id, {"version": version, "active": body.activeVersion, "rolledBack": body.rolledBack})
    db.commit()
    return {"ok": True, "status": r.status}


@router.get("/assets/{version_id}")
def asset(version_id: str, ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    """Arquivo de moldura. Só da PRÓPRIA arena e somente se referenciado por uma revisão deste servidor."""
    s = _server(db, ag)
    v = db.get(FrameVersion, version_id)
    if not v or v.arena_id != s.arena_id:
        raise HTTPException(404, "arquivo não encontrado")
    revs = db.query(ConfigRevision).filter_by(server_id=s.id).order_by(ConfigRevision.version.desc()).limit(25).all()
    if not any(version_id in r.snapshot_json for r in revs):
        raise HTTPException(404, "arquivo não encontrado")
    data = storage.path_for(v.storage_key).read_bytes()
    return Response(data, media_type="image/png", headers={"X-Sha256": v.sha256, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


# ------------------------------------------------------------------ tarefas
@router.get("/tasks/next")
def next_task(ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    s = _server(db, ag)
    now = utcnow()
    for t in db.query(AgentTask).filter(AgentTask.server_id == s.id, AgentTask.status.in_(("pending", "delivered")), AgentTask.expires_at < now):
        t.status, t.finished_at, t.error = "expired", now, "o agente não respondeu a tempo"
    t = db.query(AgentTask).filter_by(server_id=s.id, status="pending").order_by(AgentTask.created_at).first()
    if not t:
        db.commit()
        return Response(status_code=204)
    t.status, t.delivered_at = "delivered", now
    params = json.loads(t.params_json)
    if t.kind == "camera_test":
        cam = db.get(Camera, params["cameraId"])
        if not cam or cam.arena_id != s.arena_id:
            t.status, t.finished_at, t.error = "failed", now, "câmera não encontrada"
            db.commit()
            return Response(status_code=204)
        params["camera"] = {"host": cam.host, "port": cam.rtsp_port, "transport": cam.transport, "path": cam.rtsp_path, "username": cam.username,
                            "password": secretbox.unseal(cam.secret_enc), "width": cam.width, "height": cam.height, "fps": cam.fps, "codec": cam.codec}
    db.commit()
    return Response(json.dumps({"id": t.id, "kind": t.kind, "params": params}), media_type="application/json", headers={"Cache-Control": "no-store"})


class TaskResult(BaseModel):
    ok: bool
    result: dict = Field(default_factory=dict)
    error: str | None = Field(None, max_length=300)
    snapshotJpegB64: str | None = Field(None, max_length=600_000)


@router.post("/tasks/{task_id}/result")
def task_result(task_id: str, body: TaskResult, ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    s = _server(db, ag)
    t = db.get(AgentTask, task_id)
    if not t or t.server_id != s.id:
        raise HTTPException(404, "tarefa não encontrada")
    if t.status != "delivered":
        return {"ok": True, "duplicate": True}
    secrets = []
    params = json.loads(t.params_json)
    if t.kind == "camera_test":
        cam = db.get(Camera, params.get("cameraId", ""))
        if cam:
            secrets = [secretbox.unseal(cam.secret_enc) or "", cam.username]
    clean = rc.redact_obj(body.result, secrets)
    t.result_json = json.dumps(clean)[:MAX_RESULT]
    t.error = rc.redact(body.error or "", secrets)[:300] or None
    t.status, t.finished_at = ("done" if body.ok else "failed"), utcnow()
    if t.kind == "camera_test" and cam:
        cam.last_test_json = json.dumps({"at": utcnow().isoformat() + "Z", "ok": body.ok, "code": clean.get("code"), "message": clean.get("message"),
                                         "stream": clean.get("stream")})[:2000]
        if body.snapshotJpegB64:
            try:
                raw = base64.b64decode(body.snapshotJpegB64, validate=True)
            except ValueError:
                raw = b""
            if raw[:3] == b"\xff\xd8\xff" and len(raw) <= 450_000:
                key = storage.make_key(cam.arena_id, "camtest", f"{cam.id}.jpg")
                storage.ensure_parent(key).write_bytes(raw)
    db.commit()
    return {"ok": True}


class InventoryIn(BaseModel):
    os: str | None = Field(None, max_length=120)
    capabilities: dict = Field(default_factory=dict)
    devices: dict = Field(default_factory=dict)
    services: list[dict] = Field(default_factory=list, max_length=60)
    controllers: list[dict] = Field(default_factory=list, max_length=40)
    events: list[dict] = Field(default_factory=list, max_length=100)


@router.post("/inventory")
def inventory(body: InventoryIn, ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    """Dispositivos detectados, serviços autorizados e capacidades (sem segredos)."""
    s = _server(db, ag)
    data = rc.redact_obj(body.model_dump())
    raw = json.dumps(data)
    if len(raw) > 90_000:
        raise HTTPException(413, "inventário grande demais")
    s.inventory_json, s.inventory_at = raw, utcnow()
    from ..models import Controller, ControllerInput
    for ev in body.events:      # último acionamento por entrada (atualiza o Portal)
        ref, ts = str(ev.get("input", ""))[:40], ev.get("at")
        if ref and isinstance(ts, (int, float)):
            from datetime import datetime
            row = (db.query(ControllerInput).join(Controller, Controller.id == ControllerInput.controller_id)
                   .filter(Controller.server_id == s.id, ControllerInput.input_ref == ref).first())
            if row:
                row.last_event_at = datetime.utcfromtimestamp(ts)
    rc.record_state(db, s)
    db.commit()
    return {"ok": True}
