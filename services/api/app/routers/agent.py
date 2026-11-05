"""Contrato Central <-> motor local da arena (v1). SOMENTE METADADOS: nenhum endpoint aceita arquivo de vídeo.

Autenticação: token por arena (hash no banco) + vínculo ao primeiro `nodeId` registrado. Toda escrita é filtrada pela
arena do token. Escritas só são aceitas pelo nó `primary` e com epoch de fencing compatível (preparação para o
servidor reserva da API central; ver docs/ALTA-DISPONIBILIDADE.md).
"""
from datetime import datetime, timedelta, timezone

import hashlib
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit, capacity
from ..config import settings
from ..db import get_db, utcnow
from ..models import ArenaAgent, Arena, Court, CourtRuntime, ReplayEvent, Video
from ..agent_security import agent_auth_v1 as agent_auth, rotate as rotate_credentials, rotation_hint, issue_credentials
from .ingest import _code
from ..delivery import purge_spool, spool_file
from ..db import SessionLocal
from ..models import RelayRequest

NODE_RE = r"^[A-Za-z0-9_.\-]{4,64}$"
KEY_RE = r"^[A-Za-z0-9_\-]{1,60}$"
LOCAL_ID_RE = r"^[A-Za-z0-9_.:\-]{8,120}$"


router = APIRouter(prefix="/api/agent/v1", tags=["agent"])  # cabeçalhos X-Central-* são aplicados no middleware (main.py)


def writer(request: Request, ag: ArenaAgent = Depends(agent_auth)) -> ArenaAgent:
    """Fencing: reserva (standby) não grava; nó com epoch menor que o já visto pelo agente é um primário obsoleto."""
    if settings.node_role != "primary":
        raise HTTPException(503, "nó reserva: escrita de agentes indisponível", headers={"Retry-After": "5"})
    seen = request.headers.get("x-agent-epoch")
    if seen and seen.isdigit() and int(seen) > settings.central_epoch:
        raise HTTPException(409, "epoch obsoleto: este nó não é mais o primário")
    return ag


def bound(request: Request, ag: ArenaAgent = Depends(writer)) -> ArenaAgent:
    node = request.headers.get("x-agent-node")
    if not ag.node_id:
        raise HTTPException(409, "agente não registrado: chame POST /api/agent/v1/register")
    if node != ag.node_id:
        raise HTTPException(409, "credencial vinculada a outro computador")
    return ag


def _courts(db: Session, arena_id: str) -> dict[str, Court]:
    return {c.local_court_key: c for c in db.query(Court).filter_by(arena_id=arena_id)}  # sempre escopado à arena do token


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


# ---------------- registro / configuração ----------------
class RegisterIn(BaseModel):
    nodeId: str = Field(pattern=NODE_RE)
    hostname: str = Field("", max_length=120)
    version: str = Field("", max_length=40)
    courts: list[str] = Field(default_factory=list, max_length=50)


@router.post("/register")
def register(body: RegisterIn, ag: ArenaAgent = Depends(writer), db: Session = Depends(get_db)):
    if ag.node_id and ag.node_id != body.nodeId:
        raise HTTPException(409, "credencial já vinculada a outro computador; crie uma nova credencial no App da Arena")
    first = ag.node_id is None
    ag.node_id, ag.hostname, ag.agent_version = body.nodeId, body.hostname or None, body.version or None
    ag.registered_at = ag.registered_at or utcnow()
    if ag.token_expires_at is None and settings.agent_token_ttl_days > 0:
        ag.token_expires_at = utcnow() + timedelta(days=settings.agent_token_ttl_days)
    arena = db.get(Arena, ag.arena_id)
    known = _courts(db, ag.arena_id)
    if first:
        audit.record(db, None, ag.arena_id, "agent.register", "agent", ag.id, {"node": body.nodeId, "host": body.hostname})
    db.commit()
    return {"agentId": ag.id, "arenaId": ag.arena_id, "epoch": settings.central_epoch, **rotation_hint(ag),
            "unknownCourts": sorted(k for k in body.courts if k not in known), "config": capacity.agent_config(db, arena)}


