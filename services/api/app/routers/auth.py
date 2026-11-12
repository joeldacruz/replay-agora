"""Login separado por aplicação: user | arena | platform (global_admin + MFA TOTP)."""
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import audit, userman
from ..config import settings
from ..db import get_db, utcnow
from ..deps import ARENA_STAFF, Auth, active_arena_ids, self_auth, user_auth, arena_auth, _platform_base
from ..models import Membership, User
from ..ratelimit import RateLimiter
from ..security import (DUMMY_HASH, create_access_token, decode_access_token, hash_password, new_totp_secret,
                        totp_uri, verify_password, verify_totp)

router = APIRouter(prefix="/api/auth", tags=["auth"])
limiter = RateLimiter(settings.login_max_attempts, settings.login_window_s)


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=200)


class Register(Credentials):
    name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=10, max_length=200)


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _norm(email: str) -> str:
    e = email.strip().lower()
    if not _EMAIL.match(e):
        raise HTTPException(422, "e-mail inválido")
    return e


def _authenticate(db: Session, request: Request, c: Credentials) -> User:
    email = _norm(c.email)
    limiter.check(f"{request.client.host if request.client else '-'}:{email}")
    u = db.query(User).filter_by(email=email).first()
    pw_allowed = bool(u) and u.auth_method in ("password", "both") and u.password_hash != userman.UNUSABLE
    ok = verify_password(c.password, u.password_hash if pw_allowed else DUMMY_HASH)
    if not u or not ok or not pw_allowed or not u.active:
        raise HTTPException(401, "e-mail ou senha incorretos")
    return u


def mark_login(db: Session, u: User) -> None:
    u.last_login_at = utcnow()


def _me(db: Session, u: User) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "globalRole": u.global_role}


@router.post("/user/register", status_code=201)
def register(body: Register, request: Request, db: Session = Depends(get_db)):
    limiter.check(f"reg:{request.client.host if request.client else '-'}")
    email = _norm(body.email)
    if db.query(User).filter_by(email=email).first():
        raise HTTPException(409, "e-mail já cadastrado")
    u = User(name=body.name.strip(), email=email, password_hash=hash_password(body.password))
    db.add(u)
    db.flush()
    audit.record(db, u.id, None, "user.register", "user", u.id)
    db.commit()
    return {"token": create_access_token(u.id, "user"), "user": _me(db, u)}


@router.post("/user/login")
def user_login(body: Credentials, request: Request, db: Session = Depends(get_db)):
    u = _authenticate(db, request, body)
    mark_login(db, u)
    db.commit()
    return {"token": create_access_token(u.id, "user"), "user": _me(db, u)}


@router.post("/arena/login")
def arena_login(body: Credentials, request: Request, db: Session = Depends(get_db)):
    u = _authenticate(db, request, body)
    if not active_arena_ids(db, u, ARENA_STAFF):
        # mesma mensagem genérica: não revela se a conta existe só como atleta
        raise HTTPException(403, "esta conta não gerencia nenhuma arena ativa")
    mark_login(db, u)
    db.commit()
    return {"token": create_access_token(u.id, "arena"), "user": _me(db, u)}


@router.post("/platform/login")
def platform_login(body: Credentials, request: Request, db: Session = Depends(get_db)):
    u = _authenticate(db, request, body)
    if u.global_role != "global_admin":
        raise HTTPException(403, "acesso restrito à equipe da plataforma")
    step = "verify" if u.mfa_enabled else "enroll"
    return {"mfaStep": step, "mfaToken": create_access_token(u.id, "platform", scope="mfa")}


class Totp(BaseModel):
    code: str = Field(min_length=6, max_length=6)


def _pending(authorization: str | None, db: Session) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "não autenticado")
    claims = decode_access_token(authorization[7:].strip())
    if not claims or claims.get("scope") != "mfa" or claims.get("app") != "platform":
        raise HTTPException(401, "etapa de MFA inválida ou expirada")
    u = db.get(User, claims["sub"])
    if not u or not u.active or u.global_role != "global_admin":
        raise HTTPException(401, "etapa de MFA inválida ou expirada")
    return u


