"""ffprobe / miniaturas. Sempre via lista de argumentos (sem shell)."""
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import settings


class MediaError(Exception):
    pass


@dataclass
class VideoInfo:
    duration: float
    width: int
    height: int
    video_codec: str
    has_audio: bool
    audio_codec: str | None
    format_name: str
    fps: float


def probe(path: Path) -> dict:
    try:
        out = subprocess.run(
            [settings.ffprobe_bin, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
        return json.loads(out)
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError) as e:
        raise MediaError("arquivo de mídia inválido ou ilegível") from e


def _fps(s: dict) -> float:
    try:
        a, b = s.get("avg_frame_rate", "0/1").split("/")
        return float(a) / float(b) if float(b) else 0.0
    except Exception:
        return 0.0


def video_info(path: Path) -> VideoInfo:
    d = probe(path)
    streams = d.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1), None)
    if not v:
        raise MediaError("arquivo sem faixa de vídeo")
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = float(d.get("format", {}).get("duration") or v.get("duration") or 0)
    if dur <= 0:
        raise MediaError("duração do vídeo indeterminada")
    return VideoInfo(dur, int(v.get("width", 0)), int(v.get("height", 0)), v.get("codec_name", ""),
                     a is not None, a.get("codec_name") if a else None,
                     d.get("format", {}).get("format_name", ""), _fps(v))


def audio_duration(path: Path) -> float:
    d = probe(path)
    if not any(s.get("codec_type") == "audio" for s in d.get("streams", [])):
        raise MediaError("arquivo sem faixa de áudio")
    if any(s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1 for s in d.get("streams", [])):
        raise MediaError("arquivo de áudio não pode conter vídeo")
    dur = float(d.get("format", {}).get("duration") or 0)
    if dur <= 0:
        raise MediaError("duração do áudio indeterminada")
    return dur


def make_thumbnail(video: Path, dest: Path, at: float) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [settings.ffmpeg_bin, "-y", "-v", "error", "-ss", f"{max(at, 0):.2f}", "-i", str(video),
         "-frames:v", "1", "-vf", "scale='min(640,iw)':-2", "-q:v", "4", str(dest)],
        capture_output=True, timeout=60,
    )
    return r.returncode == 0 and dest.exists()
