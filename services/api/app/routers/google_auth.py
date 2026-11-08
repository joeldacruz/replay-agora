"""Login com Google nos três aplicativos. Vínculo SOMENTE por (provider, sub). E-mail igual nunca vincula sozinho.
Google não concede perfis: apenas autentica quem já tem acesso nesse aplicativo. No Portal, o TOTP continua obrigatório."""
import hashlib
import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import audit, google_oidc as g, secretbox, userman
from ..db import get_db, utcnow
from ..deps import ARENA_STAFF, Auth, active_arena_ids, platform_auth
from ..models import IntegrationSetting, User, UserIdentity, OAuthFlow
from ..ratelimit import RateLimiter
from ..config import settings
from ..security import create_access_token, decode_access_token
from .auth import _me, mark_login

router = APIRouter(prefix="/api/auth/google", tags=["google"])
portal = APIRouter(prefix="/api/platform/integrations", tags=["portal-dev"])
log = logging.getLogger("replay.google")
limiter = RateLimiter(30, 60)
APPS = {"user": "/", "arena": "/arena/", "platform": "/portal/"}
FLOW_TTL = timedelta(minutes=10)
TICKET_TTL = timedelta(seconds=60)
GENERIC = {"google_nao_configurado": "Login com Google não está configurado.",
           "cancelado": "Login com Google cancelado.",
           "falha": "Não foi possível concluir o login com Google. Tente novamente.",
           "sem_acesso": "Esta conta Google não tem acesso a este aplicativo. Peça um convite ao gestor.",
           "conta_existente": "Já existe uma conta com este e-mail. Entre com e-mail e senha e vincule o Google na sua conta.",
           "inativo": "Conta ou vínculo inativo. Fale com o gestor."}


def _ip(request: Request) -> str:
    return request.client.host if request.client else "-"


def _back(app: str, **q) -> RedirectResponse:
    from urllib.parse import urlencode
    return RedirectResponse(f"{APPS[app]}#/auth/google?{urlencode(q)}", status_code=303)


def _new_flow(db: Session, app: str, intent: str, user_id: str | None = None) -> tuple[OAuthFlow, str]:
    verifier, challenge = g.pkce_pair()
    f = OAuthFlow(state=secrets.token_urlsafe(32), app=app, intent=intent, nonce=secrets.token_urlsafe(24), code_verifier=verifier,
                  user_id=user_id, expires_at=utcnow() + FLOW_TTL)
    db.add(f)
    db.commit()
    return f, challenge


@router.get("/status")
def status(db: Session = Depends(get_db)):
    return {"enabled": g.load_config(db).enabled}


@router.get("/start")
def start(request: Request, app: str = Query(pattern=r"^(user|arena|platform)$"), db: Session = Depends(get_db)):
    limiter.check(f"gs:{_ip(request)}")
    cfg = g.load_config(db)
    if not cfg.enabled:
        return _back(app, err="google_nao_configurado")
    f, ch = _new_flow(db, app, "login")
    return RedirectResponse(g.provider.auth_url(cfg, f.state, f.nonce, ch), status_code=303)


class LinkStart(BaseModel):
    app: str = Field(pattern=r"^(user|arena|platform)$")


