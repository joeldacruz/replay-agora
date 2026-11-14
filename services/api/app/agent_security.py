"""Autenticação do agente local: credenciais rotativas, requisições assinadas (HMAC + nonce) e mTLS (preparação).

Camadas, da mais fraca à mais forte:
 1. Bearer (hash SHA-256 no banco, comparação por índice único). Agentes legados: sem expiração e sem assinatura.
 2. Assinatura HMAC-SHA256 da requisição (método, caminho+query, timestamp, nonce, hash do corpo) com chave própria
    que NUNCA trafega após a emissão. Vazar o Bearer (log, proxy) não basta para forjar requisições.
 3. Nonce de uso único + janela de tempo => requisição capturada não pode ser reapresentada.
 4. mTLS: o proxy reverso valida o certificado de cliente e informa o SHA-256 em cabeçalho; a API compara com o
    fingerprint cadastrado. Desligado por padrão (AGENT_MTLS_MODE=off).
Nenhum segredo é registrado em log/auditoria (ver logsafe.py e audit.py).
"""
import hashlib
import hmac
import secrets
import time
from datetime import timedelta

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal, get_db, utcnow
from .models import AgentNonce, Arena, ArenaAgent
from .security import hash_agent_token, new_agent_token

ROTATE_SUFFIX = "/credentials/rotate"
_UNAUTH = "credencial do agente inválida"


def new_sig_key() -> str:
    return secrets.token_hex(32)


def canonical(method: str, path: str, query: str, ts: str, nonce: str, body: bytes) -> bytes:
    q = f"?{query}" if query else ""
    return "\n".join(["RA1", method.upper(), path + q, ts, nonce, hashlib.sha256(body).hexdigest()]).encode()


def compute_sig(key: str, method: str, path: str, query: str, ts: str, nonce: str, body: bytes) -> str:
    return hmac.new(key.encode(), canonical(method, path, query, ts, nonce, body), hashlib.sha256).hexdigest()


def issue_credentials(ag: ArenaAgent, now=None) -> tuple[str, str]:
    """Emite token + chave de assinatura novos (mostrados UMA vez) e define a expiração."""
    now = now or utcnow()
    token, key = new_agent_token(), new_sig_key()
    ag.token_hash, ag.sig_key = hash_agent_token(token), key
    ag.token_rotated_at = now
    ag.token_expires_at = now + timedelta(days=settings.agent_token_ttl_days) if settings.agent_token_ttl_days > 0 else None
    return token, key


