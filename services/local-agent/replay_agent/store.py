"""Estado LOCAL durável (SQLite): replays gerados e fila de sincronização. Funciona sem a API central.

`sync_state`: pending -> synced | rejected. O localId é a chave de idempotência enviada à central, portanto reenviar
após queda/reconexão nunca duplica. Nada aqui apaga arquivo: remoção real não faz parte desta etapa.
"""
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS replays(
  local_id TEXT PRIMARY KEY, court_key TEXT NOT NULL, captured_at TEXT NOT NULL, path TEXT NOT NULL,
  bytes INTEGER NOT NULL, duration REAL NOT NULL, width INTEGER, height INTEGER, has_audio INTEGER, codec TEXT,
  protected INTEGER NOT NULL DEFAULT 0, central_protected INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0,
  sync_state TEXT NOT NULL DEFAULT 'pending', sync_reason TEXT, synced_at TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_replays_sync ON replays(sync_state, created_at);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


class _Res:
    def __init__(self, rows: list, rowcount: int):
        self._rows, self.rowcount = rows, rowcount

    def fetchall(self) -> list:
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class LocalStore:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)

    def _x(self, sql: str, args: tuple = ()) -> "_Res":
        """Executa E lê o resultado dentro do lock: a conexão é compartilhada entre os workers das quadras."""
        with self._lock:
            cur = self._db.execute(sql, args)
            return _Res(cur.fetchall(), cur.rowcount)

    # ----- replays -----
    def add_replay(self, local_id: str, court_key: str, captured_at: datetime, path: str, size: int, duration: float,
                   width: int, height: int, has_audio: bool, codec: str) -> bool:
        cur = self._x("INSERT OR IGNORE INTO replays(local_id,court_key,captured_at,path,bytes,duration,width,height,has_audio,codec,created_at)"
                      " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (local_id, court_key, captured_at.isoformat(), path, size, duration, width, height, int(has_audio), codec, _now()))
        return cur.rowcount == 1

    def pending(self, limit: int = 100) -> list[sqlite3.Row]:
        return self._x("SELECT * FROM replays WHERE sync_state='pending' ORDER BY created_at LIMIT ?", (limit,)).fetchall()

    def count(self, state: str | None = None) -> int:
        if state:
            return self._x("SELECT COUNT(*) FROM replays WHERE sync_state=?", (state,)).fetchone()[0]
        return self._x("SELECT COUNT(*) FROM replays").fetchone()[0]

    def mark(self, local_id: str, state: str, reason: str | None = None) -> None:
        self._x("UPDATE replays SET sync_state=?, sync_reason=?, synced_at=? WHERE local_id=?",
                (state, reason, _now() if state == "synced" else None, local_id))

    def requeue_recent(self, hours: int) -> int:
        """Após troca de primário (epoch): reenvia o que foi confirmado recentemente — seguro porque a central é idempotente."""
        cut = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)).isoformat()
        return self._x("UPDATE replays SET sync_state='pending' WHERE sync_state='synced' AND synced_at>=?", (cut,)).rowcount

    def usage(self, court_key: str | None = None) -> int:
        if court_key:
            return self._x("SELECT COALESCE(SUM(bytes),0) FROM replays WHERE deleted=0 AND court_key=?", (court_key,)).fetchone()[0]
        return self._x("SELECT COALESCE(SUM(bytes),0) FROM replays WHERE deleted=0").fetchone()[0]

    def get(self, local_id: str):
        return self._x("SELECT * FROM replays WHERE local_id=?", (local_id,)).fetchone()

    # ----- proteção -----
    def set_protected(self, local_id: str, value: bool) -> None:
        self._x("UPDATE replays SET protected=? WHERE local_id=?", (int(value), local_id))

    def set_central_protected(self, ids: list[str]) -> None:
        """A lista da central é autoritativa para a marca `central_protected`."""
        with self._lock:
            self._db.execute("BEGIN")
            self._db.execute("UPDATE replays SET central_protected=0 WHERE central_protected=1")
            self._db.executemany("UPDATE replays SET central_protected=1 WHERE local_id=?", [(i,) for i in ids])
            self._db.execute("COMMIT")

    def retention_candidates(self, court_key: str, retention_days: int, now: datetime | None = None) -> list[str]:
        """DRY-RUN: ids vencidos e NÃO protegidos (local ou central). Nesta etapa ninguém os apaga."""
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        cut = (now - timedelta(days=retention_days)).isoformat()
        rows = self._x("SELECT local_id FROM replays WHERE court_key=? AND deleted=0 AND protected=0 AND central_protected=0 "
                       "AND captured_at<? ORDER BY captured_at", (court_key, cut)).fetchall()
        return [r[0] for r in rows]

    # ----- kv -----
    def kv_get(self, k: str, default: str | None = None) -> str | None:
        r = self._x("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return r[0] if r else default

    def kv_set(self, k: str, v: str) -> None:
        self._x("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

    def close(self) -> None:
        with self._lock:
            self._db.close()
