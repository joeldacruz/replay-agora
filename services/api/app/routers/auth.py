"""Login separado por aplicação: user | arena | platform (global_admin + MFA TOTP)."""
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import audit, userman
from ..config import settings
from ..db import get_db, utcnow
from ..deps import ARENA_STAFF, Auth, active_arena_ids, user_auth, arena_auth, _platform_base
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
