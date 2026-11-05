"""Renderizadores SIMULADOS (sem câmera). `stub` grava bytes sintéticos (testes rápidos); `ffmpeg` gera MP4 real de teste."""
import errno
import subprocess
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


class CameraDown(Exception):
    """Câmera simulada sem sinal."""


@dataclass
class Rendered:
    duration: float
    width: int
    height: int
    has_audio: bool
    codec: str
    bytes: int


class SimRenderer:
    def __init__(self, mode: str = "stub", delay_s: float = 0.0, size_bytes: int = 300_000, clip_seconds: float = 3.0,
                 ffmpeg_bin: str = "ffmpeg"):
        assert mode in ("stub", "ffmpeg")
        self.mode, self.delay_s, self.size_bytes, self.clip_seconds, self.ffmpeg_bin = mode, delay_s, size_bytes, clip_seconds, ffmpeg_bin
        self.camera_up: dict[str, bool] = defaultdict(lambda: True)
        self.delay_by_court: dict[str, float] = {}
        self.disk_full = threading.Event()  # simula ENOSPC na escrita
        self._active = 0
        self._lock = threading.Lock()
        self.max_concurrent = 0

    def render(self, court_key: str, dest: Path, pre: int, post: int) -> Rendered:
        if not self.camera_up[court_key]:
            raise CameraDown(court_key)
        with self._lock:
            self._active += 1
            self.max_concurrent = max(self.max_concurrent, self._active)
        try:
            if self.disk_full.is_set():
                raise OSError(errno.ENOSPC, "No space left on device (simulado)")
            time.sleep(self.delay_by_court.get(court_key, self.delay_s))
            dur = min(float(pre + post), self.clip_seconds)
            if self.mode == "stub":
                dest.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * self.size_bytes)  # NÃO é um MP4 reproduzível
                return Rendered(dur, 1280, 720, True, "h264", dest.stat().st_size)
            cmd = [self.ffmpeg_bin, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=15:duration={dur}",
                   "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}", "-c:v", "libx264", "-preset", "ultrafast",
                   "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(dest)]
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
            return Rendered(dur, 320, 180, True, "h264", dest.stat().st_size)
        finally:
            with self._lock:
                self._active -= 1
