"""Regressão: chave de sessão previsível não pode iniciar em produção."""
import secrets

import pytest

from app.config import Settings


@pytest.mark.parametrize("secret", [
    None,
    "",
    "curta",
    "troque-por-um-segredo-longo-e-aleatorio",
    "changeme-please-use-a-long-different-key",
])
def test_production_rejects_missing_weak_or_example_key(monkeypatch, secret):
    monkeypatch.setenv("APP_ENV", "production")
    if secret is None:
        monkeypatch.delenv("SECRET_KEY", raising=False)
    else:
        monkeypatch.setenv("SECRET_KEY", secret)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        Settings()


def test_production_accepts_random_long_key(monkeypatch):
    secret = secrets.token_urlsafe(48)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", secret)
    assert Settings().secret_key == secret


def test_development_generates_ephemeral_key_if_unspecified(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    a = Settings().secret_key
    b = Settings().secret_key
    assert len(a) >= 32 and a != b
