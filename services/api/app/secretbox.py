"""Cifra de segredos em repouso (senhas de câmera). Fernet (AES-128-CBC + HMAC-SHA256) com chave derivada.

A chave vem de CREDENTIAL_KEY (recomendado em produção, separada da SECRET_KEY) ou, na falta dela, da SECRET_KEY.
O valor decifrado só é usado no instante da ENTREGA ao agente autenticado da própria arena e nunca é registrado em log,
auditoria, resposta de consulta ou mensagem de erro.
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


def _fernet() -> Fernet:
    raw = os.environ.get("CREDENTIAL_KEY") or settings.secret_key
    key = hashlib.sha256(b"replay-agora/secretbox/v1|" + raw.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def seal(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def unseal(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None