def authenticate(db: Session, authorization: str | None, allow_expired: bool = False) -> tuple[ArenaAgent, bool]:
    """Resolve o token. Retorna (agente, usou_credencial_anterior). Mensagens genéricas: não revela o motivo."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "credencial do agente ausente")
    h = hash_agent_token(authorization[7:].strip())
    now = utcnow()
    ag = db.query(ArenaAgent).filter(ArenaAgent.token_hash == h).first()
    via_prev = False
    if not ag:
        ag = db.query(ArenaAgent).filter(ArenaAgent.prev_token_hash == h, ArenaAgent.prev_valid_until > now).first()
        via_prev = ag is not None
    if not ag or not ag.active:
        raise HTTPException(401, _UNAUTH)
    exp = ag.token_expires_at
    if exp and now > exp and not (allow_expired and now <= exp + timedelta(days=settings.agent_expired_rotate_days)):
        raise HTTPException(401, "credencial expirada: rotacione ou emita uma nova no App da Arena")
    arena = db.get(Arena, ag.arena_id)
    if not arena or arena.status != "active":
        raise HTTPException(403, "arena suspensa")
    return ag, via_prev


def _check_mtls(request: Request, ag: ArenaAgent) -> None:
    mode = settings.agent_mtls_mode
    if mode == "off":
        return
    presented = (request.headers.get(settings.agent_mtls_header) or "").replace(":", "").lower()
    if ag.cert_fingerprint:
        if not presented or not hmac.compare_digest(presented, ag.cert_fingerprint.lower()):
            raise HTTPException(401, _UNAUTH)
    elif mode == "required":
        raise HTTPException(401, _UNAUTH)


def _check_signature(request: Request, body: bytes, ag: ArenaAgent, via_prev: bool) -> None:
    key = ag.prev_sig_key if via_prev else ag.sig_key
    sig = request.headers.get("x-agent-sig")
    if not key:
        if settings.agent_require_signature:
            raise HTTPException(401, "requisição assinada obrigatória: rotacione a credencial para receber chave de assinatura")
        return  # agente legado
    ts, nonce = request.headers.get("x-agent-ts", ""), request.headers.get("x-agent-nonce", "")
    if not (sig and ts.isdigit() and 16 <= len(nonce) <= 64 and nonce.replace("-", "").replace("_", "").isalnum()):
        raise HTTPException(401, "assinatura ausente ou malformada")
    if abs(time.time() - int(ts)) > settings.agent_sig_skew_s:
        raise HTTPException(401, "requisição fora da janela de tempo")
    expect = compute_sig(key, request.method, request.url.path, request.url.query, ts, nonce, body)
    if not hmac.compare_digest(expect, sig):
        raise HTTPException(401, "assinatura inválida")
    _consume_nonce(ag.id, nonce)


def _consume_nonce(agent_id: str, nonce: str) -> None:
    """Commit em sessão própria: o nonce fica queimado mesmo que a requisição falhe depois."""
    with SessionLocal() as s:
        s.query(AgentNonce).filter(AgentNonce.ts < utcnow() - timedelta(seconds=settings.agent_sig_skew_s * 3)).delete()
        s.add(AgentNonce(agent_id=agent_id, nonce=nonce, ts=utcnow()))
        try:
            s.commit()
        except IntegrityError:
            raise HTTPException(401, "requisição repetida (nonce já usado)") from None


async def _raw_body(request: Request) -> bytes:
    return await request.body()


def agent_auth_v1(request: Request, body: bytes = Depends(_raw_body), authorization: str | None = Header(default=None),
                  db: Session = Depends(get_db)) -> ArenaAgent:
    """Dependência dos endpoints /api/agent/v1: Bearer + mTLS + assinatura/nonce."""
    ag, via_prev = authenticate(db, authorization, allow_expired=request.url.path.endswith(ROTATE_SUFFIX))
    _check_mtls(request, ag)
    _check_signature(request, body, ag, via_prev)
    request.state.via_prev = via_prev
    ag.last_used_at = utcnow()
    return ag


def rotate(ag: ArenaAgent, via_prev: bool) -> tuple[str, str]:
    """Rotação iniciada pelo agente. A credencial vigente vira 'anterior' por uma janela curta; se a chamada veio da
    própria credencial anterior (resposta perdida), mantém a janela original e apenas reemite o par novo."""
    now = utcnow()
    if not via_prev:
        ag.prev_token_hash, ag.prev_sig_key = ag.token_hash, ag.sig_key
        ag.prev_valid_until = now + timedelta(seconds=settings.agent_rotate_grace_s)
    return issue_credentials(ag, now)


def force_rotate(ag: ArenaAgent) -> tuple[str, str]:
    """Rotação pelo gestor (suspeita de vazamento): a credencial antiga deixa de valer IMEDIATAMENTE."""
    ag.prev_token_hash = ag.prev_sig_key = ag.prev_valid_until = None
    return issue_credentials(ag)


def rotation_hint(ag: ArenaAgent) -> dict:
    exp = ag.token_expires_at
    soon = bool(exp and exp - utcnow() <= timedelta(days=settings.agent_rotate_before_days))
    return {"credentialExpiresAt": exp.isoformat() + "Z" if exp else None, "rotateRecommended": soon or not ag.sig_key}


