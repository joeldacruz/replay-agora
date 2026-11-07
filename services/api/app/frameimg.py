"""Validação e análise de molduras enviadas (PNG/ZIP). Nunca confia em extensão, nome ou Content-Type."""
import io
import struct
import threading
import zipfile
from pathlib import PurePosixPath

from fastapi import HTTPException
from PIL import Image, ImageDraw, UnidentifiedImageError

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_PNG_BYTES = 8 * 1024 * 1024
MAX_SIDE, MAX_PIXELS = 3840, 8_300_000
MAX_ZIP_FILES, MAX_ZIP_TOTAL = 20, 40 * 1024 * 1024
_lock = threading.Lock()


def _bad(msg: str) -> HTTPException:
    return HTTPException(422, msg)


def png_header_size(data: bytes) -> tuple[int, int]:
    if data[:8] != PNG_MAGIC or data[12:16] != b"IHDR":
        raise _bad("arquivo não é um PNG válido")
    return struct.unpack(">II", data[16:24])


def validate_png(data: bytes) -> tuple[bytes, int, int, str]:
    """Retorna (png_recodificado, w, h, sha256). Recodifica para eliminar metadados/payloads embutidos e preserva o canal alfa."""
    import hashlib
    if len(data) > MAX_PNG_BYTES:
        raise _bad(f"PNG excede {MAX_PNG_BYTES // (1024 * 1024)} MB")
    w, h = png_header_size(data)
    if w < 100 or h < 100 or w > MAX_SIDE or h > MAX_SIDE or w * h > MAX_PIXELS:
        raise _bad(f"dimensões fora dos limites (100–{MAX_SIDE} px por lado)")
    with _lock:
        old, Image.MAX_IMAGE_PIXELS = Image.MAX_IMAGE_PIXELS, MAX_PIXELS + 1
        try:
            with Image.open(io.BytesIO(data)) as im:
                im.verify()
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                rgba = im.convert("RGBA")
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, ValueError):
            raise _bad("PNG corrompido ou inválido") from None
        finally:
            Image.MAX_IMAGE_PIXELS = old
    alpha = rgba.getchannel("A")
    lo, hi = alpha.getextrema()
    if lo >= 250:
        raise _bad("a moldura precisa ter área transparente (canal alfa) para mostrar o vídeo")
    out = io.BytesIO()
    rgba.save(out, "PNG", optimize=True)
    png = out.getvalue()
    return png, w, h, hashlib.sha256(png).hexdigest()


def detect_window(png: bytes) -> dict:
    """Janela do vídeo = maior região transparente FECHADA pela moldura (não conectada às bordas da imagem)."""
    with Image.open(io.BytesIO(png)) as im:
        alpha = im.convert("RGBA").getchannel("A")
    W, H = alpha.size
    mask = alpha.point(lambda a: 255 if a < 16 else 0)          # 255 = transparente
    # preenche (valor 128) as regiões transparentes ligadas à borda: é o "fora" da moldura
    px = mask.load()
    seeds = [(x, y) for x in range(0, W, max(1, W // 40)) for y in (0, H - 1)] + [(x, y) for y in range(0, H, max(1, H // 40)) for x in (0, W - 1)]
    for sx, sy in seeds:
        if px[sx, sy] == 255:
            ImageDraw.floodfill(mask, (sx, sy), 128)
    inner = mask.point(lambda v: 255 if v == 255 else 0)
    box = inner.getbbox()
    if not box or (box[2] - box[0]) < W * 0.2 or (box[3] - box[1]) < H * 0.2:
        return {"x": 0, "y": 0, "w": W, "h": H}                   # sem janela interna: usa a imagem toda (ajustável no editor)
    return {"x": box[0], "y": box[1], "w": box[2] - box[0], "h": box[3] - box[1]}


def safe_name(name: str) -> str:
    base = PurePosixPath(name.replace("\\", "/")).name
    clean = "".join(c for c in base if c.isalnum() or c in "._- ").strip(" .")
    return clean[:80] or "moldura.png"


def extract_zip_pngs(data: bytes) -> list[tuple[str, bytes]]:
    """Lê SOMENTE PNGs de um ZIP, em memória (nada é extraído em disco => sem path traversal). Rejeita caminhos absolutos,
    '..', links simbólicos, criptografados, excesso de arquivos e ZIP bombs."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise _bad("ZIP inválido") from None
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if not infos:
        raise _bad("ZIP sem arquivos")
    if len(infos) > MAX_ZIP_FILES:
        raise _bad(f"ZIP com arquivos demais (máx. {MAX_ZIP_FILES})")
    if sum(i.file_size for i in infos) > MAX_ZIP_TOTAL:
        raise _bad("conteúdo do ZIP excede o limite permitido")
    out, seen = [], set()
    for i in infos:
        n = i.filename.replace("\\", "/")
        if n.startswith("/") or ".." in PurePosixPath(n).parts or (len(n) > 1 and n[1] == ":"):
            raise _bad("ZIP contém caminho inseguro")
        if (i.external_attr >> 16) & 0o170000 == 0o120000:
            raise _bad("ZIP contém link simbólico")
        if i.flag_bits & 0x1:
            raise _bad("ZIP criptografado não é aceito")
        if n.startswith("__MACOSX/") or PurePosixPath(n).name.startswith("."):
            continue
        if not n.lower().endswith(".png"):
            raise _bad(f"o ZIP só pode conter PNG ('{safe_name(n)}' não é aceito)")
        if i.file_size > MAX_PNG_BYTES:
            raise _bad(f"'{safe_name(n)}' excede {MAX_PNG_BYTES // (1024 * 1024)} MB")
        with zf.open(i) as f:
            blob = f.read(MAX_PNG_BYTES + 1)
        if len(blob) > MAX_PNG_BYTES or blob[:8] != PNG_MAGIC:
            raise _bad(f"'{safe_name(n)}' não é um PNG válido")
        key = safe_name(n).lower()
        if key in seen:
            raise _bad("ZIP com nomes de arquivo repetidos")
        seen.add(key)
        out.append((safe_name(n), blob))
    if not out:
        raise _bad("ZIP sem PNG utilizável")
    return out
