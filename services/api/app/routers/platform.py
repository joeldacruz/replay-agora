"""API do Portal do Desenvolvedor (global_admin + MFA). Gerencia metadados de TODAS as arenas.
Deliberadamente NÃO há endpoint para reproduzir/baixar mídia privada (acesso excepcional = pendência documentada)."""
import re
from pathlib import Path

from fastapi import File, UploadFile

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import audit, capacity, mailer, userman, storage
from ..db import get_db, utcnow
from ..deps import Auth, platform_auth
from ..models import (AuditLog, Arena, ArenaAgent, Court, ExportJob, Membership, OutboxMail, Plan, ReplayEvent, User, Video)
from ..security import hash_password
from ..userschemas import MemberChange, NewMember
from ..config import settings
from ..uploads import process_logo, to_temp
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
    adminEmail: str | None = Field(None, max_length=254)  # legado: conta existente
    manager: "ManagerIn | None" = None


class ManagerIn(BaseModel):
    """Gestor inicial da arena: 'existing' (userId/email de conta existente) ou 'new' (cadastro no mesmo ato)."""
    mode: str = Field(pattern=r"^(existing|new)$")
    userId: str | None = None
    email: str | None = Field(None, max_length=254)
    name: str | None = Field(None, max_length=120)
    phone: str | None = Field(None, max_length=30)
    authMethod: str = "password"
    sendInvite: bool = True


ArenaIn.model_rebuild()


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
            "logoUrl": f"/api/arenas/{ar.id}/logo" if ar.logo_key else None,
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


def _actor(a: Auth) -> userman.Actor:
    return userman.Actor("platform", a.user)


