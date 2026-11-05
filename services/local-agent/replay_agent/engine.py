"""Motor de replay local: UMA fila PERSISTENTE (SQLite) e UM worker por quadra.

* Acionamentos são gravados no disco ANTES de qualquer processamento; reinício inesperado => `recover_jobs()`.
* `job_id == local_id` (quadra + instante): não existe geração duplicada, nem após reinício ou reenvio.
* Tentativas/erros/estados ficam registrados em `jobs` e `job_events`.
* Limites de armazenamento são aplicados na CRIAÇÃO (recusa gerar); nunca apaga arquivos.
* Funciona sem a API central.
"""
import json
import os
import shutil
import statistics
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from .render import CameraDown, SimRenderer
from .store import LocalStore


class StorageRefused(Exception):
    """Recusa por limite/disco: terminal (não adianta repetir sem mudar a configuração)."""


class CourtWorker(threading.Thread):
    def __init__(self, engine: "Engine", court_key: str):
        super().__init__(name=f"court-{court_key}", daemon=True)
        self.engine, self.court_key = engine, court_key
        self.wake = threading.Event()
        self.stop_flag = False
        self.rejected_full = 0
        self.retries = 0
        self.durations: deque[float] = deque(maxlen=500)
        self.state = "ok"
        self.last_error: str | None = None
        self.processing = False

    def run(self) -> None:
        st = self.engine.store
        while not self.stop_flag:
            try:
                job = st.claim_job(self.court_key)
            except Exception:  # noqa: BLE001 - banco momentaneamente indisponível: tenta de novo
                time.sleep(0.2)
                continue
            if not job:
                self.wake.wait(0.2)  # acorda por trigger ou para pegar retentativas agendadas
                self.wake.clear()
                continue
            self.processing = True
            try:
                self.engine._process(self, job)
            except BaseException as e:  # noqa: BLE001 - o worker de uma quadra NUNCA pode morrer
                self.engine._fail(self, job, f"worker:{type(e).__name__}", retriable=True)
            finally:
                self.processing = False

    # contadores derivados do banco => sobrevivem a reinício
    @property
    def completed(self) -> int:
        return self.engine.store.job_counts(self.court_key).get("done", 0)

    @property
    def failed(self) -> int:
        return self.engine.store.job_counts(self.court_key).get("failed", 0)

    def stats(self) -> dict:
        d, c = list(self.durations), self.engine.store.job_counts(self.court_key)
        p95 = sorted(d)[max(0, int(len(d) * 0.95) - 1)] if d else None
        return {"courtKey": self.court_key, "state": self.state, "queueDepth": c.get("queued", 0) + c.get("processing", 0),
                "completed": c.get("done", 0), "failed": c.get("failed", 0), "retries": self.retries,
                "lastRenderS": round(d[-1], 3) if d else None, "avgRenderS": round(statistics.fmean(d), 3) if d else None,
                "p95RenderS": round(p95, 3) if p95 is not None else None, "storageUsedBytes": self.engine.store.usage(self.court_key)}


