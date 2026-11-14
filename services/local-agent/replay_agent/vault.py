"""Cofre local das credenciais de câmera enviadas pelo Portal. Cifrado em repouso (Fernet) com chave própria do servidor.

* `vault.key` (0600) é gerada UMA vez neste computador e nunca sai dele; `vault.enc` (0600) guarda os segredos cifrados.
* Escrita atômica (tmp + fsync + rename). Nada de senha em log, diagnóstico, exceção ou arquivo de configuração gerado.
* `public()` só informa quais câmeras têm credencial e a versão — nunca o valor.
"""
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class VaultError(Exception):
    pass


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    os.chmod(path, 0o600)


class CameraVault:
    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.key_path, self.data_path = self.dir / "vault.key", self.dir / "vault.enc"

    def _fernet(self) -> Fernet:
        if not self.key_path.exists():
            _atomic_write(self.key_path, Fernet.generate_key())
        return Fernet(self.key_path.read_bytes())

    def _load(self) -> dict:
        if not self.data_path.exists():
            return {}
        try:
            return json.loads(self._fernet().decrypt(self.data_path.read_bytes()))
        except (InvalidToken, ValueError) as e:
            raise VaultError("cofre ilegível (chave diferente ou arquivo corrompido)") from e

    def put(self, camera_id: str, username: str, password: str, version: int) -> None:
        """Guarda por VERSÃO (as 3 mais recentes): a reversão de configuração precisa da senha da versão anterior."""
        data = self._load()
        row = data.setdefault(camera_id, {"versions": {}})
        row["versions"][str(version)] = {"username": username, "password": password}
        for old in sorted(row["versions"], key=int)[:-3]:
            del row["versions"][old]
        _atomic_write(self.data_path, self._fernet().encrypt(json.dumps(data).encode()))

    def get(self, camera_id: str, version: int | None = None) -> dict | None:
        row = self._load().get(camera_id)
        if not row or not row["versions"]:
            return None
        key = str(version) if version is not None else max(row["versions"], key=int)
        return row["versions"].get(key)

    def has(self, camera_id: str, version: int) -> bool:
        row = self._load().get(camera_id)
        return bool(row and str(version) in row["versions"])

    def prune(self, keep_ids: set[str]) -> None:
        data = self._load()
        keep = {k: v for k, v in data.items() if k in keep_ids}
        if keep != data:
            _atomic_write(self.data_path, self._fernet().encrypt(json.dumps(keep).encode()))

    def public(self) -> dict:
        try:
            return {k: {"configured": True, "versions": sorted(int(x) for x in v["versions"])} for k, v in self._load().items()}
        except VaultError:
            return {"error": "cofre ilegível"}
