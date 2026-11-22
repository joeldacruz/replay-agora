"""Executor de um ciclo JPEG, sem bootstrap, cron, systemd ou leitura de credenciais.

A integração operacional deve fornecer:
- registros sincronizados da página do catálogo real;
- função resolve_record(local_id) que confirme estado atual no catálogo;
- CentralClient já autenticado e extrator JPEG.

Nunca carrega MP4 na memória (somente JPEG), nem altera catalogo.sqlite.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable


def _classify_error(exc: Exception) -> tuple[str, bool]:
    """Falha só é definitiva quando há evidência de dado local irrecuperável.

    Ausência de arquivo, timeout, HTTP 4xx ou 5xx recebem retry: replay pode
    ainda estar sendo consolidado, sincronizado ou a central pode estar em failover.
    """
    if isinstance(exc, (ValueError, TypeError)):
        return "invalid", True
    if isinstance(exc, (FileNotFoundError, PermissionError, OSError)):
        return "missing", False
    return "temporary", False


def process_batch(
    queue,
    records: list[dict],
    resolve_record: Callable[[str], dict | None],
    client,
    extract: Callable[[str], bytes],
    allowed_roots: tuple[Path, ...],
    send_thumbnail: Callable,
    max_uploads: int = 2,
    now: float | None = None,
) -> dict[str, int]:
    """Um ciclo explícito e isolado, sem loop; no máximo dois requests JPEG.

    records é uma página de candidatos do catálogo (paginada externamente).
    O resolver revalida os metadados do item reclamado; em caso de falha do
    upload o item volta à fila com backoff. Não guarda tokens ou paths na fila.
    Para evitar corrida de leases, usar um só worker por arena nesta fase.
    """
    if not 1 <= max_uploads <= 2:
        raise ValueError("max_uploads deve estar entre 1 e 2")
    keys = [r["local_id"] for r in records]
    added = queue.enqueue(keys, now=now)
    claimed = queue.claim(limit=max_uploads, now=now)
    stats = {
        "enqueued": added,
        "claimed": len(claimed),
        "synced": 0,
        "retried": 0,
        "failed": 0,
    }
    for local_id in claimed:
        try:
            replay = resolve_record(local_id)
            if replay is None or replay.get("local_id") != local_id:
                raise FileNotFoundError("registro ainda indisponivel no catalogo")
            result = send_thumbnail(client, replay, extract, allowed_roots)
            if result not in {"created", "exists"}:
                raise RuntimeError("status de central inesperado")
            queue.complete(local_id, now=now)
            stats["synced"] += 1
        except Exception as exc:
            kind, permanent = _classify_error(exc)
            queue.retry(local_id, kind=kind, now=now, permanent=permanent)
            stats["failed" if permanent else "retried"] += 1
    return stats
