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
CREATE TABLE IF NOT EXISTS jobs(
  job_id TEXT PRIMARY KEY, court_key TEXT NOT NULL, captured_at TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
  last_error TEXT, next_attempt_at TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT);
CREATE INDEX IF NOT EXISTS ix_jobs_court_state ON jobs(court_key, state, created_at);
CREATE TABLE IF NOT EXISTS job_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, ts TEXT NOT NULL, event TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS renders(
  job_id TEXT PRIMARY KEY, local_id TEXT NOT NULL, path TEXT NOT NULL, bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'done', purpose TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trigger_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, source TEXT NOT NULL, button TEXT, court_key TEXT,
  outcome TEXT, job_id TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_trigger_court ON trigger_log(court_key, ts);
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
            cols = {r["name"] for r in self._db.execute("PRAGMA table_info(replays)")}
            jcols = {r["name"] for r in self._db.execute("PRAGMA table_info(jobs)")}
            for col, ddl in (("source", "TEXT"), ("button", "TEXT"), ("window_start", "REAL"), ("window_end", "REAL")):
                if col not in jcols:
                    self._db.execute(f"ALTER TABLE jobs ADD COLUMN {col} {ddl}")
            if "file_ok" not in cols:  # bancos criados por versões anteriores
                self._db.execute("ALTER TABLE replays ADD COLUMN file_ok INTEGER NOT NULL DEFAULT 1")

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

    def set_file_ok(self, local_id: str, ok: bool) -> None:
        self._x("UPDATE replays SET file_ok=?, sync_state=CASE WHEN sync_state='rejected' THEN sync_state ELSE 'pending' END "
                "WHERE local_id=? AND file_ok<>?", (int(ok), local_id, int(ok)))

    def reconcile_files(self, limit: int = 2000) -> int:
        """Confere se os MP4 ainda existem. Muda `file_ok` e recoloca na fila de sincronização o que mudou,
        para a central parar de oferecer (ou voltar a oferecer) o vídeo. Não apaga nada."""
        import os
        changed = 0
        for r in self._x("SELECT local_id, path, file_ok FROM replays WHERE deleted=0 ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall():
            ok = int(os.path.isfile(r["path"]))
            if ok != r["file_ok"]:
                self._x("UPDATE replays SET file_ok=?, sync_state=CASE WHEN sync_state='rejected' THEN sync_state ELSE 'pending' END WHERE local_id=?",
                        (ok, r["local_id"]))
                changed += 1
        return changed

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

    # ----- fila persistente de acionamentos (estados: queued -> processing -> done | failed) -----
    def _event(self, job_id: str, event: str, detail: str | None = None) -> None:
        self._db.execute("INSERT INTO job_events(job_id,ts,event,detail) VALUES(?,?,?,?)", (job_id, _now(), event, detail))

    def enqueue_job(self, job_id: str, court_key: str, captured_at: datetime, max_attempts: int = 3,
                    source: str | None = None, button: str | None = None, ready_at: float | None = None,
                    window: tuple[float, float] | None = None) -> bool:
        """Idempotente: o mesmo job_id nunca entra duas vezes (mesmo após reinício)."""
        with self._lock:
            cur = self._db.execute("INSERT OR IGNORE INTO jobs(job_id,court_key,captured_at,max_attempts,created_at,source,button,next_attempt_at,window_start,window_end)"
                                   " VALUES(?,?,?,?,?,?,?,?,?,?)",
                                   (job_id, court_key, captured_at.isoformat(), max_attempts, _now(), source, button,
                                    datetime.fromtimestamp(ready_at, timezone.utc).replace(tzinfo=None).isoformat() if ready_at else None,
                                    window[0] if window else None, window[1] if window else None))
            if cur.rowcount == 1:
                self._event(job_id, "queued")
            return cur.rowcount == 1

    def claim_job(self, court_key: str):
        """Reivindicação atômica do job mais antigo ELEGÍVEL desta quadra (outras quadras nunca interferem)."""
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE court_key=? AND state='queued' AND (next_attempt_at IS NULL OR next_attempt_at<=?) "
                                   "ORDER BY created_at, job_id LIMIT 1", (court_key, _now())).fetchone()
            if not row:
                return None
            self._db.execute("UPDATE jobs SET state='processing', attempts=attempts+1, started_at=? WHERE job_id=?", (_now(), row["job_id"]))
            self._event(row["job_id"], "started", f"tentativa {row['attempts'] + 1}")
            return self._db.execute("SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone()

    def finish_job(self, job_id: str, state: str, error: str | None = None, retry_in_s: float | None = None) -> None:
        with self._lock:
            if state == "queued":  # nova tentativa agendada
                nxt = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=retry_in_s or 0)).isoformat()
                self._db.execute("UPDATE jobs SET state='queued', last_error=?, next_attempt_at=? WHERE job_id=?", (error, nxt, job_id))
                self._event(job_id, "retry_scheduled", error)
            else:
                self._db.execute("UPDATE jobs SET state=?, last_error=?, finished_at=? WHERE job_id=?", (state, error, _now(), job_id))
                self._event(job_id, state, error)

    def reschedule_job(self, job_id: str, retry_at: float, detail: str = "aguardando pós-roll") -> None:
        """Volta à fila SEM gastar tentativa (o trecho do buffer ainda está sendo gravado)."""
        with self._lock:
            nxt = datetime.fromtimestamp(retry_at, timezone.utc).replace(tzinfo=None).isoformat()
            self._db.execute("UPDATE jobs SET state='queued', attempts=MAX(attempts-1,0), next_attempt_at=? WHERE job_id=?", (nxt, job_id))
            self._event(job_id, "waiting", detail)

    def pinned_windows(self, court_key: str) -> list[tuple[float, float]]:
        """Janelas de tempo de acionamentos ainda NÃO concluídos: o buffer não pode apagar esses segmentos."""
        rows = self._x("SELECT window_start a, window_end b FROM jobs WHERE court_key=? AND state IN ('queued','processing') "
                       "AND window_start IS NOT NULL", (court_key,)).fetchall()
        return [(r["a"], r["b"]) for r in rows]

    def oldest_wait_s(self, court_key: str | None = None) -> float | None:
        q, a = "SELECT MIN(created_at) FROM jobs WHERE state IN ('queued','processing')", ()
        if court_key:
            q, a = q + " AND court_key=?", (court_key,)
        r = self._x(q, a).fetchone()
        if not r or not r[0]:
            return None
        return max(0.0, (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(r[0])).total_seconds())

    def recover_jobs(self) -> int:
        """Após reinício inesperado: 'processing' órfão volta à fila (ou falha se esgotou tentativas)."""
        with self._lock:
            rows = self._db.execute("SELECT job_id, attempts, max_attempts FROM jobs WHERE state='processing'").fetchall()
            for r in rows:
                if r["attempts"] >= r["max_attempts"]:
                    self._db.execute("UPDATE jobs SET state='failed', last_error='interrompido', finished_at=? WHERE job_id=?", (_now(), r["job_id"]))
                    self._event(r["job_id"], "failed", "interrompido; tentativas esgotadas")
                else:
                    self._db.execute("UPDATE jobs SET state='queued', next_attempt_at=NULL WHERE job_id=?", (r["job_id"],))
                    self._event(r["job_id"], "recovered", "reiniciado após interrupção")
            return len(rows)

    def job_counts(self, court_key: str | None = None) -> dict:
        q, a = "SELECT state, COUNT(*) c FROM jobs", ()
        if court_key:
            q, a = q + " WHERE court_key=?", (court_key,)
        return {r["state"]: r["c"] for r in self._x(q + " GROUP BY state", a).fetchall()}

    def active_jobs(self) -> int:
        return self._x("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','processing')").fetchone()[0]

    def last_job_time(self, court_key: str) -> datetime | None:
        r = self._x("SELECT MAX(captured_at) FROM jobs WHERE court_key=?", (court_key,)).fetchone()
        return datetime.fromisoformat(r[0]) if r and r[0] else None

    def job(self, job_id: str):
        return self._x("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()

    def job_source(self, job_id: str) -> str | None:
        r = self._x("SELECT source FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return r[0] if r else None

    def job_events(self, job_id: str) -> list:
        return self._x("SELECT ts,event,detail FROM job_events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()

    # ----- saídas renderizadas (prévias patrocinadas / exportações com áudio) — ficam NA ARENA -----
    def add_render(self, job_id: str, local_id: str, path: str, size: int, sha256: str, purpose: str) -> None:
        self._x("INSERT OR REPLACE INTO renders(job_id,local_id,path,bytes,sha256,state,purpose,created_at) VALUES(?,?,?,?,?,'done',?,?)",
                (job_id, local_id, path, size, sha256, purpose, _now()))

    def render(self, job_id: str):
        return self._x("SELECT * FROM renders WHERE job_id=?", (job_id,)).fetchone()

    # ----- log durável de acionamentos (horário e origem de CADA acionamento) -----
    def log_trigger(self, ts: float, source: str, button: str | None, court_key: str | None) -> int:
        with self._lock:
            cur = self._db.execute("INSERT INTO trigger_log(ts,source,button,court_key) VALUES(?,?,?,?)", (ts, source, button, court_key))
            n = cur.lastrowid
            if n % 500 == 0:  # mantém o log limitado (não são vídeos)
                self._db.execute("DELETE FROM trigger_log WHERE id < ?", (n - 5000,))
            return n

    def set_trigger_outcome(self, tid: int, outcome: str, job_id: str | None = None, detail: str | None = None) -> None:
        self._x("UPDATE trigger_log SET outcome=?, job_id=?, detail=? WHERE id=?", (outcome, job_id, detail, tid))

    def pending_triggers(self) -> list:
        return self._x("SELECT * FROM trigger_log WHERE outcome IS NULL AND court_key IS NOT NULL ORDER BY id").fetchall()

    def recent_triggers(self, limit: int = 20, court_key: str | None = None) -> list:
        if court_key:
            return self._x("SELECT * FROM trigger_log WHERE court_key=? ORDER BY id DESC LIMIT ?", (court_key, limit)).fetchall()
        return self._x("SELECT * FROM trigger_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def trigger_outcomes(self, court_key: str | None = None) -> dict:
        q, a = "SELECT COALESCE(outcome,'pending') o, COUNT(*) c FROM trigger_log", ()
        if court_key:
            q, a = q + " WHERE court_key=?", (court_key,)
        return {r["o"]: r["c"] for r in self._x(q + " GROUP BY o", a).fetchall()}

    # ----- kv -----
    def kv_get(self, k: str, default: str | None = None) -> str | None:
        r = self._x("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return r[0] if r else default

    def kv_set(self, k: str, v: str) -> None:
        self._x("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

    def close(self) -> None:
        with self._lock:
            self._db.close()