@router.post("/link/start")
def link_start(body: LinkStart, request: Request, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Usuário JÁ autenticado inicia o vínculo. Retorna a URL do Google (o SPA navega até ela)."""
    limiter.check(f"gl:{_ip(request)}")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "não autenticado")
    claims = decode_access_token(authorization[7:].strip())
    if not claims or claims.get("scope") != "full" or claims.get("app") != body.app:
        raise HTTPException(401, "sessão inválida ou expirada")
    u = db.get(User, claims["sub"])
    if not u or not u.active:
        raise HTTPException(401, "sessão inválida ou expirada")
    if body.app == "platform" and not (u.global_role == "global_admin" and claims.get("mfa")):
        raise HTTPException(403, "requer global_admin com MFA")
    cfg = g.load_config(db)
    if not cfg.enabled:
        raise HTTPException(409, GENERIC["google_nao_configurado"])
    if db.query(UserIdentity).filter_by(user_id=u.id, provider="google").first():
        raise HTTPException(409, "esta conta já tem um Google vinculado")
    f, ch = _new_flow(db, body.app, "link", u.id)
    return {"url": g.provider.auth_url(cfg, f.state, f.nonce, ch, login_hint=u.email)}


def _eligible(db: Session, u: User, app: str) -> str | None:
    if not u.active:
        return "inativo"
    if app == "platform":
        return None if u.global_role == "global_admin" else "sem_acesso"
    if app == "arena":
        return None if active_arena_ids(db, u, ARENA_STAFF) else "sem_acesso"
    return None


def _resolve_login(db: Session, claims: dict, app: str) -> tuple[User | None, str | None]:
    sub, email = claims["sub"], claims["email"].strip().lower()
    ident = db.query(UserIdentity).filter_by(provider="google", subject=sub).first()
    if ident:
        u = db.get(User, ident.user_id)
        if u.auth_method not in ("google", "both"):
            return None, "sem_acesso"  # método Google não permitido para esta conta
        ident.last_login_at = utcnow()
        return u, None
    u = db.query(User).filter_by(email=email).first()
    if u:
        # NÃO vincula por e-mail igual. Só quando o administrador convidou explicitamente para Google (uso único).
        if u.google_link_allowed and u.auth_method in ("google", "both") and u.active:
            db.add(UserIdentity(user_id=u.id, provider="google", subject=sub, email=email, last_login_at=utcnow()))
            u.google_link_allowed = False
            audit.record(db, u.id, None, "userman.google_link", "user", u.id, {"email": email, "via": "convite"})
            return u, None
        return None, "conta_existente"
    if app == "user":  # jogador novo: cria conta (sem nenhum perfil administrativo)
        u = User(name=(claims.get("name") or email.split("@")[0])[:120], email=email, password_hash=userman.UNUSABLE, auth_method="google")
        db.add(u)
        db.flush()
        db.add(UserIdentity(user_id=u.id, provider="google", subject=sub, email=email, last_login_at=utcnow()))
        audit.record(db, u.id, None, "user.register_google", "user", u.id, {"email": email})
        return u, None
    return None, "sem_acesso"


@router.get("/callback")
def callback(request: Request, state: str = "", code: str = "", error: str = "", db: Session = Depends(get_db)):
    limiter.check(f"gc:{_ip(request)}")
    f = db.get(OAuthFlow, state[:64]) if state else None
    if not f or f.used_at is not None or f.expires_at < utcnow():
        return _back("user", err="falha")  # state desconhecido/reutilizado/expirado: nada é processado
    f.used_at = utcnow()  # state de uso único, mesmo em caso de erro
    db.commit()
    app = f.app
    if error or not code:
        return _back(app, err="cancelado")
    cfg = g.load_config(db)
    if not cfg.enabled:
        return _back(app, err="google_nao_configurado")
    try:
        idt = g.provider.exchange(cfg, code, f.code_verifier)
        claims = g.validate_id_token(idt, cfg, f.nonce, g.provider.signing_key(idt))
    except g.GoogleError as e:
        log.warning("login google recusado: %s", e)
        return _back(app, err="falha")
    sub, email = claims["sub"], claims["email"].strip().lower()
    if f.intent == "link":
        u = db.get(User, f.user_id)
        taken = db.query(UserIdentity).filter_by(provider="google", subject=sub).first()
        if not u or not u.active or taken or db.query(UserIdentity).filter_by(user_id=u.id, provider="google").first():
            return _back(app, err="falha")
        db.add(UserIdentity(user_id=u.id, provider="google", subject=sub, email=email, last_login_at=utcnow()))
        if u.auth_method == "password":
            u.auth_method = "both"
        audit.record(db, u.id, None, "userman.google_link", "user", u.id, {"email": email, "via": "usuario"})
        db.commit()
        return _back(app, linked="1")
    u, why = _resolve_login(db, claims, app)
    if u and not why:
        why = _eligible(db, u, app)
    if why:
        db.commit()
        log.info("login google negado: %s", why)
        return _back(app, err=why)
    ticket = secrets.token_urlsafe(32)
    f.ticket_hash, f.ticket_user_id, f.ticket_expires_at = hashlib.sha256(ticket.encode()).hexdigest(), u.id, utcnow() + TICKET_TTL
    db.commit()
    return _back(app, ticket=ticket)


class Exchange(BaseModel):
    ticket: str = Field(min_length=20, max_length=100)
    app: str = Field(pattern=r"^(user|arena|platform)$")


@router.post("/exchange")
def exchange(body: Exchange, request: Request, db: Session = Depends(get_db)):
    limiter.check(f"ge:{_ip(request)}")
    f = db.query(OAuthFlow).filter_by(ticket_hash=hashlib.sha256(body.ticket.encode()).hexdigest()).first()
    if (not f or f.app != body.app or f.ticket_used_at is not None or not f.ticket_expires_at or f.ticket_expires_at < utcnow()):
        raise HTTPException(401, "ticket inválido ou expirado")
    f.ticket_used_at = utcnow()
    u = db.get(User, f.ticket_user_id)
    if not u or not u.active or _eligible(db, u, f.app):
        db.commit()
        raise HTTPException(403, GENERIC["sem_acesso"])
    if f.app == "platform":  # Google NÃO substitui o MFA do Portal
        db.commit()
        return {"mfaStep": "verify" if u.mfa_enabled else "enroll", "mfaToken": create_access_token(u.id, "platform", scope="mfa")}
    mark_login(db, u)
    db.commit()
    return {"token": create_access_token(u.id, f.app), "user": _me(db, u)}


# ---------- vínculo do usuário logado ----------
def _self(authorization: str | None, db: Session) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "não autenticado")
    claims = decode_access_token(authorization[7:].strip())
    if not claims or claims.get("scope") != "full":
        raise HTTPException(401, "sessão inválida ou expirada")
    u = db.get(User, claims["sub"])
    if not u or not u.active:
        raise HTTPException(401, "sessão inválida ou expirada")
    return u


@router.get("/identity")
def identity(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    u = _self(authorization, db)
    i = db.query(UserIdentity).filter_by(user_id=u.id, provider="google").first()
    return {"linked": bool(i), "email": i.email if i else None, "passwordSet": u.password_hash != userman.UNUSABLE,
            "authMethod": u.auth_method, "enabled": g.load_config(db).enabled}


@router.delete("/link")
def unlink(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    u = _self(authorization, db)
    i = db.query(UserIdentity).filter_by(user_id=u.id, provider="google").first()
    if not i:
        raise HTTPException(404, "nenhum Google vinculado")
    if u.password_hash == userman.UNUSABLE:
        raise HTTPException(409, "defina uma senha antes de desvincular o Google (use 'Esqueci minha senha' / peça redefinição ao gestor)")
    db.delete(i)
    u.auth_method = "password"
    u.google_link_allowed = False
    audit.record(db, u.id, None, "userman.google_unlink", "user", u.id, {"email": i.email})
    db.commit()
    return {"linked": False}


# ---------- configuração protegida (Portal) ----------
class GoogleCfg(BaseModel):
    clientId: str = Field(min_length=10, max_length=200)
    clientSecret: str | None = Field(None, min_length=8, max_length=200)  # omitido = mantém o atual


def _put(db: Session, key: str, val: str, by: str) -> None:
    row = db.get(IntegrationSetting, key)
    if row:
        row.value_enc, row.updated_by, row.updated_at = secretbox.seal(val), by, utcnow()
    else:
        db.add(IntegrationSetting(key=key, value_enc=secretbox.seal(val), updated_by=by, updated_at=utcnow()))


@portal.get("/google")
def get_cfg(a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    c = g.load_config(db)
    return {"clientId": c.client_id, "secretSet": bool(c.client_secret), "source": c.source, "enabled": c.enabled,
            "redirectUri": g.redirect_uri(), "publicBaseUrl": settings.public_base_url}


@portal.put("/google")
def put_cfg(body: GoogleCfg, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    cur = g.load_config(db)
    if not body.clientSecret and not cur.client_secret:
        raise HTTPException(422, "informe o client secret")
    _put(db, g.KEY_ID, body.clientId.strip(), a.user.id)
    _put(db, g.KEY_SECRET, body.clientSecret or cur.client_secret, a.user.id)
    audit.record(db, a.user.id, None, "platform.google_config", "integration", "google",
                 {"clientId": body.clientId.strip(), "secretChanged": bool(body.clientSecret)})
    db.commit()
    return get_cfg(a, db)


@portal.delete("/google", status_code=204)
def del_cfg(a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    for k in (g.KEY_ID, g.KEY_SECRET):
        row = db.get(IntegrationSetting, k)
        if row:
            db.delete(row)
    audit.record(db, a.user.id, None, "platform.google_config_remove", "integration", "google")
    db.commit()
