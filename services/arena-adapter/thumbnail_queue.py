"""Fila persistente SQLite separada do catálogo da arena.

Biblioteca pura. Nenhuma execução automática, rede, acesso a câmeras ou serviço.
O chamador deve fornecer candidatos já confirmados e testar o bridge antes do deploy.
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS thumbnails (
  local_id TEXT PRIMARY KEY,
  state TEXT NOT NULL DEFAULT 'pending'
    CHECK(state IN ('pending', 'processing', 'synced', 'failed')),
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at REAL NOT NULL DEFAULT 0,
  claimed_at REAL,
  updated_at REAL NOT NULL,
  error_kind TEXT
);
CREATE INDEX IF NOT EXISTS idx_thumbnails_ready
  ON thumbnails(state, next_attempt_at, updated_at);
"""


class ThumbnailQueue:
    """Persistência local e leasing para um único ou vários trabalhadores.

    A fila não copia arquivos MP4 e não modifica catalogo.sqlite.
    Falhas de rede geram nova tentativa exponencial limitada.
    """

    def __init__(self, path: Path, catalog_path: Path | None = None):
        self.path = Path(path)
        if catalog_path is not None and self.path.resolve() == Path(catalog_path).resolve():
            raise ValueError("fila e catalogo nao podem ser o mesmo banco")
        if not self.path.parent.is_dir():
            raise ValueError("diretorio da fila inexistente")
        with self._connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA busy_timeout=5000")
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, local_ids, now: float | None = None) -> int:
        now = time.time() if now is None else float(now)
        rows = [(str(key), now) for key in local_ids]
        if not rows:
            return 0
        if any(not row[0] or len(row[0]) > 120 for row in rows):
            raise ValueError("local_id invalido")
        with self._connect() as db:
            before = db.total_changes
            db.executemany(
                "INSERT OR IGNORE INTO thumbnails(local_id, updated_at) VALUES (?, ?)",
                rows,
            )
            return db.total_changes - before

    def claim(self, limit: int = 2, now: float | None = None, lease_s: float = 120.0) -> list[str]:
        if not 1 <= limit <= 20 or lease_s <= 0:
            raise ValueError("limite ou lease invalido")
        now = time.time() if now is None else float(now)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # Recuperar apenas leases expirados, sem reenviar os sincronizados.
            db.execute(
                "UPDATE thumbnails SET state='pending', claimed_at=NULL, "
                "next_attempt_at=?, updated_at=? "
                "WHERE state='processing' AND claimed_at <= ?",
                (now, now, now - lease_s),
            )
            ids = [
                r[0] for r in db.execute(
                    "SELECT local_id FROM thumbnails "
                    "WHERE state='pending' AND next_attempt_at <= ? "
                    "ORDER BY next_attempt_at, updated_at, local_id LIMIT ?",
                    (now, limit),
                ).fetchall()
            ]
            if ids:
                db.executemany(
                    "UPDATE thumbnails SET state='processing', claimed_at=?, updated_at=? "
                    "WHERE local_id=? AND state='pending'",
                    [(now, now, local_id) for local_id in ids],
                )
            return ids

    def complete(self, local_id: str, now: float | None = None):
        now = time.time() if now is None else float(now)
        with self._connect() as db:
            cur = db.execute(
                "UPDATE thumbnails SET state='synced', claimed_at=NULL, "
                "error_kind=NULL, updated_at=? "
                "WHERE local_id=? AND state='processing'", (now, local_id),
            )
            if cur.rowcount != 1:
                raise ValueError("item nao reservado")

    def retry(self, local_id: str, kind: str = "temporary",
              now: float | None = None, permanent: bool = False) -> float | None:
        now = time.time() if now is None else float(now)
        if kind not in {"temporary", "network", "missing", "invalid", "rejected"}:
            raise ValueError("classe de erro nao reconhecida")
        with self._connect() as db:
            row = db.execute(
                "SELECT attempts FROM thumbnails WHERE local_id=? AND state='processing'",
                (local_id,),
            ).fetchone()
            if row is None:
                raise ValueError("item nao reservado")
            attempts = row[0] + 1
            delay = min(3600.0, 30.0 * 2 ** min(attempts - 1, 10))
            next_at = None if permanent else now + delay
            db.execute(
                "UPDATE thumbnails SET state=?, attempts=?, claimed_at=NULL, "
                "next_attempt_at=?, error_kind=?, updated_at=? WHERE local_id=?",
                ("failed" if permanent else "pending", attempts,
                 0 if next_at is None else next_at, kind, now, local_id),
            )
            return next_at

    def counts(self) -> dict[str, int]:
        with self._connect() as db:
            return dict(db.execute(
                "SELECT state, count(*) FROM thumbnails GROUP BY state"
            ).fetchall())
