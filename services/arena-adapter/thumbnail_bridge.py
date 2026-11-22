"""Fila de miniaturas para o catálogo operacional da arena (SEM deploy).

Componente isolado: não importa auto_replays.py, não escreve no catálogo e
não toca no motor de captura. Recebe um CentralClient já autenticado por quem
opera o adaptador existente. Os MP4s nunca são enviados à central.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import re
import sqlite3
from urllib.parse import quote

LOCAL_ID = re.compile(r"^[A-Za-z0-9_.:-]{8,120}$")
MAX_JPEG = 256 * 1024


def eligible_rows(catalog: Path, limit: int = 2) -> list[dict]:
    """Consulta apenas replays confirmados e não apagados (snapshot de leitura).

    immutable=1 evita criação de WAL/SHM. Isso pode omitir commits ainda no WAL:
    para produção, usar um snapshot consistente fornecido pelo adaptador.
    """
    if not 1 <= limit <= 20:
        raise ValueError("limite invalido")
    uri = Path(catalog).resolve().as_uri() + "?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        rows = db.execute(
            "SELECT local_id, court_key, path, bytes "
            "FROM replays "
            "WHERE sync_state='synced' AND deleted=0 AND file_ok=1 "
            "AND path IS NOT NULL AND length(path)>0 "
            "ORDER BY captured_at ASC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def send_thumbnail(client, replay: dict, extract, allowed_roots: tuple[Path, ...]) -> str:
    """Envia JPEG com HMAC gerado pelo CentralClient.request_raw.

    Chamador fornece extract(path)->bytes, idealmente o extrator existente.
    Nenhum estado do catálogo é alterado. O chamador decide retry/controle de fila.
    """
    local_id = str(replay["local_id"])
    if not LOCAL_ID.fullmatch(local_id):
        raise ValueError("local_id fora do contrato")
    path = Path(replay["path"]).resolve(strict=True)
    roots = tuple(Path(x).resolve(strict=True) for x in allowed_roots)
    if not roots or not any(path.is_relative_to(root) for root in roots):
        raise ValueError("MP4 fora das raizes autorizadas")
    if not path.is_file() or path.suffix.lower() != ".mp4":
        raise ValueError("arquivo invalido")
    stat = path.stat()
    if stat.st_size < 64 or stat.st_size != int(replay["bytes"]):
        raise ValueError("MP4 incompleto ou alterado")
    jpeg = extract(str(path))
    if not isinstance(jpeg, bytes) or not 64 <= len(jpeg) <= MAX_JPEG:
        raise ValueError("JPEG fora do limite")
    if not jpeg.startswith(bytes.fromhex("FFD8FF")) or not jpeg.endswith(bytes.fromhex("FFD9")):
        raise ValueError("JPEG invalido")
    response = client.request_raw(
        "POST",
        "/sync/replays/" + quote(local_id, safe="") + "/thumbnail",
        content=jpeg,
        headers={"Content-Type": "image/jpeg",
                 "X-Thumb-Sha256": hashlib.sha256(jpeg).hexdigest()},
        timeout=20,
    )
    data = response.json()
    if data.get("ok") is not True or data.get("status") not in ("created", "exists"):
        raise ValueError("resposta inesperada da central")
    return data["status"]
