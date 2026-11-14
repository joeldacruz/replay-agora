"""Mídia de amostra 100% sintética (FFmpeg lavfi + Pillow): sem direitos de terceiros, sem pessoas reais."""
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from .config import settings


def _run(args: list[str]) -> None:
    subprocess.run([settings.ffmpeg_bin, "-y", "-v", "error", "-nostdin", *args], check=True, timeout=120)


def make_video(path: Path, seconds: float = 4, with_audio: bool = True, tone: int = 440, size: str = "640x360",
               label: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration={seconds}"]
    if with_audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency={tone}:sample_rate=44100:duration={seconds}"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast"]
    args += ["-c:a", "aac", "-b:a", "96k"] if with_audio else ["-an"]
    _run([*args, "-movflags", "+faststart", str(path)])
    return path


def make_tone(path: Path, freq: int = 880, seconds: float = 6, amplitude: float = 0.5) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    codec = ["-c:a", "libmp3lame", "-b:a", "128k"] if path.suffix == ".mp3" else []
    _run(["-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=44100:duration={seconds}", "-af", f"volume={amplitude}",
          *codec, str(path)])
    return path


def make_logo(path: Path, color: tuple[int, int, int] = (255, 0, 255), text: str = "LOGO", size=(400, 160)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGBA", size, color + (255,))
    d = ImageDraw.Draw(im)
    d.rectangle([6, 6, size[0] - 7, size[1] - 7], outline=(255, 255, 255, 255), width=6)
    d.text((size[0] // 2 - 4 * len(text), size[1] // 2 - 6), text, fill=(255, 255, 255, 255))
    im.save(path, "PNG")
    return path