@router.post("/arenas", status_code=201)
def create_arena(body: ArenaIn, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    """Arena + primeiro gestor em UMA transação: se o gestor falhar (e-mail duplicado, inválido…), nada é gravado."""
    _valid_tz(body.timezone)
    if db.query(Arena).filter_by(slug=body.slug).first():
        raise HTTPException(409, "slug já utilizado")
    if body.planId and not db.get(Plan, body.planId):
        raise HTTPException(422, "plano inexistente")
    mg = body.manager
    if body.adminEmail and not mg:
        mg = ManagerIn(mode="existing", email=body.adminEmail)
    try:
        ar = Arena(slug=body.slug, name=body.name.strip(), plan_id=body.planId, timezone=body.timezone)
        db.add(ar)
        db.flush()
        audit.record(db, a.user.id, ar.id, "platform.arena_create", "arena", ar.id,
                     {**body.model_dump(exclude={"manager"}), "manager": mg.mode if mg else None})
        if mg:
            act = _actor(a)
            if mg.mode == "existing":
                u = db.get(User, mg.userId) if mg.userId else (
                    db.query(User).filter_by(email=userman.norm_email(mg.email)).first() if mg.email else None)
                if not u:
                    raise HTTPException(404, "responsável não cadastrado")
                userman.create_or_link(db, act, ar, name=None, email=u.email, role="arena_admin", link_existing=True)
            else:
                if not mg.email or not mg.name:
                    raise HTTPException(422, "informe nome e e-mail do gestor")
                userman.create_or_link(db, act, ar, name=mg.name, email=mg.email, role="arena_admin", phone=mg.phone,
                                       auth_method=mg.authMethod, send_invite=mg.sendInvite)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return _arena_out(db, ar)


@router.post("/arenas/{arena_id}/gestao-ticket")
def gestao_ticket(arena_id: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    """Ticket de uso único (60 s) para abrir a Gestão desta arena. NÃO é token de acesso: só é trocado, por POST, por uma
    sessão de arena com escopo de UMA arena. Exige sessão de plataforma com MFA (platform_auth)."""
    import hashlib
    import secrets
    from datetime import timedelta
    from ..models import OAuthFlow
    ar = _arena_or_404(db, arena_id)
    if ar.status != "active":
        raise HTTPException(409, "arena suspensa: reative-a antes de abrir a Gestão")
    ticket = secrets.token_urlsafe(32)
    now = utcnow()
    db.add(OAuthFlow(state=secrets.token_urlsafe(24), app="arena", intent="portal", nonce=ar.id, code_verifier="-", user_id=a.user.id,
                     expires_at=now + timedelta(seconds=60), ticket_hash=hashlib.sha256(ticket.encode()).hexdigest(),
                     ticket_user_id=a.user.id, ticket_expires_at=now + timedelta(seconds=60)))
    audit.record(db, a.user.id, ar.id, "platform.gestao_ticket", "arena", ar.id, {})
    db.commit()
    return {"ticket": ticket, "expiresIn": 60}

@router.post("/arenas/{arena_id}/logo")
async def update_arena_logo(arena_id: str, logo: UploadFile = File(...),
                            a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    """Somente admin global com MFA pode atualizar a identidade principal da arena."""
    ar = db.get(Arena, arena_id)
    if not ar:
        raise HTTPException(404, "arena não encontrada")
    tmp = await to_temp(logo, settings.max_logo_bytes, ".png")
    try:
        content, _, _ = process_logo(tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)
    from ..db import new_id
    key = storage.make_key(ar.id, "branding", f"logo-{new_id()}.png")
    storage.ensure_parent(key).write_bytes(content)
    old = ar.logo_key
    try:
        ar.logo_key = key
        audit.record(db, a.user.id, ar.id, "platform.arena_logo_update", "arena", ar.id, {"logo": "updated"})
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(key)
        raise
    storage.delete(old)
    return {"logoUrl": f"/api/arenas/{ar.id}/logo"}


@router.delete("/arenas/{arena_id}/logo", status_code=204)
def delete_arena_logo(arena_id: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    ar = db.get(Arena, arena_id)
    if not ar:
        raise HTTPException(404, "arena não encontrada")
    old = ar.logo_key
    ar.logo_key = None
    audit.record(db, a.user.id, ar.id, "platform.arena_logo_delete", "arena", ar.id, {"logo": "removed"})
    db.commit()
    storage.delete(old)


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


# ---- usuários (visão global) ----
class UserPatch(BaseModel):
    active: bool | None = None
    globalRole: str | None = Field(None, pattern=r"^(global_admin|none)$")
    name: str | None = Field(None, max_length=120)
    phone: str | None = Field(None, max_length=30)
    authMethod: str | None = None


class UserCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(max_length=254)
    phone: str | None = Field(None, max_length=30)
    authMethod: str = "password"
    globalRole: str | None = Field(None, pattern=r"^(global_admin)$")
    sendInvite: bool = True


@router.get("/users")
def users(q: str | None = Query(None, max_length=60), role: str | None = Query(None, max_length=20),
          status: str | None = Query(None, pattern=r"^(active|inactive|suspended)$"), limit: int = Query(100, ge=1, le=300),
          a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    return userman.list_users(db, None, q, role, status, limit=limit)


@router.post("/users", status_code=201)
def create_user(body: UserCreate, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    """Cria conta sem vínculo (ex.: novo administrador global ou usuário a vincular depois)."""
    e = userman.norm_email(body.email)
    if db.query(User).filter_by(email=e).first():
        raise HTTPException(409, detail={"code": "email_exists", "message": "já existe uma conta com este e-mail"})
    if body.authMethod not in userman.AUTH_METHODS:
        raise HTTPException(422, "método de autenticação inválido")
    u = User(name=body.name.strip(), email=e, password_hash=userman.UNUSABLE, phone=userman.norm_phone(body.phone),
             auth_method=body.authMethod, global_role=body.globalRole, google_link_allowed=body.authMethod in ("google", "both"))
    db.add(u)
    db.flush()
    userman.log(db, _actor(a), None, "create", u, {"globalRole": body.globalRole, "authMethod": body.authMethod})
    if body.sendInvite:
        userman.issue_token(db, u, "invite", a.user.id)
    db.commit()
    return userman.user_out(db, u)


@router.get("/users/{user_id}")
def get_user(user_id: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "usuário não encontrado")
    return userman.user_out(db, u)


@router.patch("/users/{user_id}")
def patch_user(user_id: str, body: UserPatch, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "usuário não encontrado")
    ch = body.model_dump(exclude_unset=True)
    act = _actor(a)
    if u.id == a.user.id and (ch.get("active") is False or ch.get("globalRole") == "none"):
        raise HTTPException(409, "não é possível remover o próprio acesso")
    if "active" in ch and ch["active"] != u.active:
        userman.set_account_active(db, act, u, ch["active"])
    if "globalRole" in ch:
        new = "global_admin" if ch["globalRole"] == "global_admin" else None
        if new != u.global_role:
            u.global_role = new
            if new is None:
                u.mfa_enabled, u.mfa_secret = False, None
                u.tokens_valid_after = utcnow()
            userman.log(db, act, None, "global_role", u, {"globalRole": new})
    if {"name", "phone", "authMethod"} & ch.keys():
        userman.update_profile(db, act, u, name=ch.get("name"), phone=ch.get("phone", "__unset__"), auth_method=ch.get("authMethod"))
    db.commit()
    return userman.user_out(db, u)


@router.post("/users/{user_id}/invite")
def resend_invite(user_id: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u or not u.active:
        raise HTTPException(404, "usuário não encontrado ou inativo")
    if u.password_set_at or u.last_login_at:
        raise HTTPException(409, "este usuário já ativou o acesso; use 'Redefinir acesso'")
    userman.issue_token(db, u, "invite", a.user.id)
    userman.log(db, _actor(a), None, "invite_resend", u)
    db.commit()
    return {"sent": True}


@router.post("/users/{user_id}/reset-access")
def reset_access(user_id: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u or not u.active:
        raise HTTPException(404, "usuário não encontrado ou inativo")
    userman.issue_token(db, u, "reset", a.user.id)
    userman.log(db, _actor(a), None, "reset_access", u)
    db.commit()
    return {"sent": True}


@router.get("/users/{user_id}/history")
def user_history(user_id: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    if not db.get(User, user_id):
        raise HTTPException(404, "usuário não encontrado")
    return userman.history(db, user_id=user_id)


# ---- usuários por arena (aba "Gestores e Usuários") ----
def _arena_or_404(db: Session, arena_id: str) -> Arena:
    ar = db.get(Arena, arena_id)
    if not ar:
        raise HTTPException(404, "arena não encontrada")
    return ar


def _membership_or_404(db: Session, arena_id: str, mid: str) -> Membership:
    m = db.get(Membership, mid)
    if not m or m.arena_id != arena_id:
        raise HTTPException(404, "vínculo não encontrado")
    return m


@router.get("/arenas/{arena_id}/members")
def arena_members(arena_id: str, q: str | None = Query(None, max_length=60), role: str | None = Query(None, max_length=20),
                  status: str | None = Query(None, pattern=r"^(active|suspended|inactive)$"),
                  a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    _arena_or_404(db, arena_id)
    return userman.list_members(db, arena_id, q, role, status)


@router.post("/arenas/{arena_id}/members", status_code=201)
def arena_member_add(arena_id: str, body: NewMember, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    ar = _arena_or_404(db, arena_id)
    link = body.linkExisting
    if body.name is None:
        if not db.query(User).filter_by(email=body.email.strip().lower()).first():
            raise HTTPException(404, "usuário não cadastrado (informe o nome para cadastrar um novo usuário)")
        link = True
    r = userman.create_or_link(db, _actor(a), ar, name=body.name, email=body.email, role=body.role, phone=body.phone,
                               active=body.active, auth_method=body.authMethod, link_existing=link, send_invite=body.sendInvite)
    db.commit()
    return {**userman.member_row(r["user"]), "created": r["created"]}


@router.patch("/arenas/{arena_id}/members/{member_id}")
def arena_member_patch(arena_id: str, member_id: str, body: MemberChange, a: Auth = Depends(platform_auth),
                       db: Session = Depends(get_db)):
    ar = _arena_or_404(db, arena_id)
    m = _membership_or_404(db, arena_id, member_id)
    act, ch = _actor(a), body.model_dump(exclude_unset=True)
    userman.change_membership(db, act, ar, m, role=ch.get("role"), status=ch.get("status"))
    if {"name", "phone", "authMethod"} & ch.keys():
        userman.update_profile(db, act, db.get(User, m.user_id), name=ch.get("name"), phone=ch.get("phone", "__unset__"),
                               auth_method=ch.get("authMethod"), arena_id=arena_id)
    db.commit()
    return userman.member_row(userman.user_out(db, db.get(User, m.user_id), arena_id))


@router.delete("/arenas/{arena_id}/members/{member_id}", status_code=204)
def arena_member_remove(arena_id: str, member_id: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    ar = _arena_or_404(db, arena_id)
    userman.remove_membership(db, _actor(a), ar, _membership_or_404(db, arena_id, member_id))
    db.commit()


@router.get("/arenas/{arena_id}/history")
def arena_history(arena_id: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    _arena_or_404(db, arena_id)
    return userman.history(db, arena_id=arena_id)


@router.get("/outbox")
def outbox(limit: int = Query(50, ge=1, le=200), a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    """Caixa de saída de homologação (sem SMTP). Visível só ao global_admin; mensagens enviadas por SMTP não retêm conteúdo."""
    return [{"id": r.id, "to": r.to_email, "subject": r.subject, "body": r.body, "kind": r.kind, "status": r.status,
             "createdAt": iso(r.created_at)} for r in db.query(OutboxMail).order_by(OutboxMail.created_at.desc()).limit(limit)]


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
