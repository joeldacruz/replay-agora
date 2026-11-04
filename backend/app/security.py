import hashlib
import hmac
import secrets
from datetime import timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

from .config import settings
from .db import utcnow

_ph = PasswordHasher()
_ALG = "HS256"


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except (VerifyMismatchError, InvalidHashError):
        return False


# Hash fictício para igualar o tempo de resposta quando o e-mail não existe.
DUMMY_HASH = _ph.hash("dummy-password-for-timing")


def _encode(payload: dict, ttl_s: int) -> str:
    payload = {**payload, "exp": utcnow() + timedelta(seconds=ttl_s)}
    return jwt.encode(payload, settings.secret_key, algorithm=_ALG)


def _decode(token: str, typ: str) -> dict | None:
    try:
        data = jwt.decode(token, settings.secret_key, algorithms=[_ALG])
    except jwt.PyJWTError:
        return None
    return data if data.get("typ") == typ else None


def create_access_token(user_id: str) -> str:
    return _encode({"typ": "access", "sub": user_id}, settings.access_token_ttl_s)


def decode_access_token(token: str) -> str | None:
    d = _decode(token, "access")
    return d["sub"] if d else None


def create_media_token(kind: str, resource_id: str, user_id: str, disposition: str = "inline") -> str:
    """URL assinada de curta duração (kind: video|thumb|export|logo|music)."""
    return _encode(
        {"typ": "media", "k": kind, "rid": resource_id, "uid": user_id, "d": disposition},
        settings.signed_url_ttl_s,
    )


def decode_media_token(token: str) -> dict | None:
    return _decode(token, "media")


def new_agent_token() -> str:
    return "rag_" + secrets.token_urlsafe(32)


def hash_agent_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def safe_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
