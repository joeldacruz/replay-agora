"""Regras de gestão de usuários e vínculos com arenas. Usado pelo Portal (global_admin) e pelo App Gestão (arena_admin).
Toda regra de autorização fica AQUI e nos roteadores — o frontend apenas reflete. Nunca devolve senha, hash ou link de ativação."""
import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import audit, mailer
from .config import settings
from .db import utcnow
from .models import ROLES, AccountToken, Arena, AuditLog, Membership, User, UserIdentity
from .routers.serializers import iso

AUTH_METHODS = ("password", "google", "both")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
PHONE_RE = re.compile(r"^[0-9+()\-\s]{8,30}$")
UNUSABLE = "!"  # password_hash de contas sem senha definida (nenhuma senha confere)


@dataclass
class Actor:
    kind: str  # 'platform' | 'arena'
    user: User
    arena_ids: set = field(default_factory=set)  # arenas que o ator pode gerir (kind=arena)

    @property
    def is_platform(self) -> bool:
        return self.kind == "platform"


def norm_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not EMAIL_RE.match(e):
        raise HTTPException(422, "e-mail inválido")
    return e


def norm_phone(phone: str | None) -> str | None:
    p = (phone or "").strip()
    if not p:
        return None
    if not PHONE_RE.match(p):
        raise HTTPException(422, "telefone inválido")
    return p


def mask_name(name: str) -> str:
    parts = name.split()
    return " ".join(w[0] + "***" for w in parts[:3]) if parts else "***"


def _check_scope(actor: Actor, arena: Arena) -> None:
    if not actor.is_platform and arena.id not in actor.arena_ids:
        raise HTTPException(403, "arena fora do seu escopo")


def _check_assignable(actor: Actor, role: str) -> None:
    if role == "global_admin" or role not in ROLES:
        raise HTTPException(422, "perfil inválido para vínculo com arena")


def log(db: Session, actor: Actor, arena_id: str | None, action: str, user: User, changes: dict | None = None) -> None:
    audit.record(db, actor.user.id, arena_id, f"userman.{action}", "user", user.id, {"email": user.email, **(changes or {})})


# ---------- saída ----------
def user_out(db: Session, u: User, arena_id: str | None = None, scope: set | None = None) -> dict:
    google = db.query(UserIdentity).filter_by(user_id=u.id, provider="google").first() is not None
    q = db.query(Membership, Arena).join(Arena, Arena.id == Membership.arena_id).filter(Membership.user_id == u.id)
    if scope is not None:
        q = q.filter(Membership.arena_id.in_(scope))
    ms = [{"id": m.id, "arenaId": ar.id, "arena": ar.name, "role": m.role, "status": m.status} for m, ar in q]
    out = {"id": u.id, "name": u.name, "email": u.email, "phone": u.phone, "active": u.active, "globalRole": u.global_role,
           "authMethod": u.auth_method, "passwordSet": u.password_hash not in (UNUSABLE, ""), "googleLinked": google,
           "mfaEnabled": u.mfa_enabled, "lastLoginAt": iso(u.last_login_at), "invitedAt": iso(u.invited_at),
           "createdAt": iso(u.created_at), "memberships": ms}
    if arena_id:
        m = next((x for x in ms if x["arenaId"] == arena_id), None)
        out["membership"] = m
    return out


def list_users(db: Session, arena_id: str | None, q: str | None, role: str | None, status: str | None,
               scope: set | None = None, limit: int = 100) -> list[dict]:
    """arena_id: lista os vínculos da arena; None (só Portal): visão global. status: active|inactive (conta) ou membership."""
    qs = db.query(User)
    if arena_id:
        qs = qs.join(Membership, Membership.user_id == User.id).filter(Membership.arena_id == arena_id)
        if role:
            qs = qs.filter(Membership.role == role)
        if status in ("active", "suspended"):
            qs = qs.filter(Membership.status == status)
    elif role:
        if role == "global_admin":
            qs = qs.filter(User.global_role == "global_admin")
        else:
            qs = qs.filter(User.id.in_(db.query(Membership.user_id).filter(Membership.role == role)))
    if status == "inactive":
        qs = qs.filter(User.active.is_(False))
    if q:
        like = f"%{q.strip()}%"
        qs = qs.filter((User.email.ilike(like)) | (User.name.ilike(like)))
    return [user_out(db, u, arena_id, scope) for u in qs.order_by(User.name).limit(limit)]


