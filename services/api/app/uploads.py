"""Recebimento e validação real de arquivos (nunca confiar em extensão/Content-Type)."""
import io
import os
import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from . import mediainfo
from .config import settings

Image.MAX_IMAGE_PIXELS = 4_000_000
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MUSIC_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
VIDEO_EXT = {".mp4", ".mov", ".m4v"}


def _tmpdir() -> Path:
    d = settings.storage_dir / "_tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def to_temp(up: UploadFile, max_bytes: int, suffix: str = "") -> Path:
    fd, name = tempfile.mkstemp(dir=_tmpdir(), suffix=suffix)
    size = 0
    try:
        with os.fdopen(fd, "wb") as f:
            while chunk := await up.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(413, f"arquivo excede o limite de {max_bytes // (1024 * 1024) or 1} MB")
                f.write(chunk)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    if size == 0:
        Path(name).unlink(missing_ok=True)
        raise HTTPException(422, "arquivo vazio")
    return Path(name)


def ext_of(filename: str | None) -> str:
    return Path(filename or "").suffix.lower()


def process_logo(tmp: Path) -> tuple[bytes, int, int]:
    """Aceita somente PNG real; recodifica (remove metadados/payloads embutidos). SVG não é aceito (ver docs)."""
    head = tmp.read_bytes()[:8]
    if head != PNG_MAGIC:
        raise HTTPException(422, "logotipo deve ser um PNG válido (SVG ainda não suportado)")
    try:
        with Image.open(tmp) as im:
            im.verify()
        with Image.open(tmp) as im:
            im.load()
            if im.width < 32 or im.height < 32 or im.width > 2000 or im.height > 2000:
                raise HTTPException(422, "logotipo deve ter entre 32 e 2000 px por lado")
            out = io.BytesIO()
            im.convert("RGBA").save(out, "PNG", optimize=True)
            return out.getvalue(), im.width, im.height
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError):
        raise HTTPException(422, "imagem PNG corrompida ou inválida")


def _music_magic_ok(head: bytes, ext: str) -> bool:
    if ext == ".mp3":
        return head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)
    if ext == ".wav":
        return head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    if ext == ".m4a":
        return head[4:8] == b"ftyp"
    if ext == ".ogg":
        return head[:4] == b"OggS"
    if ext == ".flac":
        return head[:4] == b"fLaC"
    return False


def validate_music(tmp: Path, ext: str) -> float:
    if ext not in MUSIC_EXT:
        raise HTTPException(422, "formato de música não permitido (mp3, wav, m4a, ogg, flac)")
    if not _music_magic_ok(tmp.read_bytes()[:16], ext):
        raise HTTPException(422, "conteúdo do arquivo não corresponde ao formato de áudio")
    try:
        dur = mediainfo.audio_duration(tmp)
    except mediainfo.MediaError as e:
        raise HTTPException(422, str(e))
    if dur > settings.max_music_seconds:
        raise HTTPException(422, "faixa excede a duração máxima permitida")
    return dur


def validate_video(tmp: Path, ext: str) -> mediainfo.VideoInfo:
    if ext not in VIDEO_EXT:
        raise HTTPException(422, "formato de vídeo não permitido (mp4/mov)")
    head = tmp.read_bytes()[:16]
    if head[4:8] != b"ftyp":
        raise HTTPException(422, "conteúdo não é um MP4/MOV válido")
    try:
        info = mediainfo.video_info(tmp)
    except mediainfo.MediaError as e:
        raise HTTPException(422, str(e))
    if info.duration > settings.max_video_seconds:
        raise HTTPException(422, "vídeo excede a duração máxima permitida")
    return info
