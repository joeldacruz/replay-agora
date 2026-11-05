"""Visão global de capacidade (portal do desenvolvedor, global_admin + MFA). Somente metadados/telemetria;
nenhuma rota entrega mídia. O override do limite de armazenamento de uma arena é uma ação global auditada."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import audit, capacity
from ..config import settings
from ..db import get_db
from ..deps import Auth, platform_auth
from ..models import Arena, ArenaAgent, CourtRuntime
from .arena_capacity import runtime_overview, storage_overview

router = APIRouter(prefix="/api/platform", tags=["portal-dev"])


@router.get("/capacity")
def all_arenas(a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    out = []
    for ar in db.query(Arena).order_by(Arena.name):
        st, rt = storage_overview(db, ar), runtime_overview(db, ar)
        runtime_rows = db.query(CourtRuntime).filter_by(arena_id=ar.id).all()
        out.append({"arenaId": ar.id, "name": ar.name, "status": ar.status, "connection": rt["connection"],
                    "usedBytes": st["usedBytes"], "limitBytes": st["limitBytes"], "level": st["level"],
                    "daysToFull": st["forecast"]["daysToFull"], "queueDepth": sum(r.queue_depth for r in runtime_rows),
                    "failed": sum(r.failed_total for r in runtime_rows),
                    "courtsDegraded": sum(1 for r in runtime_rows if r.state != "ok")})
    return {"node": {"id": settings.node_id, "role": settings.node_role, "epoch": settings.central_epoch}, "arenas": out}


@router.get("/arenas/{arena_id}/capacity")
def one_arena(arena_id: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    ar = db.get(Arena, arena_id)
    if not ar:
        raise HTTPException(404, "arena não encontrada")
    return {"storage": storage_overview(db, ar), "runtime": runtime_overview(db, ar)}


class LimitIn(BaseModel):
    storageLimitMb: int | None = Field(None, ge=100, le=100_000_000)  # None remove o override e volta ao plano


@router.put("/arenas/{arena_id}/storage-limit")
def set_limit(arena_id: str, body: LimitIn, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    ar = db.get(Arena, arena_id)
    if not ar:
        raise HTTPException(404, "arena não encontrada")
    ar.storage_limit_mb = body.storageLimitMb
    audit.record(db, a.user.id, ar.id, "platform.storage_limit", "arena", ar.id, body.model_dump())
    db.commit()
    return storage_overview(db, ar)


@router.get("/agents/connections")
def connections(a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    now = capacity.utcnow()
    return [{"id": g.id, "arenaId": g.arena_id, "state": capacity.connection_state(g, now), "nodeId": g.node_id,
             "version": g.agent_version, "pendingSync": g.pending_sync} for g in db.query(ArenaAgent)]
