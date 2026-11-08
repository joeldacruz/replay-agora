"""Login com Google (OIDC / OAuth2 Authorization Code + PKCE). Validação do id_token feita aqui (iss, aud, exp, nonce, assinatura RS256).

O cliente HTTP é isolado em `GoogleProvider` para que os testes substituam rede/JWKS por uma chave RSA falsa — nenhuma credencial
real é usada no CI. Segredo do cliente: variável/arquivo (GOOGLE_CLIENT_SECRET[_FILE]) ou cadastrado no Portal (cifrado em repouso)."""
import base64
import hashlib
import secrets
import urllib.parse
from dataclasses import dataclass

import jwt
from sqlalchemy.orm import Session

from . import secretbox
from .config import settings
from .models import IntegrationSetting

ISSUERS = ("https://accounts.google.com", "accounts.google.com")
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
KEY_ID, KEY_SECRET = "google.client_id", "google.client_secret"


def _ep(name: str, default: str) -> str:
    b = settings.google_test_base
    return f"{b}/{name}" if b else default


class GoogleError(Exception):
    """Falha de validação/troca. A mensagem é genérica para o usuário; o detalhe vai só para log."""


@dataclass
class Config:
    client_id: str
    client_secret: str
    source: str  # 'portal' | 'ambiente' | ''

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)


def load_config(db: Session) -> Config:
    rows = {r.key: r for r in db.query(IntegrationSetting).filter(IntegrationSetting.key.in_([KEY_ID, KEY_SECRET]))}
    cid = secretbox.unseal(rows[KEY_ID].value_enc) if KEY_ID in rows else None
    sec = secretbox.unseal(rows[KEY_SECRET].value_enc) if KEY_SECRET in rows else None
    if cid and sec:
        return Config(cid, sec, "portal")
    if settings.google_client_id and settings.google_client_secret:
        return Config(settings.google_client_id, settings.google_client_secret, "ambiente")
    return Config("", "", "")


def redirect_uri() -> str:
    return f"{settings.public_base_url}/api/auth/google/callback"


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class GoogleProvider:
    def auth_url(self, cfg: Config, state: str, nonce: str, challenge: str, login_hint: str | None = None) -> str:
        q = {"client_id": cfg.client_id, "redirect_uri": redirect_uri(), "response_type": "code", "scope": "openid email profile",
             "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account"}
        if login_hint:
            q["login_hint"] = login_hint
        return f"{_ep('auth', AUTH_ENDPOINT)}?{urllib.parse.urlencode(q)}"

    def exchange(self, cfg: Config, code: str, verifier: str) -> str:
        import httpx
        try:
            r = httpx.post(_ep("token", TOKEN_ENDPOINT), data={"code": code, "client_id": cfg.client_id, "client_secret": cfg.client_secret,
                                                 "redirect_uri": redirect_uri(), "grant_type": "authorization_code",
                                                 "code_verifier": verifier}, timeout=10)
            r.raise_for_status()
            return r.json()["id_token"]
        except Exception as e:  # noqa: BLE001 — qualquer falha de rede/HTTP/JSON vira erro genérico
            raise GoogleError("troca de código falhou") from e

    def signing_key(self, id_token: str):
        import httpx
        try:
            kid = jwt.get_unverified_header(id_token).get("kid")
            keys = httpx.get(_ep("certs", JWKS_URI), timeout=10).json()["keys"]
            jwk = next(k for k in keys if k.get("kid") == kid)
            return jwt.algorithms.RSAAlgorithm.from_jwk(jwk)
        except Exception as e:  # noqa: BLE001
            raise GoogleError("chave de assinatura indisponível") from e


provider = GoogleProvider()  # os testes substituem por um provedor simulado


def validate_id_token(id_token: str, cfg: Config, nonce: str, key) -> dict:
    """Valida assinatura RS256, iss, aud, exp/iat, nonce e e-mail verificado. Retorna os claims."""
    try:
        claims = jwt.decode(id_token, key=key, algorithms=["RS256"], audience=cfg.client_id,
                            options={"require": ["exp", "iat", "iss", "aud", "sub"]}, leeway=30)
    except jwt.PyJWTError as e:
        raise GoogleError(f"id_token inválido: {type(e).__name__}") from e
    if claims.get("iss") not in ISSUERS + ((settings.google_test_base,) if settings.google_test_base else ()):
        raise GoogleError("emissor inválido")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise GoogleError("nonce inválido")
    if not claims.get("sub") or claims.get("email_verified") is not True or not claims.get("email"):
        raise GoogleError("e-mail Google não verificado")
    return claims
