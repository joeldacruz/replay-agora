"""Autorização centralizada. Todo endpoint protegido passa por uma destas dependências."""
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .db import get_db, utcnow
from .models import Arena, Membership, User
from .security import decode_access_token

ARENA_STAFF = ("arena_admin", "arena_operator")


@dataclass
class Auth:
    user: User
    app: str
    mfa: bool


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "não autenticado")
    return authorization[7:].strip()


def _auth(app: str):
    def dep(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> Auth:
        claims = decode_access_token(_bearer(authorization))
        if not claims or claims.get("scope") != "full":
            raise HTTPException(401, "sessão inválida ou expirada")
        if claims.get("app") != app:
            raise HTTPException(403, "credencial não pertence a este aplicativo")
        user = db.get(User, claims["sub"])
        if not user or not user.active:
            raise HTTPException(401, "sessão inválida ou expirada")
        return Auth(user, app, bool(claims.get("mfa")))
    return dep


user_auth = _auth("user")
arena_auth = _auth("arena")
_platform_base = _auth("platform")


def platform_auth(a: Auth = Depends(_platform_base)) -> Auth:
    if a.user.global_role != "global_admin" or not a.user.mfa_enabled or not a.mfa:
        raise HTTPException(403, "requer global_admin com MFA")
    return a


def active_arena_ids(db: Session, user: User, roles: tuple[str, ...] | None = None) -> list[str]:
    q = db.query(Membership.arena_id).join(Arena, Arena.id == Membership.arena_id).filter(
        Membership.user_id == user.id, Membership.status == "active", Arena.status == "active")
    if roles:
        q = q.filter(Membership.role.in_(roles))
    return [r[0] for r in q.all()]


def arena_context(db: Session, user: User, arena_id: str, roles: tuple[str, ...]) -> tuple[Arena, Membership]:
    """Contexto explícito de arena. Não-membro => 404 (não revela existência); papel insuficiente => 403."""
    m = db.query(Membership).filter_by(user_id=user.id, arena_id=arena_id, status="active").first()
    arena = db.get(Arena, arena_id) if m else None
    if not m or not arena:
        raise HTTPException(404, "arena não encontrada")
    if m.role not in roles:
        raise HTTPException(403, "permissão insuficiente nesta arena")
    if arena.status != "active":
        raise HTTPException(403, "arena suspensa")
    return arena, m


class ArenaGate:
    """Dependência para rotas /api/arena/{arena_id}/... com papéis permitidos."""

    def __init__(self, *roles: str):
        self.roles = roles or ARENA_STAFF

    def __call__(self, arena_id: str, a: Auth = Depends(arena_auth), db: Session = Depends(get_db)):
        arena, m = arena_context(db, a.user, arena_id, self.roles)
        return ArenaCtx(a.user, arena, m)


@dataclass
class ArenaCtx:
    user: User
    arena: Arena
    membership: Membership


def video_visible_to(db: Session, user: User, video) -> bool:
    """Regra de acesso do atleta: concessão válida (código/QR) OU publicado na arena em que é membro ativo."""
    from .models import VideoAccess

    if video.deleted_at is not None:
        return False
    arena = db.get(Arena, video.arena_id)
    if not arena or arena.status != "active":
        return False
    g = db.query(VideoAccess).filter_by(user_id=user.id, video_id=video.id).first()
    if g and (g.expires_at is None or g.expires_at > utcnow()):
        return True
    if video.visibility == "arena":
        return db.query(Membership).filter_by(user_id=user.id, arena_id=video.arena_id, status="active").first() is not None
    return False


def can_view_video(db: Session, user: User, video) -> bool:
    """Equipe da própria arena (admin/operador) ou atleta com acesso (concessão/publicação)."""
    if video.deleted_at is not None:
        return False
    arena = db.get(Arena, video.arena_id)
    if not arena or arena.status != "active":
        return False
    staff = db.query(Membership).filter(
        Membership.user_id == user.id, Membership.arena_id == video.arena_id, Membership.status == "active",
        Membership.role.in_(ARENA_STAFF)).first()
    return staff is not None or video_visible_to(db, user, video)
