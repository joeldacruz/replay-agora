"""Captura contínua por câmera: FFmpeg grava segmentos MPEG-TS (-c copy, sem recodificar) num buffer circular em disco.

* UM gravador (processo FFmpeg + supervisor) por câmera; uma câmera caída/reconectando NÃO afeta as outras.
* Nome do segmento = epoch (UTC) do instante em que ele foi aberto: é a referência temporal para cortar o replay.
  Precisão: ~1 s (nome em segundos) + latência RTSP + cortes só em keyframes (GOP da câmera <= segment_s).
* Rotação apaga SOMENTE segmentos `seg_<epoch>.ts` do próprio diretório do buffer, nunca os de janelas de acionamentos pendentes.
* Diagnóstico: FPS recebido, bitrate, frames descartados, reconexões, travamentos, lacunas e duração contínua disponível.
"""
import ctypes
import os
import re
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .config import CameraCfg, redact_text, redact_url

SEG_RE = re.compile(r"^seg_(\d{9,11})\.ts$")


@dataclass(frozen=True)
class Seg:
    path: Path
    start: float
    bytes: int


@dataclass(frozen=True)
class Span:
    seg: Seg
    start: float
    end: float


def procstat(pid: int) -> dict | None:
    """CPU acumulada (s) e RSS (MB) de um processo (Linux /proc)."""
    try:
        f = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        hz = os.sysconf("SC_CLK_TCK")
        cpu = (int(f[11]) + int(f[12])) / hz
        rss = 0.0
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1]) / 1024
        return {"cpuS": cpu, "rssMb": rss}
    except (OSError, IndexError, ValueError):
        return None


