"""Armazenamento, retenção e saúde do motor local — escopo da PRÓPRIA arena (contexto /arena/{arena_id}).
O limite global da arena (override do plano) só pode ser alterado pelo portal; aqui a arena define limiares,
limites por quadra (≤ limite da arena), retenção por quadra e proteção de vídeos."""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import audit, capacity
from ..db import get_db, utcnow
from ..deps import ArenaCtx, ArenaGate
from ..models import Arena, ArenaAgent, Court, CourtRuntime, Video
from .serializers import iso

router = APIRouter(prefix="/api/arena/{arena_id}", tags=["arena-capacity"])
staff = ArenaGate("arena_admin", "arena_operator")
admin = ArenaGate("arena_admin")


def storage_overview(db: Session, arena: Arena) -> dict:
    lim = capacity.arena_limit_bytes(db, arena)
    used = capacity.arena_usage(db, arena.id)
    forecast = capacity.forecast_days_to_full([(r.ts, r.used_bytes) for r in capacity.samples(db, arena.id, None)], lim)
    rows = capacity.samples(db, arena.id, None)
    courts = []
    for ct in db.query(Court).filter_by(arena_id=arena.id).order_by(Court.number):
        cl = capacity.court_limit_bytes(ct, lim)
        cu = capacity.court_usage(db, ct.id)
        courts.append({"courtId": ct.id, "label": ct.label, "usedBytes": cu, "limitBytes": cl, "retentionDays": ct.retention_days,
                       "level": capacity.usage_level(cu, cl, arena.storage_warn_pct, arena.storage_critical_pct),
                       "forecast": capacity.forecast_days_to_full([(r.ts, r.used_bytes) for r in capacity.samples(db, arena.id, ct.id)], cl)})
    return {"arenaId": arena.id, "usedBytes": used, "limitBytes": lim, "limitSource": "arena" if arena.storage_limit_mb else "plan",
            "warnPct": arena.storage_warn_pct, "criticalPct": arena.storage_critical_pct,
            "level": capacity.usage_level(used, lim, arena.storage_warn_pct, arena.storage_critical_pct),
            "forecast": forecast, "diskForecast": capacity.disk_forecast(rows),
            "protectedVideos": db.query(Video).filter(Video.arena_id == arena.id, Video.protected.is_(True), Video.deleted_at.is_(None)).count(),
            "autoDelete": False, "courts": courts}


def runtime_overview(db: Session, arena: Arena) -> dict:
    agents = db.query(ArenaAgent).filter_by(arena_id=arena.id).all()
    rts = {r.court_id: r for r in db.query(CourtRuntime).filter_by(arena_id=arena.id)}
    now = utcnow()
    return {
        "connection": capacity.arena_connection(agents, now),
        "agents": [{"id": a.id, "label": a.label, "active": a.active, "state": capacity.connection_state(a, now),
                    "hostname": a.hostname, "version": a.agent_version, "lastHeartbeatAt": iso(a.last_heartbeat_at),
                    "pendingSync": a.pending_sync, "configApplied": a.config_ack,
                    "system": json.loads(a.system_json) if a.system_json else None} for a in agents],
        "courts": [{"courtId": c.id, "label": c.label, "state": (rts[c.id].state if c.id in rts else "unknown"),
                    "queueDepth": rts[c.id].queue_depth if c.id in rts else None,
                    "completed": rts[c.id].completed_total if c.id in rts else None,
                    "failed": rts[c.id].failed_total if c.id in rts else None,
                    "avgRenderS": rts[c.id].avg_render_s if c.id in rts else None,
                    "p95RenderS": rts[c.id].p95_render_s if c.id in rts else None,
                    "updatedAt": iso(rts[c.id].updated_at) if c.id in rts else None}
                   for c in db.query(Court).filter_by(arena_id=arena.id).order_by(Court.number)],
    }


@router.get("/storage")
def storage(c: ArenaCtx = Depends(staff), db: Session = Depends(get_db)):
    return storage_overview(db, c.arena)


@router.get("/runtime")
def runtime(c: ArenaCtx = Depends(staff), db: Session = Depends(get_db)):
    return runtime_overview(db, c.arena)


class PolicyIn(BaseModel):
    warnPct: int = Field(80, ge=10, le=99)
    criticalPct: int = Field(95, ge=11, le=100)


@router.put("/storage-policy")
def set_policy(body: PolicyIn, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    if body.warnPct >= body.criticalPct:
        raise HTTPException(422, "warnPct deve ser menor que criticalPct")
    c.arena.storage_warn_pct, c.arena.storage_critical_pct = body.warnPct, body.criticalPct
    audit.record(db, c.user.id, c.arena.id, "storage.policy", "arena", c.arena.id, body.model_dump())
    db.commit()
    return storage_overview(db, c.arena)


class CourtPolicyIn(BaseModel):
    storageLimitMb: int | None = Field(None, ge=1)
    retentionDays: int = Field(30, ge=1, le=3650)


@router.put("/courts/{court_id}/storage-policy")
def set_court_policy(court_id: str, body: CourtPolicyIn, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    ct = db.get(Court, court_id)
    if not ct or ct.arena_id != c.arena.id:  # IDOR: quadra de outra arena é "inexistente"
        raise HTTPException(404, "quadra não encontrada")
    alim = capacity.arena_limit_bytes(db, c.arena)
    if body.storageLimitMb and alim and body.storageLimitMb * capacity.MB > alim:
        raise HTTPException(422, "limite da quadra não pode exceder o limite da arena")
    ct.storage_limit_mb, ct.retention_days = body.storageLimitMb, body.retentionDays
    audit.record(db, c.user.id, c.arena.id, "storage.court_policy", "court", ct.id, body.model_dump())
    db.commit()
    return storage_overview(db, c.arena)


class ProtectIn(BaseModel):
    protected: bool


@router.post("/videos/{video_id}/protection")
def protect(video_id: str, body: ProtectIn, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if not v or v.arena_id != c.arena.id or v.deleted_at is not None:
        raise HTTPException(404, "vídeo não encontrado")
    v.protected = body.protected
    audit.record(db, c.user.id, c.arena.id, "video.protect" if body.protected else "video.unprotect", "video", v.id, {"code": v.code})
    db.commit()
    return {"id": v.id, "protected": v.protected}


@router.get("/retention/plan")
def retention_plan(c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    """Dry-run. Não existe endpoint que remova arquivos reais nesta etapa."""
    return capacity.retention_plan(db, c.arena)
