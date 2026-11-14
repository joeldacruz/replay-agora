"""Assinatura de requisição (HMAC-SHA256) — mesmo esquema da API (RA1). Chave nunca é enviada nem registrada em log."""
import hashlib
import hmac
import secrets
import time


def canonical(method: str, path: str, query: str, ts: str, nonce: str, body: bytes) -> bytes:
    q = f"?{query}" if query else ""
    return "\n".join(["RA1", method.upper(), path + q, ts, nonce, hashlib.sha256(body).hexdigest()]).encode()


def sign_headers(key: str, method: str, path: str, query: str, body: bytes, now: float | None = None, nonce: str | None = None) -> dict:
    ts, nonce = str(int(now if now is not None else time.time())), nonce or secrets.token_urlsafe(18)
    sig = hmac.new(key.encode(), canonical(method, path, query, ts, nonce, body), hashlib.sha256).hexdigest()
    return {"X-Agent-Ts": ts, "X-Agent-Nonce": nonce, "X-Agent-Sig": sig}
