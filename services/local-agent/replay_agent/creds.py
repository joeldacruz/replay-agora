"""Armazenamento local das credenciais (arquivo 0600, escrita atômica). Nunca em Git, nunca em log."""
import json
import os
from pathlib import Path


class CredentialStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict | None:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None

    def save(self, token: str, signing_key: str | None, expires_at: str | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"token": token, "signingKey": signing_key, "expiresAt": expires_at}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)
