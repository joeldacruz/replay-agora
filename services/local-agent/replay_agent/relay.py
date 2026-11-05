"""Servidor de trechos do agente: busca pedidos da central por conexão de SAÍDA (long-poll) e responde com bytes do disco local.

O agente NÃO abre porta nem escuta na internet. Só serve arquivos que existem no próprio banco local (por local_id/job_id),
dentro das pastas permitidas; qualquer caminho vindo da rede é ignorado (nunca há caminho no pedido).
"""
import hashlib
import os
import re
import threading
import time
from pathlib import Path

from .client import CentralClient, CentralRejected, CentralUnavailable
from .store import LocalStore

_ID = re.compile(r"^[A-Za-z0-9_.:\-]{8,120}$")


class RelayServer:
    def __init__(self, client: CentralClient, store: LocalStore, allowed_roots: list[Path], max_chunk: int = 4 * 1024 * 1024):
        self.client, self.store, self.max_chunk = client, store, max_chunk
        self.roots = [Path(r).resolve() for r in allowed_roots]
        self.served = self.errors = 0

    def _resolve(self, kind: str, ref: str) -> Path | None:
        if not _ID.match(ref):
            return None
        if kind == "video":
            row = self.store.get(ref)
            path = Path(row["path"]) if row and not row["deleted"] else None
        elif kind == "render":
            row = self.store.render(ref)
            path = Path(row["path"]) if row and row["state"] == "done" else None
        else:
            return None
        if not path or not path.is_file():
            if kind == "video" and row is not None:
                self.store.set_file_ok(ref, False)  # a central será avisada no próximo ciclo (para de oferecer o vídeo)
            return None
        rp = path.resolve()
        return rp if any(root in rp.parents for root in self.roots) else None  # defesa contra banco adulterado

    def serve_one(self, wait: int = 20) -> bool:
        job = self.client.call("GET", f"/relay/next?wait={wait}", timeout=wait + 15)
        if not job:
            return False
        rid, start, end = job["requestId"], int(job["start"]), int(job["end"])
        path = self._resolve(job["kind"], job["ref"])
        if path is None:
            self.client.call("POST", f"/relay/{rid}/error", {"code": "not_found", "message": "arquivo não encontrado na arena"})
            self.errors += 1
            return True
        total = os.path.getsize(path)
        if start < 0 or start >= total or end < start or end - start + 1 > self.max_chunk:
            self.client.call("POST", f"/relay/{rid}/error", {"code": "bad_request", "message": "intervalo inválido"})
            self.errors += 1
            return True
        end = min(end, total - 1)
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(end - start + 1)
        self.client.request_raw("POST", f"/relay/{rid}/data", data,
                                {"Content-Type": "application/octet-stream", "X-Total-Size": str(total),
                                 "X-Chunk-Sha256": hashlib.sha256(data).hexdigest()}, timeout=60)
        self.served += 1
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

    def start(self, stop: threading.Event, workers: int = 2, wait: int = 20) -> list[threading.Thread]:
        ts = [threading.Thread(target=self.run_forever, args=(stop, wait), daemon=True, name=f"relay-{i}") for i in range(workers)]
        [t.start() for t in ts]
        return ts