class BufferIndex:
    """Índice do diretório de segmentos (reconstruído do disco: sobrevive a reinício)."""

    def __init__(self, directory: Path, nominal_s: float = 2.0, glob: str | None = None, time_source: str = "name_epoch", ffprobe: str = "ffprobe"):
        self.dir, self.nominal, self.glob, self.time_source, self.ffprobe = Path(directory), nominal_s, glob, time_source, ffprobe
        self._dur: dict[str, float] = {}

    def _files(self) -> list[Path]:
        if self.glob:
            p = Path(self.glob)
            return sorted(p.parent.glob(p.name))
        return sorted(p for p in self.dir.glob("seg_*.ts") if SEG_RE.match(p.name))

    def scan(self, open_last: bool = False) -> list[Seg]:
        """Segmentos FECHADOS. O mais novo está sendo gravado: só entra se `open_last` (gravador parado)."""
        segs: list[Seg] = []
        for p in self._files():
            try:
                st = p.stat()
            except OSError:
                continue
            if self.time_source == "mtime":
                start = st.st_mtime - self.nominal           # mtime = instante de FECHAMENTO do segmento
            else:
                m = re.search(r"(\d{9,11})", p.name)
                if not m:
                    continue
                start = float(m.group(1))
            segs.append(Seg(p, start, st.st_size))
        segs.sort(key=lambda s: s.start)
        return segs if (open_last or self.time_source == "mtime") else segs[:-1]

    def duration(self, seg: Seg) -> float:
        d = self._dur.get(str(seg.path))
        if d is None:
            try:
                out = subprocess.run([self.ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(seg.path)],
                                     capture_output=True, text=True, timeout=20).stdout.strip()
                d = float(out) if out else self.nominal
            except (ValueError, subprocess.SubprocessError, OSError):
                d = self.nominal
            self._dur[str(seg.path)] = d
        return d

    def spans(self, segs: list[Seg]) -> list[Span]:
        """Intervalo realmente coberto por cada segmento (duração medida com ffprobe, em cache)."""
        out: list[Span] = []
        for i, s in enumerate(segs):
            end = s.start + self.duration(s)
            if i + 1 < len(segs) and segs[i + 1].start < end:
                end = segs[i + 1].start                      # nomes em segundos podem sobrepor ~1 s
            out.append(Span(s, s.start, end))
        return out

    @staticmethod
    def runs(spans: list[Span], tol: float) -> list[tuple[float, float, list[Span]]]:
        """Trechos contínuos (lacunas > tol separam)."""
        runs: list[tuple[float, float, list[Span]]] = []
        for sp in spans:
            if runs and sp.start - runs[-1][1] <= tol:
                a, b, l = runs[-1]
                runs[-1] = (a, max(b, sp.end), l + [sp])
            else:
                runs.append((sp.start, sp.end, [sp]))
        return runs

    def stats(self, tol: float) -> dict:
        segs = self.scan(open_last=False)
        if not segs:
            return {"segments": 0, "availableSeconds": 0.0, "oldestAt": None, "newestAt": None, "bytes": 0, "gaps": 0, "medianSegmentS": None}
        spans = self.spans(segs)
        runs = self.runs(spans, tol)
        starts = [s.start for s in segs]
        diffs = sorted(b - a for a, b in zip(starts, starts[1:]) if b - a < 3 * self.nominal + 5)
        return {"segments": len(segs), "availableSeconds": round(runs[-1][1] - runs[-1][0], 1), "oldestAt": segs[0].start,
                "newestAt": spans[-1].end, "bytes": sum(s.bytes for s in segs), "gaps": len(runs) - 1,
                "medianSegmentS": diffs[len(diffs) // 2] if diffs else None}


def rotate(index: BufferIndex, keep_s: float, pinned: list[tuple[float, float]], now: float, margin: float = 4.0) -> int:
    """Apaga segmentos antigos do buffer (circular). NUNCA toca em janelas de acionamentos pendentes nem fora de seg_*.ts."""
    removed = 0
    for sp in index.spans(index.scan(open_last=False)):
        if sp.end >= now - keep_s:
            continue
        if any(sp.end >= a - margin and sp.start <= b + margin for a, b in pinned):
            continue
        p = sp.seg.path
        if not SEG_RE.match(p.name) or p.parent != index.dir or now - p.stat().st_mtime < 1.0:
            continue
        try:
            p.unlink()
            index._dur.pop(str(p), None)
            removed += 1
        except OSError:
            pass
    return removed


def _die_with_parent() -> None:
    """PR_SET_PDEATHSIG: se o agente morrer (kill -9, OOM), o FFmpeg de captura morre junto — sem processo órfão gravando."""
    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGKILL)
    except Exception:  # noqa: BLE001 - melhor esforço (Linux)
        pass


_RTSP_TIMEOUT_OPT: dict[str, str] = {}


def rtsp_timeout_opt(ffmpeg: str) -> str:
    """FFmpeg >= 5 usa -timeout (µs); versões antigas -stimeout."""
    if ffmpeg not in _RTSP_TIMEOUT_OPT:
        try:
            h = subprocess.run([ffmpeg, "-hide_banner", "-h", "demuxer=rtsp"], capture_output=True, text=True, timeout=10).stdout
            _RTSP_TIMEOUT_OPT[ffmpeg] = "-stimeout" if "stimeout" in h else "-timeout"
        except (OSError, subprocess.SubprocessError):
            _RTSP_TIMEOUT_OPT[ffmpeg] = "-timeout"
    return _RTSP_TIMEOUT_OPT[ffmpeg]


def build_capture_cmd(cam: CameraCfg, out_dir: Path, ffmpeg: str = "ffmpeg") -> list[str]:
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    url = cam.url
    if url.startswith(("rtsp://", "rtsps://")):
        cmd += ["-rtsp_transport", cam.transport, rtsp_timeout_opt(ffmpeg), str(int(cam.rtsp_timeout_s * 1_000_000)),
                "-fflags", "+genpts+discardcorrupt", "-i", url]
    elif url.startswith("file:"):
        cmd += ["-re", "-stream_loop", "-1", "-fflags", "+genpts", "-i", url[5:]]       # arquivo de vídeo real, em laço e tempo real
    elif url.startswith("lavfi:"):
        cmd += ["-re", "-f", "lavfi", "-i", url[6:]]
    else:
        raise ValueError("esquema de câmera não suportado")
    codec = ["-c:v", "libx264", "-preset", "ultrafast", "-g", "25", "-keyint_min", "25", "-sc_threshold", "0", "-pix_fmt", "yuv420p"] \
        if url.startswith("lavfi:") else ["-c", "copy"]
    cmd += ["-map", "0:v:0", "-map", "0:a:0?", *codec, "-f", "segment", "-segment_time", f"{cam.segment_s:g}",
            "-segment_format", "mpegts", "-reset_timestamps", "1", "-strftime", "1", str(out_dir / "seg_%s.ts"),
            "-progress", "pipe:1", "-stats_period", "1"]
    return cmd


class SegmentRecorder(threading.Thread):
    def __init__(self, court_key: str, cam: CameraCfg, buf_dir: Path, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe",
                 pinned=lambda: [], tolerance: float = 2.5, clock=time.time):
        super().__init__(daemon=True, name=f"rec-{court_key}")
        self.court_key, self.cam, self.dir, self.ffmpeg, self.clock = court_key, cam, Path(buf_dir), ffmpeg, clock
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index = BufferIndex(self.dir, cam.segment_s, ffprobe=ffprobe)
        self.pinned, self.tol = pinned, tolerance
        self.stop_flag = threading.Event()
        self.proc: subprocess.Popen | None = None
        self.state = "disabled" if not cam.enabled else "connecting"
        # métricas
        self.runs = self.reconnects = self.stalls = 0
        self.frames = self.dropped = 0
        self.fps = self.bitrate_kbps = self.speed = 0.0
        self.last_frame_at: float | None = None
        self.last_error: str | None = None
        self.started_at: float | None = None
        self._fps_hist: deque[tuple[int, float]] = deque(maxlen=5)   # (pacotes, duração) dos últimos segmentos
        self._measured: set[str] = set()
        self.declared_fps = 0.0
        self.expected_frames = 0
        self.last_out_us = -1
        self.last_progress_at: float | None = None
        self._cpu_prev: tuple[float, float] | None = None

    @property
    def alive(self) -> bool:
        return self.state in ("streaming",) and self.proc is not None and self.proc.poll() is None

    # ---- laço de supervisão ----
    def run(self) -> None:
        if not self.cam.enabled:
            return
        backoff, fails = self.cam.reconnect_min_s, 0
        last_rotate = 0.0
        while not self.stop_flag.is_set():
            ok_for = self._run_once(last_rotate_cb=lambda: self._maybe_rotate())
            fails = 0 if ok_for >= 30 else fails + 1
            backoff = self.cam.reconnect_min_s if ok_for >= 30 else min(self.cam.reconnect_max_s, backoff * 2)
            if self.stop_flag.is_set():
                break
            self.state = "down" if fails >= 3 else "reconnecting"
            self.reconnects += 1
            self.stop_flag.wait(backoff)
        self.state = "stopped"

    def _maybe_rotate(self) -> None:
        now = self.clock()
        if now - getattr(self, "_last_rot", 0) >= 5:
            self._last_rot = now
            rotate(self.index, self.cam.buffer_s, self.pinned(), now)

    def _run_once(self, last_rotate_cb) -> float:
        cmd = build_capture_cmd(self.cam, self.dir, self.ffmpeg)
        self.runs += 1
        t_start = time.monotonic()
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                         preexec_fn=_die_with_parent)
        except OSError as e:
            self.last_error = f"não foi possível iniciar o FFmpeg: {type(e).__name__}"
            return 0.0
        self.state, self.started_at = "connecting", self.clock()
        self.last_out_us = -1
        last_change = time.monotonic()
        last_out = -1
        block: dict[str, str] = {}
        reader_err: list[str] = []
        threading.Thread(target=lambda: reader_err.append(self.proc.stderr.read()[-400:]) if self.proc and self.proc.stderr else None, daemon=True).start()

        def pump():
            nonlocal block
            for line in self.proc.stdout:  # type: ignore[union-attr]
                k, _, v = line.strip().partition("=")
                block[k] = v
                if k == "progress":
                    self._on_progress(block)
                    block = {}
        threading.Thread(target=pump, daemon=True).start()
        while not self.stop_flag.is_set():
            time.sleep(0.25)
            if self.proc.poll() is not None:
                break
            if self.last_out_us != last_out:
                last_out, last_change = self.last_out_us, time.monotonic()
            elif time.monotonic() - last_change > self.cam.stall_s:
                self.stalls += 1
                self.last_error = f"sem dados há {self.cam.stall_s:g}s (travamento) — reconectando"
                self._kill()
                break
            self._measure_segments()
            last_rotate_cb()
        else:
            self._stop_gracefully()
            return time.monotonic() - t_start
        time.sleep(0.1)
        if reader_err and reader_err[0] and self.proc.returncode not in (0, None):
            self.last_error = redact_text(reader_err[0].strip().splitlines()[-1] if reader_err[0].strip() else f"ffmpeg saiu com {self.proc.returncode}")
        elif not self.last_error:
            self.last_error = f"ffmpeg encerrou (código {self.proc.returncode})"
        return time.monotonic() - t_start

    def _on_progress(self, b: dict) -> None:
        """Liveness pelo avanço de out_time (o muxer de segmentos não informa frames em stream copy)."""
        try:
            out = int(b.get("out_time_us", "-1") or -1)
        except ValueError:
            return
        if out > self.last_out_us and out > 0:
            self.last_out_us = out
            self.last_progress_at = self.clock()
            self.last_frame_at = self.last_progress_at
            self.state = "streaming"
        sp = b.get("speed", "").replace("x", "").strip()
        try:
            self.speed = float(sp) if sp not in ("", "N/A") else self.speed
        except ValueError:
            pass

    def _measure_segments(self) -> None:
        """FPS REAL recebido, perdas e bitrate por segmento fechado (ffprobe -count_packets; segmentos de ~1-2 s)."""
        try:
            segs = self.index.scan(open_last=False)
        except OSError:
            return
        for sg in segs[-3:]:
            key = str(sg.path)
            if key in self._measured:
                continue
            self._measured.add(key)
            if len(self._measured) > 500:
                self._measured = set(list(self._measured)[-200:])
            try:
                out = subprocess.run([self.index.ffprobe, "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                                      "stream=nb_read_packets,avg_frame_rate:format=duration", "-of", "default=nw=1", str(sg.path)],
                                     capture_output=True, text=True, timeout=20).stdout
                kv = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
                pk, dur = int(kv.get("nb_read_packets", 0)), float(kv.get("duration", 0) or 0)
                num, _, den = kv.get("avg_frame_rate", "0/1").partition("/")
                decl = float(num) / float(den) if float(den or 0) else 0.0
            except (ValueError, subprocess.SubprocessError, OSError):
                continue
            if dur <= 0:
                continue
            self.frames += pk
            self._fps_hist.append((pk, dur))
            self.fps = round(sum(p for p, _ in self._fps_hist) / sum(d for _, d in self._fps_hist), 2)
            self.bitrate_kbps = round(sg.bytes * 8 / dur / 1000, 1)
            if decl > 0:
                self.declared_fps = round(decl, 2)
                self.expected_frames += round(decl * dur)
                self.dropped = max(0, self.expected_frames - self.frames)

    def _stop_gracefully(self) -> None:
        p = self.proc
        if not p or p.poll() is not None:
            return
        try:
            p.stdin.write("q\n")  # type: ignore[union-attr]
            p.stdin.flush()  # type: ignore[union-attr]
            p.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            self._kill()

    def _kill(self) -> None:
        p = self.proc
        if p and p.poll() is None:
            p.kill()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    def stop(self) -> None:
        self.stop_flag.set()
        self._stop_gracefully()
        if self.is_alive():
            self.join(timeout=10)

    # ---- diagnóstico ----
    def diagnostics(self) -> dict:
        now = self.clock()
        res = procstat(self.proc.pid) if self.proc and self.proc.poll() is None else None
        cpu_pct = None
        if res:
            if self._cpu_prev:
                dt = time.monotonic() - self._cpu_prev[0]
                cpu_pct = round(100 * (res["cpuS"] - self._cpu_prev[1]) / dt, 1) if dt > 0.5 else None
            self._cpu_prev = (time.monotonic(), res["cpuS"])
        st = self.index.stats(self.tol)
        warn = None
        if st["medianSegmentS"] and st["medianSegmentS"] > 2 * self.cam.segment_s + 1:
            warn = f"segmentos de ~{st['medianSegmentS']:.0f}s: o GOP da câmera é maior que segment_s ({self.cam.segment_s:g}s)"
        return {"state": self.state, "url": redact_url(self.cam.url), "fps": self.fps, "bitrateKbps": self.bitrate_kbps, "speed": self.speed,
                "frames": self.frames, "droppedFrames": self.dropped, "reconnects": self.reconnects, "stalls": self.stalls, "runs": self.runs,
                "declaredFps": self.declared_fps, "lossPct": round(100 * self.dropped / self.expected_frames, 1) if self.expected_frames else 0.0,
                "lastFrameAgeS": round(now - self.last_frame_at, 1) if self.last_frame_at else None,
                "lastError": self.last_error, "pid": self.proc.pid if res else None, "cpuPct": cpu_pct, "rssMb": round(res["rssMb"], 1) if res else None,
                "buffer": {**st, "targetSeconds": self.cam.buffer_s}, "warning": warn}
