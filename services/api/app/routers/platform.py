"""API do Portal do Desenvolvedor (global_admin + MFA). Gerencia metadados de TODAS as arenas.
Deliberadamente NÃO há endpoint para reproduzir/baixar mídia privada (acesso excepcional = pendência documentada)."""
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import audit, capacity
from ..db import get_db, utcnow
from ..deps import Auth, platform_auth
from ..models import (AuditLog, Arena, ArenaAgent, Court, ExportJob, Membership, Plan, ReplayEvent, User, Video)
from ..security import hash_password
from .arena import storage_used
from .serializers import iso

router = APIRouter(prefix="/api/platform", tags=["portal-dev"])
SLUG = r"^[a-z0-9][a-z0-9\-]{1,58}[a-z0-9]$"


# ---- planos ----
class PlanIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    maxCourts: int = Field(2, ge=1, le=200)
    maxStorageMb: int = Field(10240, ge=100)
    maxExportsPerUserDay: int = Field(20, ge=1, le=1000)
    modules: list[str] = Field(default_factory=lambda: ["sponsors", "music"])


def _plan_out(p: Plan) -> dict:
    return {"id": p.id, "name": p.name, "maxCourts": p.max_courts, "maxStorageMb": p.max_storage_mb,
            "maxExportsPerUserDay": p.max_exports_per_user_day, "modules": [m for m in p.modules.split(",") if m]}