# ---------- guardas ----------
def _other_active_admins(db: Session, arena_id: str, excluding_user: str) -> int:
    return (db.query(func.count(Membership.id)).join(User, User.id == Membership.user_id)
            .filter(Membership.arena_id == arena_id, Membership.role == "arena_admin", Membership.status == "active",
                    User.active.is_(True), Membership.user_id != excluding_user).scalar())


def last_admin_guard(db: Session, m: Membership) -> None:
    if m.role == "arena_admin" and m.status == "active" and _other_active_admins(db, m.arena_id, m.user_id) < 1:
        raise HTTPException(409, "a arena precisa manter pelo menos um gestor ativo; indique um substituto antes")


# ---------- convite / redefinição ----------
def _hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def issue_token(db: Session, u: User, kind: str, created_by: str | None, arena: Arena | None = None) -> None:
    """Cria token de uso único e envia por e-mail. Tokens anteriores do mesmo tipo são invalidados."""
    now = utcnow()
    for t in db.query(AccountToken).filter_by(user_id=u.id, kind=kind, used_at=None):
        t.used_at = now
    raw = secrets.token_urlsafe(32)
    ttl = timedelta(hours=settings.invite_ttl_h) if kind == "invite" else timedelta(minutes=settings.reset_ttl_min)
    db.add(AccountToken(user_id=u.id, kind=kind, token_hash=_hash(raw), expires_at=now + ttl, created_by=created_by))
    base = settings.public_base_url.rstrip("/")
    where = f" na arena {arena.name}" if arena else ""
    if kind == "invite":
        gtxt = ("\nVocê também pode entrar com a sua conta Google (mesmo e-mail), quando disponível.\n"
                if u.auth_method in ("google", "both") else "")
        if u.auth_method == "google":
            body = f"Olá, {u.name}.\n\nSeu acesso ao Replay Agora{where} foi criado. Entre com a conta Google deste e-mail em {base}/.{gtxt}"
            kindtxt = "invite"
        else:
            body = (f"Olá, {u.name}.\n\nSeu acesso ao Replay Agora{where} foi criado. Defina sua senha (link válido por "
                    f"{settings.invite_ttl_h} h, uso único):\n{base}/definir-senha.html?token={raw}\n{gtxt}")
            kindtxt = "invite"
        subject = "Convite — Replay Agora"
        u.invited_at = now
    else:
        body = (f"Olá, {u.name}.\n\nFoi solicitada a redefinição do seu acesso ao Replay Agora. Crie uma nova senha (link válido por "
                f"{settings.reset_ttl_min} min, uso único):\n{base}/definir-senha.html?token={raw}\n\n"
                "Se você não reconhece esta solicitação, ignore este e-mail.")
        subject, kindtxt = "Redefinição de acesso — Replay Agora", "reset"
    mailer.send(db, u.email, subject, body, kindtxt)


def consume_token(db: Session, raw: str, kinds=("invite", "reset")) -> tuple[AccountToken, User]:
    t = db.query(AccountToken).filter_by(token_hash=_hash(raw or "")).first()
    if not t or t.kind not in kinds or t.used_at is not None or t.expires_at < utcnow():
        raise HTTPException(400, "link inválido ou expirado — peça um novo ao gestor")
    u = db.get(User, t.user_id)
    if not u or not u.active:
        raise HTTPException(400, "link inválido ou expirado — peça um novo ao gestor")
    return t, u


