"""Armazenamento privado em disco, fora de qualquer diretório servido estaticamente."""
import re
import shutil
from pathlib import Path

from .config import settings


def root() -> Path:
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    return settings.storage_dir


def path_for(key: str) -> Path:
    """Resolve uma chave de storage garantindo que fica dentro da raiz (anti path traversal)."""
    p = (root() / key).resolve()
    if root() not in p.parents:
        raise ValueError("storage key inválida")
    return p


def make_key(arena_id: str, kind: str, name: str) -> str:
    assert re.fullmatch(r"[a-z]+", kind) and re.fullmatch(r"[A-Za-z0-9_.-]+", name) and re.fullmatch(r"[0-9a-f]{32}", arena_id)
    return f"arenas/{arena_id}/{kind}/{name}"


def ensure_parent(key: str) -> Path:
    p = path_for(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def move_into(src: Path, key: str) -> Path:
    dst = ensure_parent(key)
    shutil.move(str(src), str(dst))
    return dst


def delete(key: str | None) -> None:
    if not key:
        return
    try:
        path_for(key).unlink(missing_ok=True)
    except ValueError:
        pass
