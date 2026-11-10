"""Autorização centralizada. Todo endpoint protegido passa por uma destas dependências."""
import calendar
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db, utcnow
from .models import Arena, Membership, User
from .security import decode_access_token

ARENA_STAFF = ("arena_admin", "arena_operator")

# Permissões POR OPERAÇÃO (o papel sozinho não basta). global_admin (sessão de plataforma + MFA) possui todas.
# Operações que só o Portal/administrador global executa não aparecem em nenhum papel de arena.
OPERATOR_PERMS = frozenset({"arena.view", "video.view", "video.play", "sponsor.view", "music.view", "preview", "diagnostics.view", "qr.view"})
ADMIN_PERMS = OPERATOR_PERMS | {"audio.manage", "music.manage", "sponsor.manage", "arena.info.edit", "members.manage"}
GLOBAL_ONLY_PERMS = frozenset({"court.manage", "agent.manage", "policy.manage", "video.moderate", "video.delete", "capture.test", "audit.view"})
ROLE_PERMS = {"arena_operator": OPERATOR_PERMS, "arena_admin": ADMIN_PERMS}


@dataclass
class Auth:
    user: User
    app: str
    mfa: bool
    arena_id: str | None = None   # token de arena emitido ao global_admin pelo Portal: vale para UMA arena


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
        if user.tokens_valid_after and int(claims.get("iatm", 0)) < (calendar.timegm(user.tokens_valid_after.utctimetuple()) * 1000 + user.tokens_valid_after.microsecond // 1000):
            raise HTTPException(401, "sessão revogada: entre novamente")      # troca/redefinição de senha revoga sessões anteriores
        aid = None
        if claims.get("gadm"):
            # Acesso global emitido pelo Portal (troca de ticket). Reavaliado a CADA requisição: perde efeito se a conta
            # deixar de ser global_admin ou de ter MFA, e nunca vale fora da arena para a qual foi emitido.
            if app != "arena" or user.global_role != "global_admin" or not user.mfa_enabled or not claims.get("aid"):
                raise HTTPException(401, "sessão inválida ou expirada")
            aid = claims["aid"]
        return Auth(user, app, bool(claims.get("mfa")), aid)
    return dep


user_auth = _auth("user")
arena_auth = _auth("arena")
_platform_base = _auth("platform")


def platform_auth(a: Auth = Depends(_platform_base)) -> Auth:
    if a.user.global_role != "global_admin" or not a.user.mfa_enabled or not a.mfa:
        raise HTTPException(403, "requer global_admin com MFA")
    return a


_arena_dep = _auth("arena")


def arena_or_platform_auth(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> Auth:
    """Rotas /api/arena/{id}: aceita (a) token do app 'arena' ou (b) sessão COMPLETA de plataforma (global_admin + MFA).
    Token do jogador => 403; token preliminar de MFA => 401; plataforma sem MFA => 403."""
    claims = decode_access_token(_bearer(authorization))
    if claims and claims.get("scope") == "full" and claims.get("app") == "platform":
        return platform_auth(_platform_base(authorization, db))
    return _arena_dep(authorization, db)


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

    def __init__(self, *roles: str, perm: str | None = None):
        # perm: autorização por operação (preferida). roles: forma legada, só por papel.
        self.perm = perm
        self.roles = tuple(r for r, ps in ROLE_PERMS.items() if perm in ps) if perm else (roles or ARENA_STAFF)

    def __call__(self, arena_id: str, request: Request, a: Auth = Depends(arena_or_platform_auth), db: Session = Depends(get_db)):
        if a.app == "platform" or a.arena_id:
            # Administrador global: contexto da arena do CAMINHO, sem Membership (nenhum vínculo artificial é criado).
            # Token emitido pelo Portal só vale para a arena para a qual foi emitido (sem acesso lateral).
            if a.arena_id and a.arena_id != arena_id:
                raise HTTPException(403, "sessão de gestão emitida para outra arena")
            arena = db.get(Arena, arena_id)
            if not arena:
                raise HTTPException(404, "arena não encontrada")
            if arena.status != "active":
                raise HTTPException(403, "arena suspensa")
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                from . import audit
                audit.record(db, a.user.id, arena.id, "platform.arena_act", "arena", arena.id,
                             {"method": request.method, "path": request.url.path})
            return ArenaCtx(a.user, arena, None, True)
        arena, m = arena_context(db, a.user, arena_id, self.roles)
        return ArenaCtx(a.user, arena, m)


@dataclass
class ArenaCtx:
    user: User
    arena: Arena
    membership: Membership | None = None
    is_global: bool = False

    @property
    def role(self) -> str:
        return "global_admin" if self.is_global else self.membership.role


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
