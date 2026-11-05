"""Controle de recursos do computador da arena.

Princípios: (1) a CAPTURA contínua tem prioridade — render/corte rodam com `nice` e PAUSAM enquanto alguma câmera atrasa;
(2) no máximo N FFmpeg de render/corte ao mesmo tempo (a captura não conta); (3) VAAPI/QSV só se detectados e solicitados,
com recuo automático para CPU na primeira falha; (4) tudo medido (tempo de espera/geração) e exposto no diagnóstico.
"""
import os
import re
import statistics
import subprocess
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HwProfile:
    name: str                      # vaapi | qsv
    device: str = "/dev/dri/renderD128"

    def input_args(self) -> list[str]:
        if self.name == "vaapi":
            return ["-vaapi_device", self.device]
        return ["-init_hw_device", "qsv=hw", "-filter_hw_device", "hw"]

    def upload(self) -> str:
        return "format=nv12,hwupload" if self.name == "vaapi" else "format=nv12,hwupload=extra_hw_frames=64"

    def encoder(self) -> list[str]:
        if self.name == "vaapi":
            return ["-c:v", "h264_vaapi", "-qp", "23"]
        return ["-c:v", "h264_qsv", "-global_quality", "23", "-look_ahead", "0"]


def detect_hw(ffmpeg: str = "ffmpeg", device: str = "/dev/dri/renderD128") -> dict:
    """Só detecta (não testa codificação real). Retorna o que está compilado no FFmpeg e se há dispositivo DRM."""
    out = {"vaapi": False, "qsv": False, "device": device, "deviceExists": Path(device).exists(), "renderNodes": []}
    try:
        enc = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10).stdout
        out["vaapi"] = "h264_vaapi" in enc and out["deviceExists"]
        out["qsv"] = "h264_qsv" in enc and out["deviceExists"]
    except (OSError, subprocess.SubprocessError):
        pass
    d = Path("/dev/dri")
    out["renderNodes"] = sorted(p.name for p in d.glob("renderD*")) if d.exists() else []
    return out


def choose_hw(setting: str, detected: dict, device: str) -> HwProfile | None:
    if setting == "none":
        return None
    order = ["vaapi", "qsv"] if setting == "auto" else [setting]
    for n in order:
        if detected.get(n):
            return HwProfile(n, device)
    return None


class ResourceGovernor:
    def __init__(self, max_ffmpeg: int = 1, hwaccel: str = "none", device: str = "/dev/dri/renderD128", render_nice: int = 10,
                 recorders: dict | None = None, pause_when_lagging: bool = True, ffmpeg: str = "ffmpeg", detected: dict | None = None,
                 lag_speed: float = 0.9, max_pause_s: float = 120.0, clock=time.monotonic):
        self.recorders = recorders if recorders is not None else {}
        self.ffmpeg, self.device, self.render_nice, self.pause_when_lagging = ffmpeg, device, render_nice, pause_when_lagging
        self.lag_speed, self.max_pause_s, self.clock = lag_speed, max_pause_s, clock
        self._cv = threading.Condition()
        self.max_ffmpeg, self.active, self.waiting = max(1, max_ffmpeg), 0, 0
        self.detected = detected if detected is not None else detect_hw(ffmpeg, device)
        self.hw_setting, self.hw_failed, self.hw_failure_reason = hwaccel, False, None
        self.waits: deque[float] = deque(maxlen=200)
        self.durations: deque[float] = deque(maxlen=200)
        self.paused_for_capture = self.forced_runs = self.runs = 0
        self.paused_now = False

    # ---- ajuste dinâmico (config central por arena) ----
    def apply(self, max_ffmpeg: int | None = None, hwaccel: str | None = None, render_nice: int | None = None) -> None:
        with self._cv:
            if max_ffmpeg is not None:
                self.max_ffmpeg = max(1, min(int(max_ffmpeg), os.cpu_count() or 1))
            if hwaccel is not None and hwaccel != self.hw_setting:
                self.hw_setting, self.hw_failed, self.hw_failure_reason = hwaccel, False, None   # nova escolha => tenta de novo
            if render_nice is not None:
                self.render_nice = max(0, min(19, int(render_nice)))
            self._cv.notify_all()

    def hw_profile(self) -> HwProfile | None:
        return None if self.hw_failed else choose_hw(self.hw_setting, self.detected, self.device)

    def report_hw_failure(self, reason: str) -> None:
        self.hw_failed, self.hw_failure_reason = True, reason[:160]

    # ---- prioridade da captura ----
    def capture_lagging(self) -> bool:
        for r in self.recorders.values():
            if r.state == "streaming" and 0 < r.speed < self.lag_speed:
                return True                                    # o FFmpeg de captura não está acompanhando o tempo real
        return False

    @contextmanager
    def slot(self):
        t0 = self.clock()
        with self._cv:
            self.waiting += 1
            try:
                while True:
                    lagging = self.pause_when_lagging and self.capture_lagging()
                    if self.active < self.max_ffmpeg and not lagging:
                        break
                    if lagging:
                        self.paused_now = True
                        if self.clock() - t0 > self.max_pause_s:      # evita fome eterna: depois do limite, segue (e registra)
                            if self.active < self.max_ffmpeg:
                                self.forced_runs += 1
                                break
                    self._cv.wait(0.2)
                self.active += 1
                if self.paused_now:
                    self.paused_for_capture += 1
                self.paused_now = False
            finally:
                self.waiting -= 1
        start = self.clock()
        self.waits.append(start - t0)
        try:
            yield self
        finally:
            self.durations.append(self.clock() - start)
            self.runs += 1
            with self._cv:
                self.active -= 1
                self._cv.notify_all()

    def stats(self) -> dict:
        d, w = list(self.durations), list(self.waits)
        p95 = sorted(d)[max(0, int(len(d) * 0.95) - 1)] if d else None
        prof = self.hw_profile()
        return {"maxFfmpeg": self.max_ffmpeg, "activeFfmpeg": self.active, "waiting": self.waiting, "runs": self.runs,
                "avgRunS": round(statistics.fmean(d), 2) if d else None, "p95RunS": round(p95, 2) if p95 is not None else None,
                "avgWaitS": round(statistics.fmean(w), 2) if w else None, "pausedForCapture": self.paused_for_capture,
                "pausedNow": self.paused_now or (self.waiting > 0 and self.capture_lagging()), "forcedRuns": self.forced_runs, "renderNice": self.render_nice,
                "hw": {"setting": self.hw_setting, "active": prof.name if prof else "cpu", "failed": self.hw_failed,
                       "reason": self.hw_failure_reason, "detected": {k: self.detected.get(k) for k in ("vaapi", "qsv", "deviceExists")}}}


_HWERR = re.compile(r"(vaapi|qsv|hwupload|hw device|libva|cannot load|no device|device creation)", re.I)


def looks_like_hw_failure(stderr: str) -> bool:
    return bool(_HWERR.search(stderr or ""))