# ---------- criação / vínculo ----------
def create_or_link(db: Session, actor: Actor, arena: Arena, *, name: str | None, email: str, role: str, phone: str | None = None,
                   active: bool = True, auth_method: str = "password", link_existing: bool = False, send_invite: bool = True,
                   commit: bool = False) -> dict:
    _check_scope(actor, arena)
    _check_assignable(actor, role)
    if auth_method not in AUTH_METHODS:
        raise HTTPException(422, "método de autenticação inválido")
    e = norm_email(email)
    u = db.query(User).filter_by(email=e).first()
    created = False
    if u:
        if not link_existing:
            raise HTTPException(409, detail={"code": "email_exists", "message": "já existe uma conta com este e-mail",
                                             "existing": {"id": u.id, "maskedName": mask_name(u.name), "email": u.email}})
        if u.global_role:
            raise HTTPException(422, "contas de administrador global não são vinculadas a arenas por esta tela")
        if db.query(Membership).filter_by(user_id=u.id, arena_id=arena.id).first():
            raise HTTPException(409, "usuário já faz parte desta arena")
    else:
        if not name or len(name.strip()) < 2:
            raise HTTPException(422, "informe o nome completo")
        u = User(name=name.strip()[:120], email=e, password_hash=UNUSABLE, phone=norm_phone(phone), active=active,
                 auth_method=auth_method, google_link_allowed=auth_method in ("google", "both"))
        db.add(u)
        db.flush()
        created = True
    m = Membership(user_id=u.id, arena_id=arena.id, role=role, status="active" if active else "suspended")
    db.add(m)
    db.flush()
    log(db, actor, arena.id, "create" if created else "link", u, {"role": role, "arena": arena.name, "membershipId": m.id,
                                                              "authMethod": u.auth_method})
    if created and send_invite and active:
        issue_token(db, u, "invite", actor.user.id, arena)
    elif not created and u.active and send_invite:
        mailer.send(db, u.email, "Novo vínculo — Replay Agora",
                    f"Olá, {u.name}.\n\nVocê foi vinculado à arena {arena.name} como {role}. Use o seu acesso atual.", "link")
    if commit:
        db.commit()
    return {"user": user_out(db, u, arena.id), "membershipId": m.id, "created": created}


def change_membership(db: Session, actor: Actor, arena: Arena, m: Membership, *, role: str | None = None,
                      status: str | None = None) -> Membership:
    _check_scope(actor, arena)
    if m.arena_id != arena.id:
        raise HTTPException(404, "vínculo não encontrado")
    u = db.get(User, m.user_id)
    if u.id == actor.user.id and (role not in (None, m.role) or status not in (None, m.status)):
        raise HTTPException(409, "não é possível alterar o próprio perfil ou situação")
    if u.global_role and not actor.is_platform:
        raise HTTPException(403, "conta de administrador global só é alterada no Portal")
    if role is not None:
        _check_assignable(actor, role)
    if status is not None and status not in ("active", "suspended"):
        raise HTTPException(422, "situação inválida")
    new_role, new_status = role or m.role, status or m.status
    if (m.role == "arena_admin" and m.status == "active") and (new_role != "arena_admin" or new_status != "active"):
        last_admin_guard(db, m)
    ch = {}
    if new_role != m.role:
        ch["role"] = {"de": m.role, "para": new_role}
    if new_status != m.status:
        ch["status"] = {"de": m.status, "para": new_status}
    m.role, m.status = new_role, new_status
    if ch:
        ch["arena"] = arena.name
        log(db, actor, arena.id, "membership_update", u, ch)
    return m


def remove_membership(db: Session, actor: Actor, arena: Arena, m: Membership) -> None:
    _check_scope(actor, arena)
    if m.arena_id != arena.id:
        raise HTTPException(404, "vínculo não encontrado")
    u = db.get(User, m.user_id)
    if u.id == actor.user.id:
        raise HTTPException(409, "não é possível remover o próprio vínculo")
    last_admin_guard(db, m)
    log(db, actor, arena.id, "membership_remove", u, {"role": m.role, "arena": arena.name})
    db.delete(m)


def _can_edit_profile(db: Session, actor: Actor, u: User) -> None:
    if actor.is_platform:
        return
    if u.global_role:
        raise HTTPException(403, "conta de administrador global só é alterada no Portal")
    other = db.query(func.count(Membership.id)).filter(Membership.user_id == u.id, Membership.arena_id.notin_(actor.arena_ids)).scalar()
    mine = db.query(func.count(Membership.id)).filter(Membership.user_id == u.id, Membership.arena_id.in_(actor.arena_ids)).scalar()
    if not mine:
        raise HTTPException(404, "usuário não encontrado")
    if other:
        raise HTTPException(403, "este usuário também pertence a outras arenas; só o Portal altera os dados da conta")


