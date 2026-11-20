"""Servidor de trechos do agente: busca pedidos da central por conexão de SAÍDA (long-poll) e responde com bytes do disco local.

O agente NÃO abre porta nem escuta na internet. Só serve arquivos que existem no próprio banco local (por local_id/job_id),
dentro das pastas permitidas; qualquer caminho vindo da rede é ignorado (nunca há caminho no pedido).
"""
import errno
import hashlib
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .client import CentralClient, CentralRejected, CentralUnavailable
from .filecheck import Probe, classify, errno_name, probe_file
from .store import LocalStore

log = logging.getLogger("replay_agent.relay")
_ID = re.compile(r"^[A-Za-z0-9_.:\-]{8,120}$")
_MSG = {"not_found": "arquivo não encontrado na arena", "io_error": "arquivo indisponível na arena (erro de leitura); tente novamente"}


@dataclass
class Resolution:
    """Resultado de `_resolve_ex`. `code` é o que vai à central (contrato: not_found | io_error | bad_request); `reason` é a causa LOCAL
    detalhada (log). A ausência definitiva é `not_found`; permissão/E-S são `io_error` e NÃO rebaixam o replay."""
    path: Path | None = None
    code: str | None = None
    reason: str | None = None
    probe: Probe | None = None


class RelayServer:
    def __init__(self, client: CentralClient, store: LocalStore, allowed_roots: list[Path], max_chunk: int = 4 * 1024 * 1024):
        self.client, self.store, self.max_chunk = client, store, max_chunk
        self.roots = [Path(r).resolve() for r in allowed_roots]
        self.served = self.errors = 0
        self._last_log: dict[tuple[str, str], float] = {}

    def _log_failure(self, kind: str, ref: str, stage: str, reason: str, pr: Probe | None = None) -> None:
        """Uma linha estruturada e SANITIZADA (ref, causa, errno, componente do caminho, uid/gid); no máximo 1 por minuto por (ref, causa)."""
        key, now = (ref, reason), time.monotonic()
        if now - self._last_log.get(key, -1e9) < 60:
            return
        self._last_log[key] = now
        log.warning("relay_unresolved kind=%s ref=%s stage=%s reason=%s errno=%s component=%s uid=%s gid=%s", kind, ref, stage, reason,
                    pr.errno if pr else "-", pr.component if pr else "-", os.geteuid(), os.getegid())

    def _resolve_ex(self, kind: str, ref: str) -> Resolution:
        if not _ID.match(ref):
            return Resolution(code="not_found", reason="invalid_ref")
        if kind == "video":
            row = self.store.get(ref)
            if not row or row["deleted"]:
                return Resolution(code="not_found", reason="unknown_ref")
            path = Path(row["path"])
        elif kind == "render":
            row = self.store.render(ref)
            if not row or row["state"] != "done":
                return Resolution(code="not_found", reason="unknown_ref")
            path = Path(row["path"])
        else:
            return Resolution(code="not_found", reason="invalid_kind")
        pr = probe_file(path)
        if pr.definitive_absence:
            # única situação que rebaixa o replay: o arquivo NÃO existe (ENOENT/ENOTDIR/não é arquivo regular)
            if kind == "video":
                self.store.set_file_ok(ref, False)  # a central será avisada no próximo ciclo (para de oferecer o vídeo)
            self._log_failure(kind, ref, "stat", "file_missing", pr)
            return Resolution(code="not_found", reason="file_missing", probe=pr)
        if not pr.ok:
            # permissão / E-S / laço: condição do AMBIENTE do serviço. Não é "sumiu": não rebaixa e a central tenta de novo (503)
            self._log_failure(kind, ref, "stat", pr.status, pr)
            return Resolution(code="io_error", reason=pr.status, probe=pr)
        rp = path.resolve()
        if not any(root in rp.parents for root in self.roots):  # defesa contra banco adulterado (symlink para fora, prefixo de pasta…)
            self._log_failure(kind, ref, "allowlist", "outside_allowed_roots", pr)
            return Resolution(code="not_found", reason="outside_allowed_roots", probe=pr)
        if kind == "video" and not row["file_ok"]:
            self.store.set_file_ok(ref, True)  # estava sinalizado como ausente mas o arquivo está bom: corrige e reenfileira a sincronização
        return Resolution(path=rp, probe=pr)

    def _resolve(self, kind: str, ref: str) -> Path | None:
        return self._resolve_ex(kind, ref).path

    def serve_one(self, wait: int = 20) -> bool:
        job = self.client.call("GET", f"/relay/next?wait={wait}", timeout=wait + 15)
        if not job:
            return False
        rid, start, end = job["requestId"], int(job["start"]), int(job["end"])
        res = self._resolve_ex(job["kind"], job["ref"])
        if res.path is None:
            self._error(rid, res.code or "not_found")
            return True
        path = res.path
        try:
            total = os.path.getsize(path)
        except OSError as e:
            return self._io_failure(rid, job, "getsize", e)
        if start < 0 or start >= total or end < start or end - start + 1 > self.max_chunk:
            self.client.call("POST", f"/relay/{rid}/error", {"code": "bad_request", "message": "intervalo inválido"})
            self.errors += 1
            return True
        end = min(end, total - 1)
        try:
            with open(path, "rb") as f:
                f.seek(start)
                data = f.read(end - start + 1)
        except OSError as e:
            return self._io_failure(rid, job, "read", e)
        self.client.request_raw("POST", f"/relay/{rid}/data", data,
                                {"Content-Type": "application/octet-stream", "X-Total-Size": str(total),
                                 "X-Chunk-Sha256": hashlib.sha256(data).hexdigest()}, timeout=60)
        self.served += 1
        return True

    def _error(self, rid: str, code: str) -> None:
        self.client.call("POST", f"/relay/{rid}/error", {"code": code, "message": _MSG.get(code, "")})
        self.errors += 1

    def _io_failure(self, rid: str, job: dict, stage: str, e: OSError) -> bool:
        """Falha ao ler um arquivo que passou no stat. Sumiu entre as duas chamadas (ENOENT/ENOTDIR) = ausência; o resto = io_error."""
        code = "not_found" if classify(e.errno) == "missing" else "io_error"
        if code == "not_found" and job["kind"] == "video":
            self.store.set_file_ok(job["ref"], False)
        self._log_failure(job["kind"], job["ref"], stage, "vanished" if code == "not_found" else "read_error", Probe("io_error", errno_name(e.errno), str(getattr(e, "filename", "") or "-")))
        self._error(rid, code)
        return True

    def run_forever(self, stop: threading.Event, wait: int = 20) -> None:  # pragma: no cover - laço de produção
        fails = 0
        while not stop.is_set():
            try:
                self.serve_one(wait)
                fails = 0
            except (CentralUnavailable, CentralRejected):
                fails += 1
                stop.wait(min(30, 0.5 * 2 ** min(fails, 6)))
            except Exception:  # noqa: BLE001 — um erro inesperado não pode matar o worker (a entrega das DUAS quadras depende dele)
                log.exception("relay_worker_unexpected_error")
                fails += 1
                stop.wait(min(30, 0.5 * 2 ** min(fails, 6)))

    def start(self, stop: threading.Event, workers: int = 2, wait: int = 20) -> list[threading.Thread]:
        ts = [threading.Thread(target=self.run_forever, args=(stop, wait), daemon=True, name=f"relay-{i}") for i in range(workers)]
        [t.start() for t in ts]
        return ts