@router.post("/platform/mfa/setup")
def mfa_setup(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    u = _pending(authorization, db)
    if u.mfa_enabled:
        raise HTTPException(409, "MFA já configurado")
    u.mfa_secret = new_totp_secret()
    db.commit()
    return {"secret": u.mfa_secret, "otpauthUri": totp_uri(u.mfa_secret, u.email)}


@router.post("/platform/mfa/verify")
def mfa_verify(body: Totp, request: Request, authorization: str | None = Header(default=None),
               db: Session = Depends(get_db)):
    u = _pending(authorization, db)
    limiter.check(f"mfa:{u.id}")
    if not u.mfa_secret:
        raise HTTPException(409, "MFA não iniciado")
    step = verify_totp(u.mfa_secret, body.code, u.mfa_last_step)
    if step is None:
        raise HTTPException(401, "código inválido")
    first = not u.mfa_enabled
    u.mfa_last_step = step
    u.mfa_enabled = True
    audit.record(db, u.id, None, "platform.mfa_enabled" if first else "platform.login", "user", u.id)
    mark_login(db, u)
    db.commit()
    return {"token": create_access_token(u.id, "platform", mfa=True), "user": _me(db, u)}


@router.get("/user/me")
def user_me(a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    return _me_full(db, a)


@router.get("/arena/me")
def arena_me(a: Auth = Depends(arena_auth), db: Session = Depends(get_db)):
    return _me_full(db, a, staff_only=True)


@router.get("/platform/me")
def platform_me(a: Auth = Depends(_platform_base), db: Session = Depends(get_db)):
    return _me(db, a.user) | {"mfa": a.mfa}


def _me_full(db: Session, a: Auth, staff_only: bool = False) -> dict:
    from ..models import Arena

    if a.arena_id:  # sessão de gestão emitida pelo Portal ao global_admin: uma única arena, papel 'global_admin', sem Membership
        ar = db.get(Arena, a.arena_id)
        ms = [{"arenaId": ar.id, "arenaSlug": ar.slug, "arenaName": ar.name, "role": "global_admin", "brandColor": ar.brand_color}] if ar and ar.status == "active" else []
        return _me(db, a.user) | {"memberships": ms}

    q = (db.query(Membership, Arena).join(Arena, Arena.id == Membership.arena_id)
         .filter(Membership.user_id == a.user.id, Membership.status == "active", Arena.status == "active"))
    if staff_only:
        q = q.filter(Membership.role.in_(ARENA_STAFF))
    return _me(db, a.user) | {"memberships": [
        {"arenaId": ar.id, "arenaSlug": ar.slug, "arenaName": ar.name, "role": m.role, "brandColor": ar.brand_color}
        for m, ar in q.all()]}


# ---------- convite / redefinição de senha (token de uso único enviado por e-mail) ----------
class SetPassword(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=10, max_length=200)


@router.get("/password/check")
def password_check(token: str, request: Request, db: Session = Depends(get_db)):
    limiter.check(f"pwc:{request.client.host if request.client else '-'}")
    t, u = userman.consume_token(db, token)
    return {"kind": t.kind, "name": u.name, "email": u.email, "authMethod": u.auth_method}


@router.post("/password/set")
def password_set(body: SetPassword, request: Request, db: Session = Depends(get_db)):
    limiter.check(f"pws:{request.client.host if request.client else '-'}")
    t, u = userman.consume_token(db, body.token)
    if u.auth_method == "google":
        u.auth_method = "both"  # definir senha por convite/reset habilita também e-mail+senha
    u.password_hash = hash_password(body.password)
    u.password_set_at = utcnow()
    u.tokens_valid_after = utcnow()  # revoga sessões anteriores
    t.used_at = utcnow()
    audit.record(db, u.id, None, f"userman.password_{t.kind}", "user", u.id, {"email": u.email})
    db.commit()
    return {"ok": True}


# ---------- Portal -> Gestão: troca de ticket de uso único (60 s) por sessão da arena, sem token em URL ----------
class PortalExchange(BaseModel):
    ticket: str = Field(min_length=20, max_length=100)


@router.post("/arena/portal-exchange")
def arena_portal_exchange(body: PortalExchange, request: Request, db: Session = Depends(get_db)):
    import hashlib
    from ..models import Arena, OAuthFlow
    limiter.check(f"pe:{request.client.host if request.client else '-'}")
    f = db.query(OAuthFlow).filter_by(ticket_hash=hashlib.sha256(body.ticket.encode()).hexdigest(), intent="portal").first()
    if not f or f.ticket_used_at is not None or not f.ticket_expires_at or f.ticket_expires_at < utcnow():
        raise HTTPException(401, "ticket inválido ou expirado")
    f.ticket_used_at = utcnow()           # uso único, mesmo em falha
    db.commit()
    u, ar = db.get(User, f.ticket_user_id), db.get(Arena, f.nonce)
    if not u or not u.active or u.global_role != "global_admin" or not u.mfa_enabled or not ar or ar.status != "active":
        raise HTTPException(403, "acesso global indisponível")
    audit.record(db, u.id, ar.id, "platform.gestao_session", "arena", ar.id, {})
    db.commit()
    tok = create_access_token(u.id, "arena", mfa=True, extra={"gadm": True, "aid": ar.id}, ttl_s=3600)
    return {"token": tok, "arenaId": ar.id}


# ---------- conta do próprio usuário (qualquer app): dados pessoais e senha ----------
def _account(db: Session, u: User) -> dict:
    from ..models import UserIdentity
    return {"id": u.id, "name": u.name, "email": u.email, "phone": u.phone, "authMethod": u.auth_method,
            "passwordSet": u.password_hash not in (userman.UNUSABLE, ""),
            "googleLinked": db.query(UserIdentity).filter_by(user_id=u.id, provider="google").first() is not None}


@router.get("/account")
def account_get(a: Auth = Depends(self_auth), db: Session = Depends(get_db)):
    return _account(db, a.user)


class AccountPatch(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    phone: str | None = Field(None, max_length=30)


@router.patch("/account")
def account_patch(body: AccountPatch, a: Auth = Depends(self_auth), db: Session = Depends(get_db)):
    """Só nome e telefone. E-mail, perfil, vínculos e situação NÃO são editáveis pelo próprio usuário (sem autoelevação)."""
    ch = body.model_dump(exclude_unset=True)
    if "name" in ch:
        a.user.name = ch["name"].strip()
    if "phone" in ch:
        a.user.phone = userman.norm_phone(ch["phone"])
    audit.record(db, a.user.id, None, "user.self_update", "user", a.user.id, {k: ("***" if k == "phone" else v) for k, v in ch.items()})
    db.commit()
    return _account(db, a.user)


class PasswordChange(BaseModel):
    current: str = Field(min_length=1, max_length=200)
    new: str = Field(min_length=10, max_length=200)


@router.post("/account/password")
def account_password(body: PasswordChange, request: Request, a: Auth = Depends(self_auth), db: Session = Depends(get_db)):
    """Troca de senha com a senha atual. Encerra TODAS as outras sessões e devolve um token novo para a sessão corrente."""
    u = a.user
    limiter.check(f"pwchg:{u.id}")
    if u.password_hash in (userman.UNUSABLE, ""):
        raise HTTPException(409, "esta conta ainda não tem senha: peça um link de definição de senha")
    if not verify_password(body.current, u.password_hash):
        raise HTTPException(403, "senha atual incorreta")
    if body.new == body.current:
        raise HTTPException(422, "a nova senha deve ser diferente da atual")
    u.password_hash = hash_password(body.new)
    u.password_set_at = utcnow()
    u.tokens_valid_after = utcnow()
    audit.record(db, u.id, None, "user.self_password", "user", u.id)
    db.commit()
    import time
    time.sleep(0.002)          # garante iatm posterior ao corte de revogação
    return {"token": create_access_token(u.id, a.app, mfa=a.mfa)}
