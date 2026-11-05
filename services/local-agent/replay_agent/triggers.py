"""Acionamento por botoeira: protocolo serial REPLAY (Arduino, 9600 8N1, uma linha por acionamento) e roteamento por quadra.

Garantias:
 * cada linha recebida vira UMA linha em `trigger_log` (horário + origem) ANTES de qualquer processamento;
 * o job usa chave idempotente (quadra + ms): nem repique nem reprocessamento após queda gera replay duplicado;
 * debounce por botoeira (repique mecânico) e cooldown por quadra (configuráveis); botoeiras desconhecidas são só registradas;
 * lixo/ruído na linha serial, linhas longas e desconexão USB nunca derrubam o leitor.
"""
import errno
import fcntl
import os
import re
import select
import termios
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from .engine import Engine
from .store import LocalStore

_BAUD = {1200: termios.B1200, 2400: termios.B2400, 4800: termios.B4800, 9600: termios.B9600, 19200: termios.B19200,
         38400: termios.B38400, 57600: termios.B57600, 115200: termios.B115200}
TIOCEXCL = getattr(termios, "TIOCEXCL", 0x540C)
LINE_OK = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
MAX_LINE = 64


class SerialTrigger:
    def __init__(self, name: str, port: str, baud: int = 9600, reopen_s: float = 1.0, exclusive: bool = True):
        if baud not in _BAUD:
            raise ValueError("baud não suportado")
        self.name, self.port, self.baud, self.reopen_s, self.exclusive = name, port, baud, reopen_s, exclusive
        self.stop_flag = threading.Event()
        self.thread: threading.Thread | None = None
        self.state = "stopped"
        self.lines = self.garbled = self.overflow = self.reconnects = 0
        self.last_line_at: float | None = None
        self.last_error: str | None = None

    def _open(self) -> int:
        fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            attr = termios.tcgetattr(fd)
            attr[0] = 0                                    # iflag: sem tradução
            attr[1] = 0                                    # oflag
            attr[2] = termios.CS8 | termios.CREAD | termios.CLOCAL      # 8N1
            attr[3] = 0                                    # lflag: modo raw
            attr[4] = attr[5] = _BAUD[self.baud]
            attr[6][termios.VMIN], attr[6][termios.VTIME] = 0, 0
            termios.tcsetattr(fd, termios.TCSANOW, attr)
            termios.tcflush(fd, termios.TCIFLUSH)          # descarta lixo acumulado enquanto estava fechado
            if self.exclusive:
                fcntl.ioctl(fd, TIOCEXCL)                  # outro processo (ex.: leitor.py) não consegue abrir ao mesmo tempo
        except Exception:
            os.close(fd)
            raise
        return fd

    def start(self, emit: Callable[[str, str, float], None]) -> None:
        self.thread = threading.Thread(target=self._run, args=(emit,), daemon=True, name=f"serial-{self.name}")
        self.thread.start()

    def _run(self, emit) -> None:
        backoff = self.reopen_s
        while not self.stop_flag.is_set():
            try:
                fd = self._open()
            except OSError as e:
                self.state = "busy" if e.errno == errno.EBUSY else "disconnected"
                self.last_error = f"{e.strerror or 'erro'} ({self.port.rsplit('/', 1)[-1]})"
                self.stop_flag.wait(backoff)
                backoff = min(10.0, backoff * 1.5)
                continue
            backoff = self.reopen_s
            self.state, self.last_error = "connected", None
            try:
                self._read_loop(fd, emit)
            finally:
                os.close(fd)
            if not self.stop_flag.is_set():
                self.state = "disconnected"
                self.reconnects += 1

    def _read_loop(self, fd: int, emit) -> None:
        buf = b""
        discarding = False
        while not self.stop_flag.is_set():
            try:
                r, _, _ = select.select([fd], [], [], 0.25)
                if not r:
                    continue
                chunk = os.read(fd, 256)
            except (OSError, ValueError) as e:                       # EIO: USB removido
                self.last_error = f"leitura falhou ({getattr(e, 'strerror', 'erro')})"
                return
            if not chunk:                                            # EOF (pty fechado / dispositivo sumiu)
                self.last_error = "porta fechada pelo dispositivo"
                time.sleep(0.05)
                return
            ts = time.time()
            buf += chunk
            while True:
                m = re.search(rb"[\r\n]", buf)
                if not m:
                    if len(buf) > MAX_LINE:                          # sem terminador: ruído; descarta até o próximo
                        self.overflow += 1
                        buf, discarding = b"", True
                    break
                line, buf = buf[:m.start()], buf[m.end():]
                if discarding:
                    discarding = False
                    continue
                if not line.strip():
                    continue
                try:
                    text = line.decode("ascii").strip()
                except UnicodeDecodeError:
                    self.garbled += 1
                    continue
                if len(text) > 40 or not LINE_OK.match(text):
                    self.garbled += 1
                    continue
                self.lines += 1
                self.last_line_at = ts
                emit(text, f"serial:{self.name}", ts)

    def stop(self) -> None:
        self.stop_flag.set()
        if self.thread:
            self.thread.join(timeout=3)
        self.state = "stopped"

    def diagnostics(self) -> dict:
        return {"kind": "serial", "name": self.name, "port": self.port.rsplit("/", 1)[-1], "baud": self.baud, "state": self.state,
                "lines": self.lines, "garbled": self.garbled, "overflow": self.overflow, "reconnects": self.reconnects,
                "lastLineAt": self.last_line_at, "lastError": self.last_error}


