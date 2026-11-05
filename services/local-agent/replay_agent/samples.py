"""Vídeos de teste com GOP controlado (cortes por keyframe) — mídia sintética, sem direitos de terceiros."""
import subprocess
from pathlib import Path


def make_gop_video(path: Path, seconds: float = 6, size: str = "320x180", fps: int = 25, gop: int = 25, audio: bool = True,
                   bitrate_k: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={seconds}"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0", "-pix_fmt", "yuv420p"]
    if bitrate_k:   # taxa alvo (aproxima o fluxo de uma câmera IP)
        args += ["-b:v", f"{bitrate_k}k", "-maxrate", f"{bitrate_k}k", "-bufsize", f"{2 * bitrate_k}k"]
    args += ["-c:a", "aac", "-b:a", "64k"] if audio else ["-an"]
    subprocess.run([*args, str(path)], check=True, timeout=120)
    return path