class Engine:
    def __init__(self, arena_tag: str, root: Path, court_keys: list[str], store: LocalStore, renderer: SimRenderer,
                 dedup_s: float = 3.0, disk_reserve_bytes: int = 0, max_queue: int = 50, pre: int = 15, post: int = 5,
                 max_attempts: int = 3, retry_backoff_s: float = 2.0):
        self.arena_tag, self.root, self.store, self.renderer = arena_tag, Path(root), store, renderer
        self.dedup_s, self.disk_reserve, self.max_queue, self.pre, self.post = dedup_s, disk_reserve_bytes, max_queue, pre, post
        self.max_attempts, self.retry_backoff_s = max_attempts, retry_backoff_s
        self.root.mkdir(parents=True, exist_ok=True)
        self.workers = {k: CourtWorker(self, k) for k in court_keys}
        self.policy: dict[str, dict] = {k: {"cooldown_s": dedup_s, "pre": pre, "post": post} for k in court_keys}   # por quadra
        self.arena_limit: int | None = None
        self.court_cfg: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.disk_usage = shutil.disk_usage  # injetável nos testes
        cached = store.kv_get("config")
        if cached:  # configuração em cache: o motor opera mesmo com a central fora do ar desde o boot
            self.apply_config(json.loads(cached), persist=False)
        self.recovered = store.recover_jobs()  # reinício inesperado: retoma o que estava em andamento
        self._clean_partials()
        for w in self.workers.values():
            w.start()

    def _clean_partials(self) -> None:
        """Remove SOMENTE arquivos parciais de execuções anteriores (pasta .tmp do próprio motor)."""
        for k in self.workers:
            for p in (self.root / k / ".tmp").glob("*.mp4"):
                p.unlink(missing_ok=True)

    # ----- configuração -----
    def apply_config(self, cfg: dict, persist: bool = True) -> None:
        self.arena_limit = (cfg.get("storage") or {}).get("arenaLimitBytes")
        self.court_cfg = {c["courtKey"]: c for c in cfg.get("courts", [])}
        if persist:
            self.store.kv_set("config", json.dumps(cfg))

    def configure_court(self, court_key: str, cooldown_s: float | None = None, pre: int | None = None, post: int | None = None) -> None:
        """Política independente por quadra (cooldown entre replays e janela pré/pós)."""
        p = self.policy[court_key]
        if cooldown_s is not None:
            p["cooldown_s"] = cooldown_s
        if pre is not None:
            p["pre"] = pre
        if post is not None:
            p["post"] = post

    # ----- botoeira -----
    def on_serial_line(self, line: str, at: datetime | None = None) -> dict:
        """Equivalente ao leitor.py: a mensagem serial (REPLAY_01/REPLAY_02) é a chave da quadra."""
        return self.trigger(line.strip(), at)

    def trigger(self, court_key: str, at: datetime | None = None, source: str = "api", button: str | None = None) -> dict:
        w = self.workers.get(court_key)
        if not w:
            return {"accepted": False, "reason": "unknown_court"}
        if (self.court_cfg.get(court_key) or {}).get("status", "active") != "active":
            return {"accepted": False, "reason": "court_inactive"}
        at = at or datetime.now(timezone.utc).replace(tzinfo=None)
        with self._lock:
            last = self.store.last_job_time(court_key)  # debounce persistente: vale também após reinício
            if last and abs((at - last).total_seconds()) < self.policy[court_key]["cooldown_s"]:
                return {"accepted": False, "reason": "duplicate"}
            c = self.store.job_counts(court_key)
            if c.get("queued", 0) + c.get("processing", 0) >= self.max_queue:
                w.rejected_full += 1
                return {"accepted": False, "reason": "queue_full"}
            local_id = f"{self.arena_tag}-{court_key}-{int(at.timestamp() * 1000)}"
            if not self.store.enqueue_job(local_id, court_key, at, self.max_attempts, source=source, button=button):
                return {"accepted": False, "reason": "duplicate"}
        w.wake.set()
        return {"accepted": True, "localId": local_id}

    # ----- armazenamento -----
    def _check_storage(self, court_key: str, est: int) -> str | None:
        ccfg = self.court_cfg.get(court_key) or {}
        lim = ccfg.get("storageLimitBytes")
        if lim and self.store.usage(court_key) + est > lim:
            return "court_limit"
        if self.arena_limit and self.store.usage() + est > self.arena_limit:
            return "arena_limit"
        try:
            if self.disk_usage(str(self.root)).free - est < self.disk_reserve:
                return "disk_free_low"
        except OSError:
            return "disk_unavailable"
        return None

    # ----- processamento (por worker de quadra) -----
    def _fail(self, w: CourtWorker, job, reason: str, retriable: bool, state: str = "degraded") -> None:
        w.state, w.last_error = state, reason
        if retriable and job["attempts"] < job["max_attempts"]:
            w.retries += 1
            self.store.finish_job(job["job_id"], "queued", reason, retry_in_s=self.retry_backoff_s * job["attempts"])
        else:
            self.store.finish_job(job["job_id"], "failed", reason)

    def _process(self, w: CourtWorker, job) -> None:
        t0 = time.perf_counter()
        local_id, at = job["job_id"], datetime.fromisoformat(job["captured_at"])
        cdir = self.root / w.court_key
        final = cdir / f"{local_id}.mp4"
        existing = self.store.get(local_id)
        if existing and final.is_file():  # já gerado (queda entre gravar e concluir): NÃO regenera
            self.store.finish_job(local_id, "done")
            return
        why = self._check_storage(w.court_key, getattr(self.renderer, "size_bytes", 0))
        if why:
            return self._fail(w, job, why, retriable=False, state="storage_failure")
        tmp = cdir / ".tmp" / f"{local_id}.mp4"
        try:
            tmp.parent.mkdir(parents=True, exist_ok=True)
            pol = self.policy[w.court_key]
            r = self.renderer.render(w.court_key, tmp, pol["pre"], pol["post"], at)
            os.replace(tmp, final)  # publicação atômica: nunca expõe arquivo parcial
            self.store.add_replay(local_id, w.court_key, at, str(final), r.bytes, r.duration, r.width, r.height, r.has_audio, r.codec)
            self.store.finish_job(local_id, "done")
            w.state, w.last_error = "ok", None
            w.durations.append(time.perf_counter() - t0)
        except CameraDown as e:  # o trecho do buffer não volta: sem retentativa
            tmp.unlink(missing_ok=True)
            self._fail(w, job, f"camera:{e}", retriable=False, state="camera_down")
        except OSError as e:  # disco cheio/erro de escrita: remove só o PARCIAL deste job
            tmp.unlink(missing_ok=True)
            self._fail(w, job, f"io:{e.errno}", retriable=True, state="storage_failure")
        except Exception as e:  # noqa: BLE001 - um job ruim não pode matar o worker da quadra
            tmp.unlink(missing_ok=True)
            self._fail(w, job, type(e).__name__, retriable=True)

    # ----- observabilidade / ciclo de vida -----
    def court_stats(self) -> list[dict]:
        return [w.stats() for w in self.workers.values()]

    def wait_idle(self, timeout: float = 60) -> bool:
        end = time.monotonic() + timeout
        while self.store.active_jobs() and time.monotonic() < end:
            time.sleep(0.01)
        return self.store.active_jobs() == 0

    def stop(self) -> None:
        for w in self.workers.values():
            w.stop_flag = True
            w.wake.set()
        for w in self.workers.values():
            w.join(timeout=10)
