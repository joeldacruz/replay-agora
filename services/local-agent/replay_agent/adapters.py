"""Contratos (portas) entre o agente e o código de captura/acionamento/render, e adaptadores para o código LEGADO.

O código real (`/srv/replayagora/.../leitor.py`, `buffer.py`, `replay.py`) NÃO foi inspecionado nem importado. Os adaptadores
abaixo não dependem dele: usam comandos externos configuráveis, leitura de log e diretório de segmentos em modo somente leitura.
O contrato real deve ser levantado com `scripts/inventory_legacy.py` (somente leitura) antes da primeira homologação.
"""
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from .config import redact_text
from .render import Rendered


class Unrecoverable(Exception):
    """O trecho pedido NÃO pode ser reconstruído (buffer expirou, câmera caiu...). Não gera replay incorreto."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason


class NotReady(Exception):
    """Ainda falta gravar o pós-roll; tentar de novo em `retry_at` (epoch) sem gastar tentativa."""

    def __init__(self, retry_at: float):
        super().__init__(f"aguardando pós-roll até {retry_at}")
        self.retry_at = retry_at


class ClipRenderer(Protocol):
    needs_buffer: bool
    size_bytes: int

    def ready_at(self, court_key: str, at_epoch: float, post: float) -> float | None: ...
    def render(self, court_key: str, dest: Path, pre: int, post: int, at: datetime | None = None) -> Rendered: ...


class TriggerSource(Protocol):
    def start(self, emit: Callable[[str, str, float], None]) -> None: ...   # emit(mensagem, origem, epoch)
    def stop(self) -> None: ...
    def diagnostics(self) -> dict: ...


def utc_epoch(dt: datetime) -> float:
    """O agente guarda instantes UTC ingênuos; esta é a única conversão para epoch."""
    return dt.replace(tzinfo=timezone.utc).timestamp()


def probe_clip(path: Path, ffprobe: str = "ffprobe") -> Rendered:
    r = subprocess.run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError("saída ilegível")
    import json
    d = json.loads(r.stdout)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    if not v:
        raise RuntimeError("saída sem vídeo")
    has_a = any(s["codec_type"] == "audio" for s in d["streams"])
    return Rendered(float(d["format"]["duration"]), int(v["width"]), int(v["height"]), has_a, v["codec_name"], path.stat().st_size)


class ExternalCommandRenderer:
    """Adaptador para um `replay.py` legado (ou qualquer comando) que gere o MP4 a partir de {court,t0,pre,post}.

    Executa SEM shell, com tempo limite e ambiente mínimo; o arquivo de saída é validado com ffprobe antes de ser aceito.
    Nunca altera o código legado: só o chama como um processo externo, com argumentos definidos na configuração.
    """
    needs_buffer = False
    size_bytes = 0

    def __init__(self, template: list[str], timeout_s: int = 120, ffprobe: str = "ffprobe", nice: int = 10):
        if not any("{out}" in a for a in template):
            raise ValueError("o modelo precisa conter {out}")
        self.template, self.timeout_s, self.ffprobe, self.nice = template, timeout_s, ffprobe, nice

    def ready_at(self, court_key: str, at_epoch: float, post: float) -> float | None:
        return at_epoch + post + 1.0  # o legado grava o pós-roll por conta própria

    def render(self, court_key: str, dest: Path, pre: int, post: int, at: datetime | None = None) -> Rendered:
        t0 = utc_epoch(at) if at else 0.0
        iso = at.replace(tzinfo=timezone.utc).isoformat() if at else ""
        mapping = {"court": court_key, "t0": f"{t0:.3f}", "t0_iso": iso, "pre": str(pre), "post": str(post), "out": str(dest)}
        cmd = [re.sub(r"\{(\w+)\}", lambda m: mapping.get(m.group(1), m.group(0)), a) for a in self.template]
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "HOME")}
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_s, env=env,
                               preexec_fn=(lambda: os.nice(self.nice)) if self.nice else None)
        except subprocess.TimeoutExpired as e:
            raise TimeoutError("comando externo excedeu o tempo") from e
        if p.returncode != 0:
            raise RuntimeError("comando externo falhou: " + redact_text((p.stderr or "")[-160:]).strip())
        if not dest.is_file() or dest.stat().st_size == 0:
            raise Unrecoverable("external_no_output", "o comando não gerou o arquivo de saída")
        return probe_clip(dest, self.ffprobe)


class LogTailTrigger:
    """MODO SOMBRA: lê o log do `leitor.py` legado (arquivo ou `journalctl -f`) e emite os acionamentos que ele registra.
    Não abre a porta serial nem altera o legado: permite validar câmera/buffer/relay com a botoeira ainda sob o leitor antigo."""

    def __init__(self, name: str, regex: str, log_file: str | None = None, journal_unit: str | None = None, poll_s: float = 0.1):
        self.name, self.rx, self.log_file, self.unit, self.poll_s = name, re.compile(regex), log_file, journal_unit, poll_s
        self.stop_flag = threading.Event()
        self.thread: threading.Thread | None = None
        self.proc: subprocess.Popen | None = None
        self.lines = self.matches = 0
        self.last_line_at: float | None = None
        self.state = "stopped"

    def start(self, emit) -> None:
        self.thread = threading.Thread(target=self._run, args=(emit,), daemon=True, name=f"logtail-{self.name}")
        self.thread.start()

    def _handle(self, line: str, emit) -> None:
        import time
        self.lines += 1
        m = self.rx.search(line)
        if m:
            self.matches += 1
            self.last_line_at = time.time()
            emit(m.group(1), f"log:{self.name}", self.last_line_at)

    def _run(self, emit) -> None:
        self.state = "running"
        if self.unit:
            cmd = [shutil.which("journalctl") or "journalctl", "-u", self.unit, "-f", "-n", "0", "-o", "cat"]  # somente leitura
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            for line in self.proc.stdout:  # type: ignore[union-attr]
                if self.stop_flag.is_set():
                    break
                self._handle(line, emit)
            return
        path = Path(self.log_file or "")
        pos = path.stat().st_size if path.exists() else 0       # só linhas NOVAS (não reproduz o histórico)
        while not self.stop_flag.is_set():
            try:
                size = path.stat().st_size
                if size < pos:
                    pos = 0                                       # rotação de log
                if size > pos:
                    with open(path, "rb") as f:
                        f.seek(pos)
                        data = f.read()
                    cut = data.rfind(b"\n") + 1            # só linhas COMPLETAS; o resto espera o próximo ciclo
                    for line in data[:cut].decode("utf-8", "replace").splitlines():
                        self._handle(line, emit)
                    pos += cut
                self.state = "running"
            except OSError:
                self.state = "log_unavailable"
            self.stop_flag.wait(self.poll_s)

    def stop(self) -> None:
        self.stop_flag.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        if self.thread:
            self.thread.join(timeout=3)
        self.state = "stopped"

    def diagnostics(self) -> dict:
        return {"kind": "log_tail", "state": self.state, "lines": self.lines, "matches": self.matches, "lastLineAt": self.last_line_at}