@router.get("/config")
def config(ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    return capacity.agent_config(db, db.get(Arena, ag.arena_id))


# ---------------- heartbeat ----------------
class SystemIn(BaseModel):
    cpuPct: float | None = Field(None, ge=0, le=100)
    load1: float | None = Field(None, ge=0, le=10000)
    memUsedPct: float | None = Field(None, ge=0, le=100)
    diskFreeBytes: int | None = Field(None, ge=0)
    diskTotalBytes: int | None = Field(None, ge=0)


class CourtStat(BaseModel):
    courtKey: str = Field(pattern=KEY_RE)
    state: str = Field("ok", pattern=r"^(ok|degraded|storage_failure|camera_down)$")
    queueDepth: int = Field(0, ge=0, le=1_000_000)
    completed: int = Field(0, ge=0)
    failed: int = Field(0, ge=0)
    lastRenderS: float | None = Field(None, ge=0, le=86400)
    avgRenderS: float | None = Field(None, ge=0, le=86400)
    p95RenderS: float | None = Field(None, ge=0, le=86400)
    storageUsedBytes: int = Field(0, ge=0)


class HeartbeatIn(BaseModel):
    version: str = Field("", max_length=40)
    configVersion: str = Field("", max_length=40)
    pendingSync: int = Field(0, ge=0, le=10_000_000)
    system: SystemIn = Field(default_factory=SystemIn)
    courts: list[CourtStat] = Field(default_factory=list, max_length=50)


@router.post("/heartbeat")
def heartbeat(body: HeartbeatIn, ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    now = utcnow()
    arena = db.get(Arena, ag.arena_id)
    ag.last_heartbeat_at, ag.agent_version, ag.pending_sync = now, body.version or ag.agent_version, body.pendingSync
    ag.config_ack = body.configVersion or None
    ag.system_json = body.system.model_dump_json()
    courts, unknown = _courts(db, ag.arena_id), []
    for cs in body.courts:
        ct = courts.get(cs.courtKey)
        if not ct:
            unknown.append(cs.courtKey)
            continue
        rt = db.get(CourtRuntime, ct.id) or CourtRuntime(court_id=ct.id, arena_id=ag.arena_id)
        rt.state, rt.queue_depth, rt.completed_total, rt.failed_total = cs.state, cs.queueDepth, cs.completed, cs.failed
        rt.last_render_s, rt.avg_render_s, rt.p95_render_s = cs.lastRenderS, cs.avgRenderS, cs.p95RenderS
        rt.storage_used_bytes, rt.updated_at = cs.storageUsedBytes, now
        db.merge(rt)
        capacity.record_sample(db, ag.arena_id, ct.id, cs.storageUsedBytes, None, None, now)
    total = sum(c.storageUsedBytes for c in body.courts if c.courtKey in courts)
    capacity.record_sample(db, ag.arena_id, None, total, body.system.diskFreeBytes, body.system.diskTotalBytes, now)
    current = capacity.agent_config(db, arena)["configVersion"]
    db.commit()
    return {"ok": True, "serverTime": now.isoformat() + "Z", "epoch": settings.central_epoch,
            "configVersion": current, "configChanged": current != body.configVersion, "unknownCourts": unknown,
            **rotation_hint(ag)}


# ---------------- sincronização de metadados de replays ----------------
class ReplayIn(BaseModel):
    localId: str = Field(pattern=LOCAL_ID_RE)  # id estável gerado no computador da arena => chave de idempotência
    courtKey: str = Field(pattern=KEY_RE)
    capturedAt: datetime
    durationS: float = Field(0, ge=0, le=3600)
    bytes: int = Field(0, ge=0, le=50 * 1024**3)
    width: int = Field(0, ge=0, le=8192)
    height: int = Field(0, ge=0, le=8192)
    hasAudio: bool = False
    videoCodec: str | None = Field(None, max_length=30)
    protected: bool = False
    deletedLocally: bool = False
    fileAvailable: bool = True  # o agente confirma que o MP4 existe em disco


class SyncIn(BaseModel):
    replays: list[ReplayIn] = Field(max_length=1000)


def _apply(db: Session, ag: ArenaAgent, it: ReplayIn, ct: Court) -> dict:
    v = db.query(Video).filter_by(arena_id=ag.arena_id, local_id=it.localId).first()
    if v:
        if v.court_id != ct.id:
            return {"localId": it.localId, "status": "rejected", "reason": "replay pertence a outra quadra"}
        if v.bytes != it.bytes or abs(v.duration - it.durationS) > 0.01:
            v.bytes, v.duration = it.bytes, it.durationS
        v.media_state = "ok" if it.fileAvailable else "missing"
        if it.deletedLocally and v.deleted_at is None:
            v.deleted_at = utcnow()
            audit.record(db, None, ag.arena_id, "video.deleted_local", "video", v.id, {"agent": ag.id})
        return {"localId": it.localId, "status": "duplicate", "videoId": v.id, "code": v.code}
    ev = db.query(ReplayEvent).filter_by(arena_id=ag.arena_id, idempotency_key=it.localId).first()
    at = _utc(it.capturedAt)
    if ev and ev.court_id != ct.id:
        return {"localId": it.localId, "status": "rejected", "reason": "evento pertence a outra quadra"}
    if not ev:
        ev = ReplayEvent(arena_id=ag.arena_id, court_id=ct.id, captured_at=at, source="agent-sync",
                         idempotency_key=it.localId, status="completed")
        db.add(ev)
        db.flush()
    ev.status = "completed"
    v = Video(arena_id=ag.arena_id, court_id=ct.id, event_id=ev.id, code=_code(db), storage_key="", thumb_key=None,
              bytes=it.bytes, duration=it.durationS, width=it.width, height=it.height, video_codec=it.videoCodec,
              has_audio=it.hasAudio, visibility="private", processing_status="ready", captured_at=at,
              storage_location="agent", local_id=it.localId, protected=it.protected,
              media_state="ok" if it.fileAvailable else "missing",
              deleted_at=utcnow() if it.deletedLocally else None)
    db.add(v)
    db.flush()
    return {"localId": it.localId, "status": "created", "videoId": v.id, "code": v.code}


@router.post("/sync/replays")
def sync_replays(body: SyncIn, ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    if len(body.replays) > settings.agent_sync_batch_max:
        raise HTTPException(413, f"lote acima de {settings.agent_sync_batch_max} itens")
    courts, results = _courts(db, ag.arena_id), []
    for it in body.replays:
        ct = courts.get(it.courtKey)
        if not ct or ct.status == "inactive":
            results.append({"localId": it.localId, "status": "rejected", "reason": "quadra desconhecida ou inativa nesta arena"})
            continue
        try:
            with db.begin_nested():  # savepoint: corrida entre dois envios do mesmo localId não derruba o lote
                results.append(_apply(db, ag, it, ct))
        except IntegrityError:
            r = db.query(Video).filter_by(arena_id=ag.arena_id, local_id=it.localId).first()
            if not r:
                results.append({"localId": it.localId, "status": "rejected", "reason": "conflito de integridade"})
            else:
                results.append({"localId": it.localId, "status": "duplicate", "videoId": r.id, "code": r.code})
    ag.last_used_at = utcnow()
    db.commit()
    arena = db.get(Arena, ag.arena_id)
    lim = capacity.arena_limit_bytes(db, arena)
    return {"results": results, "storageLevel": capacity.usage_level(capacity.arena_usage(db, arena.id), lim,
                                                                      arena.storage_warn_pct, arena.storage_critical_pct)}


@router.get("/sync/protection")
def protection(limit: int = Query(5000, ge=1, le=20000), ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    """Lista autoritativa de replays protegidos (a central é a fonte). O agente nunca remove o que está aqui."""
    rows = (db.query(Video.local_id).filter(Video.arena_id == ag.arena_id, Video.protected.is_(True),
                                            Video.deleted_at.is_(None), Video.local_id.isnot(None)).limit(limit).all())
    return {"localIds": [r[0] for r in rows]}


@router.get("/retention/plan")
def retention(ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    """Somente candidatos (dry-run). Nesta etapa nenhum componente apaga arquivos automaticamente."""
    return capacity.retention_plan(db, db.get(Arena, ag.arena_id))


@router.post("/credentials/rotate")
def rotate(request: Request, ag: ArenaAgent = Depends(writer), db: Session = Depends(get_db)):
    """O agente troca suas próprias credenciais. Resposta contém token e chave NOVOS (única vez); a anterior vale só
    pela janela de carência, para sobreviver a uma resposta perdida. Nada disso vai para log/auditoria."""
    token, key = rotate_credentials(ag, bool(getattr(request.state, "via_prev", False)))
    audit.record(db, None, ag.arena_id, "agent.rotate", "agent", ag.id, {"by": "agent"})
    db.commit()
    return {"token": token, "signingKey": key, "expiresAt": ag.token_expires_at.isoformat() + "Z" if ag.token_expires_at else None,
            "previousValidUntil": ag.prev_valid_until.isoformat() + "Z" if ag.prev_valid_until else None}


# ---------------- relay: entrega de vídeo (o agente busca por conexão de SAÍDA) ----------------
@router.get("/relay/next")
def relay_next(wait: int = Query(20, ge=0, le=60), ag: ArenaAgent = Depends(bound)):
    """Long-poll: devolve o pedido de trecho mais antigo da PRÓPRIA arena (204 se nada surgir)."""
    deadline = time.monotonic() + min(wait, settings.relay_poll_max_s)
    first = True
    while first or time.monotonic() < deadline:
        first = False
        with SessionLocal() as s:
            now = utcnow()
            cand = (s.query(RelayRequest.id).filter(RelayRequest.arena_id == ag.arena_id, RelayRequest.status == "pending",
                                                    RelayRequest.expires_at > now)
                    .order_by(RelayRequest.created_at).limit(3).all())
            for (rid,) in cand:
                n = s.query(RelayRequest).filter(RelayRequest.id == rid, RelayRequest.status == "pending").update(
                    {"status": "claimed", "agent_id": ag.id})
                s.commit()
                if n == 1:  # reivindicação atômica
                    r = s.get(RelayRequest, rid)
                    return {"requestId": r.id, "kind": r.kind, "ref": r.ref, "start": r.start, "end": r.end}
            # heartbeat implícito: o agente está vivo e pronto para servir
            s.query(ArenaAgent).filter_by(id=ag.id).update({"last_used_at": now})
            s.commit()
        time.sleep(0.05)
    return Response(status_code=204)


def _claimed(db: Session, ag: ArenaAgent, rid: str) -> RelayRequest:
    r = db.get(RelayRequest, rid)
    if not r or r.arena_id != ag.arena_id or r.agent_id != ag.id:  # outro agente/arena => inexistente
        raise HTTPException(404, "pedido não encontrado")
    if r.status != "claimed":
        raise HTTPException(409, "pedido não está aguardando dados")
    return r


@router.post("/relay/{rid}/data")
async def relay_data(rid: str, request: Request, ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    """Corpo binário = bytes do trecho. Validado contra o pedido; guardado em spool TEMPORÁRIO até a entrega."""
    r = _claimed(db, ag, rid)
    body = await request.body()
    want = r.end - r.start + 1
    total = request.headers.get("x-total-size", "")
    if not total.isdigit() or not (0 < len(body) <= want) or len(body) > settings.relay_window_bytes:
        r.status, r.error = "failed", "tamanho inválido"
        db.commit()
        raise HTTPException(422, "trecho inválido")
    if hashlib.sha256(body).hexdigest() != request.headers.get("x-chunk-sha256", ""):
        r.status, r.error = "failed", "integridade"
        db.commit()
        raise HTTPException(422, "hash do trecho não confere")
    path = spool_file(rid)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(body)
    tmp.replace(path)
    r.status, r.total_size, r.sha256, r.done_at = "done", int(total), hashlib.sha256(body).hexdigest(), utcnow()
    db.commit()
    purge_spool()
    return {"ok": True}


class RelayErr(BaseModel):
    code: str = Field(pattern=r"^(not_found|io_error|bad_request)$")
    message: str = Field("", max_length=200)


@router.post("/relay/{rid}/error")
def relay_error(rid: str, body: RelayErr, ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    r = _claimed(db, ag, rid)
    r.status, r.error, r.done_at = "failed", body.code, utcnow()
    db.commit()
    return {"ok": True}


@router.get("/status")
def status(ag: ArenaAgent = Depends(agent_auth)):
    return {"nodeId": settings.node_id, "role": settings.node_role, "epoch": settings.central_epoch,
            "heartbeatIntervalS": settings.agent_heartbeat_s}
