"""Contrato Central <-> motor local da arena (v1). SOMENTE METADADOS: nenhum endpoint aceita arquivo de vídeo.

Autenticação: token por arena (hash no banco) + vínculo ao primeiro `nodeId` registrado. Toda escrita é filtrada pela
arena do token. Escritas só são aceitas pelo nó `primary` e com epoch de fencing compatível (preparação para o
servidor reserva da API central; ver docs/ALTA-DISPONIBILIDADE.md).
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit, capacity
from ..config import settings
from ..db import get_db, utcnow
from ..models import ArenaAgent, Arena, Court, CourtRuntime, ReplayEvent, Video
from .ingest import _code, agent_auth

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
    arena = db.get(Arena, ag.arena_id)
    known = _courts(db, ag.arena_id)
    if first:
        audit.record(db, None, ag.arena_id, "agent.register", "agent", ag.id, {"node": body.nodeId, "host": body.hostname})
    db.commit()
    return {"agentId": ag.id, "arenaId": ag.arena_id, "epoch": settings.central_epoch,
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
            "configVersion": current, "configChanged": current != body.configVersion, "unknownCourts": unknown}


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


class SyncIn(BaseModel):
    replays: list[ReplayIn] = Field(max_length=1000)


def _apply(db: Session, ag: ArenaAgent, it: ReplayIn, ct: Court) -> dict:
    v = db.query(Video).filter_by(arena_id=ag.arena_id, local_id=it.localId).first()
    if v:
        if v.court_id != ct.id:
            return {"localId": it.localId, "status": "rejected", "reason": "replay pertence a outra quadra"}
        if v.bytes != it.bytes or abs(v.duration - it.durationS) > 0.01:
            v.bytes, v.duration = it.bytes, it.durationS
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


@router.get("/status")
def status(ag: ArenaAgent = Depends(agent_auth)):
    return {"nodeId": settings.node_id, "role": settings.node_role, "epoch": settings.central_epoch,
            "heartbeatIntervalS": settings.agent_heartbeat_s}
