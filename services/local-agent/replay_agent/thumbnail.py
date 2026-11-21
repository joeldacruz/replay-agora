"""Gera somente imagem JPEG de prévia a partir do MP4 já consolidado na arena.

Não modifica o MP4, não interfere no buffer/RTSP e não depende da API central.
A rotação do replay é aplicada na apresentação do jogador, não no JPEG de origem.
"""
import subprocess
from pathlib import Path


class ThumbnailUnavailable(Exception):
    pass


def extract_thumbnail(video_path: str, ffmpeg_bin: str = "ffmpeg", seconds: float = 1.0) -> bytes:
    path = Path(video_path)
    if not path.is_file() or path.suffix.lower() != ".mp4":
        raise ThumbnailUnavailable("MP4 local ausente")
    if not (0 <= seconds <= 10):
        raise ValueError("ponto de captura inválido")
    cmd = [
        ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-noautorotate", "-ss", str(seconds), "-i", str(path),
        "-map", "0:v:0", "-frames:v", "1", "-an", "-sn",
        "-vf", "scale=480:270:force_original_aspect_ratio=decrease",
        "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"
    ]
    try:
        completed = subprocess.run(cmd, capture_output=True, timeout=12, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ThumbnailUnavailable(type(exc).__name__) from exc
    data = completed.stdout
    if completed.returncode or not (64 <= len(data) <= 256 * 1024):
        raise ThumbnailUnavailable("falha de extração ou tamanho")
    if not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9"):
        raise ThumbnailUnavailable("JPEG inválido")
    return data
