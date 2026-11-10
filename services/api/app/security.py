import calendar
import base64
import hashlib
import hmac
import secrets
import struct
import time
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
    now = utcnow()
    payload = {**payload, "iat": calendar.timegm(now.utctimetuple()), "iatm": calendar.timegm(now.utctimetuple()) * 1000 + now.microsecond // 1000, "exp": now + timedelta(seconds=ttl_s)}
    return jwt.encode(payload, settings.secret_key, algorithm=_ALG)


def _decode(token: str, typ: str) -> dict | None:
    try:
        data = jwt.decode(token, settings.secret_key, algorithms=[_ALG])
    except jwt.PyJWTError:
        return None
    return data if data.get("typ") == typ else None


APPS = ("user", "arena", "platform")


def create_access_token(user_id: str, app: str, mfa: bool = False, scope: str = "full", extra: dict | None = None,
                        ttl_s: int | None = None) -> str:
    """Token por aplicação (`app`): um token do app do usuário não vale na API da arena nem do portal.
    scope='mfa' é um token curto que só permite concluir o MFA."""
    assert app in APPS
    ttl = ttl_s or (300 if scope != "full" else (3600 if app == "platform" else settings.access_token_ttl_s))
    return _encode({**(extra or {}), "typ": "access", "sub": user_id, "app": app, "mfa": mfa, "scope": scope}, ttl)


def decode_access_token(token: str) -> dict | None:
    return _decode(token, "access")


# --- TOTP (RFC 6238) sem dependências externas ---
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _totp_code(secret: str, step: int, digits: int = 6) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", step), "sha1").digest()
    off = mac[-1] & 0x0F
    num = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(num).zfill(digits)


def totp_now_step(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // 30)


def verify_totp(secret: str, code: str, last_step: int = 0, now: float | None = None) -> int | None:
    """Retorna o passo aceito (para impedir replay) ou None. Janela de ±1 passo."""
    code = (code or "").strip()
    if not (code.isdigit() and len(code) == 6):
        return None
    cur = totp_now_step(now)
    for step in (cur - 1, cur, cur + 1):
        if step > last_step and safe_eq(_totp_code(secret, step), code):
            return step
    return None


def totp_uri(secret: str, email: str) -> str:
    return f"otpauth://totp/ReplayAgora:{email}?secret={secret}&issuer=ReplayAgora"


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