def update_profile(db: Session, actor: Actor, u: User, *, name=None, phone="__unset__", auth_method=None,
                   arena_id: str | None = None) -> User:
    _can_edit_profile(db, actor, u)
    ch = {}
    if name is not None:
        if len(name.strip()) < 2:
            raise HTTPException(422, "informe o nome completo")
        ch["name"] = name.strip()[:120]
    if phone != "__unset__":
        ch["phone"] = norm_phone(phone)
    if auth_method is not None:
        if auth_method not in AUTH_METHODS:
            raise HTTPException(422, "método de autenticação inválido")
        has_pw = u.password_hash not in (UNUSABLE, "")
        google = db.query(UserIdentity).filter_by(user_id=u.id, provider="google").first() is not None
        if auth_method == "google" and not google and not u.google_link_allowed and has_pw:
            u.google_link_allowed = True
        if auth_method == "password" and google and not has_pw:
            raise HTTPException(409, "defina uma senha antes de restringir ao e-mail e senha")
        if auth_method in ("google", "both"):
            u.google_link_allowed = True
        ch["authMethod"] = auth_method
    for k, attr in (("name", "name"), ("phone", "phone"), ("authMethod", "auth_method")):
        if k in ch:
            setattr(u, attr, ch[k])
    if ch:
        log(db, actor, arena_id, "profile_update", u, ch)
    return u


def set_account_active(db: Session, actor: Actor, u: User, active: bool) -> User:
    """Ativar/inativar a CONTA (todas as arenas). Só Portal. Preserva histórico e vínculos; revoga sessões ao inativar."""
    if not actor.is_platform:
        raise HTTPException(403, "apenas o Portal inativa a conta inteira")
    if u.id == actor.user.id and not active:
        raise HTTPException(409, "não é possível inativar a própria conta")
    if u.active and not active:
        for m in db.query(Membership).filter_by(user_id=u.id, role="arena_admin", status="active"):
            if _other_active_admins(db, m.arena_id, u.id) < 1:
                ar = db.get(Arena, m.arena_id)
                raise HTTPException(409, f"{u.name} é o único gestor ativo da arena {ar.name}; indique um substituto antes")
        u.tokens_valid_after = utcnow()
    u.active = active
    log(db, actor, None, "account_activate" if active else "account_inactivate", u)
    return u


# ---------- histórico ----------
def history(db: Session, *, user_id: str | None = None, arena_id: str | None = None, limit: int = 200,
            scope: set | None = None) -> list[dict]:
    q = db.query(AuditLog, User.email).outerjoin(User, User.id == AuditLog.actor_id).filter(AuditLog.action.like("userman.%"))
    if user_id:
        q = q.filter(AuditLog.resource_id == user_id)
        if scope is not None:  # gestor: somente eventos da(s) sua(s) arena(s)
            q = q.filter(AuditLog.arena_id.in_(scope))
    if arena_id:
        q = q.filter(AuditLog.arena_id == arena_id)
    return [{"id": r.id, "at": iso(r.created_at), "actor": email, "action": r.action.removeprefix("userman."),
             "arenaId": r.arena_id, "userId": r.resource_id, "changes": json.loads(r.changed_fields)}
            for r, email in q.order_by(AuditLog.created_at.desc()).limit(limit)]


def member_row(row: dict) -> dict:
    """Achata user_out(arena_id=...) em uma linha de tabela 'Gestores e Usuários' (id = id do vínculo)."""
    m = row["membership"]
    return {"id": m["id"], "userId": row["id"], "name": row["name"], "email": row["email"], "phone": row["phone"],
            "role": m["role"], "status": m["status"], "accountActive": row["active"], "authMethod": row["authMethod"],
            "passwordSet": row["passwordSet"], "googleLinked": row["googleLinked"], "lastLoginAt": row["lastLoginAt"],
            "invitedAt": row["invitedAt"], "otherArenas": len(row["memberships"]) - 1}


def list_members(db: Session, arena_id: str, q=None, role=None, status=None) -> list[dict]:
    rows = list_users(db, arena_id, q, role, status, scope={arena_id}, limit=500)
    # 'inactive' filtra conta; 'suspended' filtra vínculo. Em ambos, vínculo da arena sempre presente.
    return [member_row(r) for r in rows if r.get("membership")]
