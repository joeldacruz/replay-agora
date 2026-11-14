"""Ligação com a central: registro, heartbeat, config, sincronização idempotente de metadados.

Regras: (1) nunca envia arquivo; (2) falha da central NUNCA interrompe o motor; (3) reenvio é sempre seguro;
(4) troca de epoch (failover) => revalida registro e reenvia a janela recente (a central deduplica por localId).
"""
import json
import random
import threading
import time

from .client import CentralClient, CentralRejected, CentralUnavailable
from .creds import CredentialStore
from .engine import Engine
from .metrics import SystemSampler
from .store import LocalStore

VERSION = "0.1.0-sim"


class CentralLink:
    def __init__(self, client: CentralClient, store: LocalStore, engine: Engine, hostname: str = "sim-host",
                 sampler: SystemSampler | None = None, batch: int = 100, resync_hours: int = 24,
                 creds: CredentialStore | None = None, governor=None):
        self.client, self.store, self.engine = client, store, engine
        self.hostname, self.sampler, self.batch, self.resync_hours = hostname, sampler, batch, resync_hours
        self.registered = False
        self.state = "offline"          # online|offline|rejected
        self.last_error: str | None = None
        self.fail_streak = 0
        self.config_version = ""
        self.creds = creds
        self.governor = governor
        self.diag_provider = None          # callable() -> dict (diagnóstico completo)
        self.command_handler = None        # callable(cmd) -> dict(outcome, jobId, detail)
        self.remote_tick = None            # callable(hint) -> None  (configuração remota: aplica pendências, tarefas e inventário)
        self.last_ok_at: float | None = None
        self.rotations = 0

    # ----- passos -----
    def _register(self) -> None:
        r = self.client.call("POST", "/register", {"nodeId": self.client.node_id, "hostname": self.hostname, "version": VERSION,
                                                   "courts": list(self.engine.workers)})
        self.registered = True
        self._apply(r["config"])

    def _apply(self, cfg: dict) -> None:
        self.engine.apply_config(cfg)
        res = cfg.get("resources")
        if res and self.governor is not None:   # ajuste por arena vindo da central; o governor limita ao hardware local
            self.governor.apply(res.get("maxFfmpeg"), res.get("hwaccel"), res.get("renderNice"))
        self.config_version = cfg["configVersion"]

    def _heartbeat(self) -> dict:
        body = {"version": VERSION, "configVersion": self.config_version, "pendingSync": self.store.count("pending"),
                "system": self.sampler.sample() if self.sampler else {}, "courts": self.engine.court_stats()}
        if self.diag_provider:
            try:
                body["diagnostics"] = self.diag_provider()
            except Exception:  # noqa: BLE001 - diagnóstico nunca derruba o heartbeat
                body["diagnostics"] = None
        return self.client.call("POST", "/heartbeat", body)

    def _run_commands(self, commands: list[dict]) -> None:
        for cmd in commands or []:
            if self.command_handler is None:
                continue
            try:
                res = self.command_handler(cmd)
            except Exception as e:  # noqa: BLE001
                res = {"outcome": "error", "detail": type(e).__name__}
            self.client.call("POST", f"/commands/{cmd['id']}/result", {"outcome": str(res.get("outcome", "error"))[:30],
                                                                         "jobId": res.get("jobId"), "detail": res.get("detail")})

    def rotate_credentials(self) -> None:
        """Troca de credencial: persiste o par NOVO em disco antes de usá-lo (a anterior ainda vale pela janela de carência)."""
        r = self.client.call("POST", "/credentials/rotate")
        if self.creds:
            self.creds.save(r["token"], r["signingKey"], r.get("expiresAt"))
        self.client.set_credentials(r["token"], r["signingKey"])
        self.rotations += 1

    def _sync(self) -> int:
        sent = 0
        while True:
            rows = self.store.pending(self.batch)
            if not rows:
                return sent
            items = [{"localId": r["local_id"], "courtKey": r["court_key"], "capturedAt": r["captured_at"] + "Z",
                      "durationS": r["duration"], "bytes": r["bytes"], "width": r["width"] or 0, "height": r["height"] or 0,
                      "hasAudio": bool(r["has_audio"]), "videoCodec": r["codec"], "protected": bool(r["protected"]),
                      "fileAvailable": bool(r["file_ok"]),
                      **({"title": "Teste de acionamento (painel)"} if (self.store.job_source(r["local_id"]) or "").startswith("manual:") else {})}
                     for r in rows]
            res = self.client.call("POST", "/sync/replays", {"replays": items})
            by_id = {x["localId"]: x for x in res["results"]}
            for r in rows:  # só marca o que a central CONFIRMOU; o resto continua pendente
                x = by_id.get(r["local_id"])
                if not x:
                    continue
                if x["status"] in ("created", "duplicate"):
                    self.store.mark(r["local_id"], "synced")
                else:
                    self.store.mark(r["local_id"], "rejected", x.get("reason"))
            sent += len(rows)
            if not any(by_id.get(r["local_id"]) for r in rows):
                return sent

    def tick(self) -> dict:
        """Um ciclo completo. Retorna o resultado; nunca propaga erro de rede."""
        try:
            if not self.registered:
                self._register()
            self.store.reconcile_files()
            hb = self._heartbeat()
            if self.client.epoch_changed:  # failover detectado
                self.client.epoch_changed = False
                self.registered = False
                self._register()
                self.store.requeue_recent(self.resync_hours)
            self._run_commands(hb.get("commands", []))
            if self.remote_tick is not None:
                try:
                    self.remote_tick(hb.get("remote"))
                except (CentralUnavailable, CentralRejected):
                    raise
                except Exception:  # noqa: BLE001 - a configuração remota nunca derruba o ciclo de sincronização
                    pass
            if hb.get("rotateRecommended") and self.creds is not None:
                self.rotate_credentials()
            if hb.get("configChanged"):
                self._apply(self.client.call("GET", "/config"))
            self.store.set_central_protected(self.client.call("GET", "/sync/protection")["localIds"])
            synced = self._sync()
            self.state, self.last_error, self.fail_streak = "online", None, 0
            self.last_ok_at = time.time()
            return {"online": True, "synced": synced, "pending": self.store.count("pending")}
        except CentralUnavailable as e:
            self.state, self.last_error = "offline", str(e)
            self.fail_streak += 1
            return {"online": False, "error": str(e), "pending": self.store.count("pending")}
        except CentralRejected as e:
            self.state, self.last_error = "rejected", str(e)
            self.fail_streak += 1
            return {"online": False, "error": str(e), "pending": self.store.count("pending")}

    def backoff_s(self, base: float = 2.0, cap: float = 120.0) -> float:
        return min(cap, base * (2 ** min(self.fail_streak, 10))) * random.uniform(0.8, 1.2) if self.fail_streak else base

    def run_forever(self, stop: threading.Event, interval_s: float = 30.0) -> None:  # pragma: no cover - laço de produção
        while not stop.is_set():
            self.tick()
            stop.wait(interval_s if self.state == "online" else self.backoff_s())

    def summary(self) -> str:
        return json.dumps({"state": self.state, "pending": self.store.count("pending"), "error": self.last_error})