class TriggerRouter:
    """Mensagem da botoeira -> quadra -> job. Registra tudo em trigger_log (horário e origem)."""

    def __init__(self, engine: Engine, store: LocalStore, buttons: dict[str, str], debounce: dict[str, float] | None = None):
        self.engine, self.store, self.buttons = engine, store, buttons
        self.debounce = debounce or {}               # quadra -> segundos
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.counts: dict[str, int] = {}

    def handle(self, message: str, source: str, ts: float | None = None) -> dict:
        ts = ts if ts is not None else time.time()
        court = self.buttons.get(message)
        tid = self.store.log_trigger(ts, source, message, court)            # 1) registra ANTES de qualquer decisão
        if court is None:
            return self._done(tid, "unknown_button", None)
        with self._lock:
            last = self._last.get(message)
            if last is not None and 0 <= ts - last < self.debounce.get(court, 0.3):
                return self._done(tid, "debounce", None)
            self._last[message] = ts
        return self._enqueue(tid, court, ts, source, message)

    def _enqueue(self, tid: int, court: str, ts: float, source: str, button: str | None) -> dict:
        at = datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)
        r = self.engine.trigger(court, at, source=source, button=button)       # 2) job persistido (idempotente por quadra+ms)
        outcome = "accepted" if r["accepted"] else r["reason"]
        return self._done(tid, outcome, r.get("localId"), court)

    def _done(self, tid: int, outcome: str, job_id: str | None, court: str | None = None) -> dict:
        self.store.set_trigger_outcome(tid, outcome, job_id)
        self.counts[outcome] = self.counts.get(outcome, 0) + 1
        return {"outcome": outcome, "jobId": job_id, "court": court}

    def manual(self, court_key: str, who: str) -> dict:
        """Teste manual (painel web / CLI): passa pelo mesmo caminho, com origem identificada e sem debounce de botoeira."""
        ts = time.time()
        tid = self.store.log_trigger(ts, f"manual:{who}", None, court_key)
        if court_key not in self.engine.workers:
            return self._done(tid, "unknown_court", None)
        return self._enqueue(tid, court_key, ts, f"manual:{who}", None)

    def recover_pending(self) -> int:
        """Após queda entre o registro e o job: reprocessa linhas sem desfecho. Idempotente (mesma chave => 'duplicate')."""
        n = 0
        for row in self.store.pending_triggers():
            self._enqueue(row["id"], row["court_key"], row["ts"], row["source"], row["button"])
            n += 1
        return n
