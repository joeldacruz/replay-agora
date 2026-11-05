"""Motor de replay local: UMA fila e UM worker por quadra (quadras nunca bloqueiam umas às outras).

Limites de armazenamento são aplicados na CRIAÇÃO (recusa gerar quando estouraria o limite); nunca apaga arquivos.
Funciona sem a API central: tudo que importa fica no LocalStore e é sincronizado depois.
"""
import json
import os
import queue
import shutil
import statistics
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from .render import CameraDown, SimRenderer
from .store import LocalStore


class CourtWorker(threading.Thread):
    def __init__(self, engine: "Engine", court_key: str):
        super().__init__(name=f"court-{court_key}", daemon=True)
        self.engine, self.court_key = engine, court_key
        self.q: queue.Queue = queue.Queue()
        self.completed = self.failed = 0
        self.rejected_full = 0
        self.durations: deque[float] = deque(maxlen=500)
        self.state = "ok"
        self.last_error: str | None = None
        self.processing = False

    def run(self) -> None:
        while True:
            job = self.q.get()
            if job is None:
                self.q.task_done()
                return
            self.processing = True
            try:
                self.engine._process(self, *job)
            except BaseException as e:  # noqa: BLE001 - o worker de uma quadra NUNCA pode morrer
                self.failed, self.state, self.last_error = self.failed + 1, "degraded", f"worker:{type(e).__name__}"
            finally:
                self.processing = False
                self.q.task_done()

    def stats(self) -> dict:
        d = list(self.durations)
        p95 = sorted(d)[max(0, int(len(d) * 0.95) - 1)] if d else None
        return {"courtKey": self.court_key, "state": self.state, "queueDepth": self.q.qsize() + (1 if self.processing else 0),
                "completed": self.completed, "failed": self.failed, "lastRenderS": round(d[-1], 3) if d else None,
                "avgRenderS": round(statistics.fmean(d), 3) if d else None, "p95RenderS": round(p95, 3) if p95 is not None else None,
                "storageUsedBytes": self.engine.store.usage(self.court_key)}


class Engine:
    def __init__(self, arena_tag: str, root: Path, court_keys: list[str], store: LocalStore, renderer: SimRenderer,
                 dedup_s: float = 3.0, disk_reserve_bytes: int = 0, max_queue: int = 50, pre: int = 15, post: int = 5):
        self.arena_tag, self.root, self.store, self.renderer = arena_tag, Path(root), store, renderer
        self.dedup_s, self.disk_reserve, self.max_queue, self.pre, self.post = dedup_s, disk_reserve_bytes, max_queue, pre, post
        self.root.mkdir(parents=True, exist_ok=True)
        self.workers = {k: CourtWorker(self, k) for k in court_keys}
        self.arena_limit: int | None = None
        self.court_cfg: dict[str, dict] = {}
        self._last_trigger: dict[str, datetime] = {}
        self._lock = threading.Lock()
        self.disk_usage = shutil.disk_usage  # injetável nos testes
        cached = store.kv_get("config")
        if cached:  # configuração em cache: o motor opera mesmo com a central fora do ar desde o boot
            self.apply_config(json.loads(cached), persist=False)
        for w in self.workers.values():
            w.start()

    # ----- configuração -----
    def apply_config(self, cfg: dict, persist: bool = True) -> None:
        self.arena_limit = (cfg.get("storage") or {}).get("arenaLimitBytes")
        self.court_cfg = {c["courtKey"]: c for c in cfg.get("courts", [])}
        if persist:
            self.store.kv_set("config", json.dumps(cfg))

    # ----- botoeira -----
    def on_serial_line(self, line: str, at: datetime | None = None) -> dict:
        """Equivalente ao leitor.py: a mensagem serial (REPLAY_01/REPLAY_02) é a chave da quadra."""
        return self.trigger(line.strip(), at)

    def trigger(self, court_key: str, at: datetime | None = None) -> dict:
        w = self.workers.get(court_key)
        if not w:
            return {"accepted": False, "reason": "unknown_court"}
        if (self.court_cfg.get(court_key) or {}).get("status", "active") != "active":
            return {"accepted": False, "reason": "court_inactive"}
        at = at or datetime.now(timezone.utc).replace(tzinfo=None)
        with self._lock:
            last = self._last_trigger.get(court_key)
            if last and abs((at - last).total_seconds()) < self.dedup_s:
                return {"accepted": False, "reason": "duplicate"}
            if w.q.qsize() >= self.max_queue:
                w.rejected_full += 1
                return {"accepted": False, "reason": "queue_full"}
            self._last_trigger[court_key] = at
        local_id = f"{self.arena_tag}-{court_key}-{int(at.timestamp() * 1000)}"
        w.q.put((local_id, at))
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
    def _process(self, w: CourtWorker, local_id: str, at: datetime) -> None:
        t0 = time.perf_counter()
        est = getattr(self.renderer, "size_bytes", 0)
        why = self._check_storage(w.court_key, est)
        if why:
            w.failed, w.state, w.last_error = w.failed + 1, "storage_failure", why
            return
        cdir = self.root / w.court_key
        tmp = cdir / ".tmp" / f"{local_id}.mp4"
        final = cdir / f"{local_id}.mp4"
        try:
            tmp.parent.mkdir(parents=True, exist_ok=True)
            r = self.renderer.render(w.court_key, tmp, self.pre, self.post)
            os.replace(tmp, final)  # publicação atômica: nunca expõe arquivo parcial
            self.store.add_replay(local_id, w.court_key, at, str(final), r.bytes, r.duration, r.width, r.height, r.has_audio, r.codec)
            w.completed += 1
            w.state, w.last_error = "ok", None
            w.durations.append(time.perf_counter() - t0)
        except CameraDown as e:
            w.failed, w.state, w.last_error = w.failed + 1, "camera_down", f"camera:{e}"
        except OSError as e:  # disco cheio/erro de escrita: remove só o PARCIAL que este job criou
            tmp.unlink(missing_ok=True)
            w.failed, w.state, w.last_error = w.failed + 1, "storage_failure", f"io:{e.errno}"
        except Exception as e:  # noqa: BLE001 - um job ruim não pode matar o worker da quadra
            tmp.unlink(missing_ok=True)
            w.failed, w.state, w.last_error = w.failed + 1, "degraded", type(e).__name__

    # ----- observabilidade / ciclo de vida -----
    def court_stats(self) -> list[dict]:
        return [w.stats() for w in self.workers.values()]

    def wait_idle(self, timeout: float = 60) -> bool:
        end = time.monotonic() + timeout
        for w in self.workers.values():
            while w.q.unfinished_tasks and time.monotonic() < end:
                time.sleep(0.01)
        return all(w.q.unfinished_tasks == 0 for w in self.workers.values())

    def stop(self) -> None:
        for w in self.workers.values():
            w.q.put(None)
        for w in self.workers.values():
            w.join(timeout=10)