@router.get("/plans")
def plans(a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    return [_plan_out(p) for p in db.query(Plan).order_by(Plan.name)]


@router.post("/plans", status_code=201)
def create_plan(body: PlanIn, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    if db.query(Plan).filter_by(name=body.name).first():
        raise HTTPException(409, "plano já existe")
    p = Plan(name=body.name, max_courts=body.maxCourts, max_storage_mb=body.maxStorageMb,
             max_exports_per_user_day=body.maxExportsPerUserDay, modules=",".join(body.modules))
    db.add(p)
    db.flush()
    audit.record(db, a.user.id, None, "platform.plan_create", "plan", p.id, body.model_dump())
    db.commit()
    return _plan_out(p)


@router.put("/plans/{plan_id}")
def update_plan(plan_id: str, body: PlanIn, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    p = db.get(Plan, plan_id)
    if not p:
        raise HTTPException(404, "plano não encontrado")
    p.name, p.max_courts, p.max_storage_mb = body.name, body.maxCourts, body.maxStorageMb
    p.max_exports_per_user_day, p.modules = body.maxExportsPerUserDay, ",".join(body.modules)
    audit.record(db, a.user.id, None, "platform.plan_update", "plan", p.id, body.model_dump())
    db.commit()
    return _plan_out(p)


# ---- arenas ----
class ArenaIn(BaseModel):
    slug: str = Field(pattern=SLUG)
    name: str = Field(min_length=2, max_length=120)
    planId: str | None = None
    timezone: str = "America/Sao_Paulo"
    adminEmail: str | None = Field(None, max_length=254)


class ArenaPatch(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    planId: str | None = None
    timezone: str | None = None
    status: str | None = Field(None, pattern=r"^(active|suspended)$")


def _arena_out(db: Session, ar: Arena) -> dict:
    plan = db.get(Plan, ar.plan_id) if ar.plan_id else None
    return {"id": ar.id, "slug": ar.slug, "name": ar.name, "status": ar.status, "timezone": ar.timezone,
            "plan": _plan_out(plan) if plan else None,
            "courts": db.query(func.count(Court.id)).filter_by(arena_id=ar.id).scalar(),
            "videos": db.query(func.count(Video.id)).filter(Video.arena_id == ar.id, Video.deleted_at.is_(None)).scalar(),
            "storageBytes": storage_used(db, ar.id), "createdAt": iso(ar.created_at),
            "admins": [{"id": u.id, "name": u.name, "email": u.email} for u in
                       db.query(User).join(Membership, Membership.user_id == User.id).filter(
                           Membership.arena_id == ar.id, Membership.role == "arena_admin", Membership.status == "active")]}


def _valid_tz(tz: str) -> None:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "fuso horário inválido")


@router.get("/arenas")
def arenas(a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    return [_arena_out(db, r) for r in db.query(Arena).order_by(Arena.name)]


@router.post("/arenas", status_code=201)
def create_arena(body: ArenaIn, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    _valid_tz(body.timezone)
    if db.query(Arena).filter_by(slug=body.slug).first():
        raise HTTPException(409, "slug já utilizado")
    if body.planId and not db.get(Plan, body.planId):
        raise HTTPException(422, "plano inexistente")
    ar = Arena(slug=body.slug, name=body.name.strip(), plan_id=body.planId, timezone=body.timezone)
    db.add(ar)
    db.flush()
    if body.adminEmail:
        u = db.query(User).filter_by(email=body.adminEmail.strip().lower()).first()
        if not u:
            raise HTTPException(404, "responsável não cadastrado")
        db.add(Membership(user_id=u.id, arena_id=ar.id, role="arena_admin"))
    audit.record(db, a.user.id, ar.id, "platform.arena_create", "arena", ar.id, body.model_dump())
    db.commit()
    return _arena_out(db, ar)


@router.get("/arenas/{arena_id}")
def get_arena(arena_id: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    ar = db.get(Arena, arena_id)
    if not ar:
        raise HTTPException(404, "arena não encontrada")
    return _arena_out(db, ar)


@router.patch("/arenas/{arena_id}")
def patch_arena(arena_id: str, body: ArenaPatch, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    ar = db.get(Arena, arena_id)
    if not ar:
        raise HTTPException(404, "arena não encontrada")
    ch = body.model_dump(exclude_unset=True)
    if "planId" in ch and ch["planId"] and not db.get(Plan, ch["planId"]):
        raise HTTPException(422, "plano inexistente")
    if ch.get("timezone"):
        _valid_tz(ch["timezone"])
    for k, attr in (("name", "name"), ("planId", "plan_id"), ("timezone", "timezone"), ("status", "status")):
        if k in ch:
            setattr(ar, attr, ch[k])
    audit.record(db, a.user.id, ar.id, "platform.arena_suspend" if ch.get("status") == "suspended" else "platform.arena_update",
                 "arena", ar.id, ch)
    db.commit()
    return _arena_out(db, ar)


class AdminAssign(BaseModel):
    email: str = Field(max_length=254)
    role: str = Field("arena_admin", pattern=r"^(arena_admin|arena_operator)$")


@router.post("/arenas/{arena_id}/administrators", status_code=201)
def assign_admin(arena_id: str, body: AdminAssign, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    ar = db.get(Arena, arena_id)
    u = db.query(User).filter_by(email=body.email.strip().lower()).first()
    if not ar or not u:
        raise HTTPException(404, "arena ou usuário não encontrado")
    m = db.query(Membership).filter_by(user_id=u.id, arena_id=ar.id).first()
    if m:
        m.role, m.status = body.role, "active"
    else:
        db.add(Membership(user_id=u.id, arena_id=ar.id, role=body.role))
    audit.record(db, a.user.id, ar.id, "platform.admin_assign", "membership", u.id, {"email": u.email, "role": body.role})
    db.commit()
    return _arena_out(db, ar)


# ---- usuários ----
class UserPatch(BaseModel):
    active: bool | None = None
    globalRole: str | None = Field(None, pattern=r"^(global_admin|none)$")


@router.get("/users")
def users(q: str | None = Query(None, max_length=60), limit: int = Query(50, ge=1, le=200),
          a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    qs = db.query(User)
    if q:
        qs = qs.filter((User.email.ilike(f"%{q}%")) | (User.name.ilike(f"%{q}%")))
    out = []
    for u in qs.order_by(User.created_at.desc()).limit(limit):
        ms = db.query(Membership, Arena).join(Arena, Arena.id == Membership.arena_id).filter(Membership.user_id == u.id).all()
        out.append({"id": u.id, "name": u.name, "email": u.email, "active": u.active, "globalRole": u.global_role,
                    "mfaEnabled": u.mfa_enabled,
                    "memberships": [{"arenaId": ar.id, "arena": ar.name, "role": m.role, "status": m.status} for m, ar in ms]})
    return out


@router.patch("/users/{user_id}")
def patch_user(user_id: str, body: UserPatch, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "usuário não encontrado")
    ch = body.model_dump(exclude_unset=True)
    if u.id == a.user.id and (ch.get("active") is False or ch.get("globalRole") == "none"):
        raise HTTPException(409, "não é possível remover o próprio acesso")
    if "active" in ch:
        u.active = ch["active"]
    if "globalRole" in ch:
        u.global_role = "global_admin" if ch["globalRole"] == "global_admin" else None
        if u.global_role is None:
            u.mfa_enabled, u.mfa_secret = False, None
    audit.record(db, a.user.id, None, "platform.user_update", "user", u.id, ch)
    db.commit()
    return {"id": u.id, "active": u.active, "globalRole": u.global_role}


# ---- agentes / status / auditoria ----
@router.get("/agents")
def agents(a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    rows = db.query(ArenaAgent, Arena.name).join(Arena, Arena.id == ArenaAgent.arena_id).order_by(Arena.name).all()
    now = utcnow()
    return [{"id": g.id, "arena": name, "arenaId": g.arena_id, "label": g.label, "active": g.active,
             "lastSeenAt": iso(g.last_used_at), "version": g.agent_version, "pendingSync": g.pending_sync,
             "online": bool(g.active and g.last_used_at and (now - g.last_used_at).total_seconds() < 300),
             "connectionState": capacity.connection_state(g, now)} for g, name in rows]


@router.get("/status")
def status(a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    cnt = lambda col, val: db.query(func.count(ExportJob.id)).filter(col == val).scalar()  # noqa: E731
    return {
        "arenas": {"active": db.query(func.count(Arena.id)).filter_by(status="active").scalar(),
                   "suspended": db.query(func.count(Arena.id)).filter_by(status="suspended").scalar()},
        "users": db.query(func.count(User.id)).scalar(),
        "videos": db.query(func.count(Video.id)).filter(Video.deleted_at.is_(None)).scalar(),
        "storageBytes": int(db.query(func.coalesce(func.sum(Video.bytes), 0)).filter(Video.deleted_at.is_(None)).scalar())
                        + int(db.query(func.coalesce(func.sum(ExportJob.output_bytes), 0)).scalar()),
        "queue": {"queued": cnt(ExportJob.status, "queued"), "processing": cnt(ExportJob.status, "processing"),
                  "failed": cnt(ExportJob.status, "failed")},
        "failedEvents": db.query(func.count(ReplayEvent.id)).filter_by(status="failed").scalar(),
        "agents": {"total": db.query(func.count(ArenaAgent.id)).filter_by(active=True).scalar()},
    }


@router.get("/audit")
def audit_list(arenaId: str | None = None, limit: int = Query(100, ge=1, le=500),
               a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    import json
    q = db.query(AuditLog, User.email).outerjoin(User, User.id == AuditLog.actor_id)
    if arenaId:
        q = q.filter(AuditLog.arena_id == arenaId)
    return [{"id": r.id, "actor": email, "arenaId": r.arena_id, "action": r.action, "resourceType": r.resource_type,
             "resourceId": r.resource_id, "changes": json.loads(r.changed_fields), "createdAt": iso(r.created_at)}
            for r, email in q.order_by(AuditLog.created_at.desc()).limit(limit)]
