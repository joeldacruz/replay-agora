"""Recepção isolada das molduras escolhidas no Portal Dev para QUADRA_02.

Somente consultas GET autenticadas. Nunca consome /remote/pending, nunca confirma
revisão e não altera câmera, buffer, Arduino, serviços ou MP4 publicado.

O chamador fornece uma pasta privada de cache FORA da árvore de replays.
A renderização automática depende de integração posterior com o replay.py legado.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import tempfile

from . import framing

COURT_KEY = "QUADRA_02"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
_MAX_PNG = 16 * 1024 * 1024
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _private(root: Path) -> Path:
    root = Path(root)
    if root.is_symlink():
        raise ValueError("pasta de cache não pode ser link simbólico")
    path = root.resolve(strict=True)
    if not path.is_dir() or path.stat().st_mode & 0o077:
        raise ValueError("pasta de molduras deve ser privada (0700)")
    return path


def _atomic(path: Path, content: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=".moldura-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 33 or not data.startswith(_PNG_MAGIC) or data[12:16] != b"IHDR":
        raise ValueError("arquivo não é PNG válido")
    return struct.unpack(">II", data[16:24])


def _validate_frame(frame: dict, orientation: str) -> dict:
    if not isinstance(frame, dict):
        raise ValueError("moldura inválida")
    vid, sha = frame.get("versionId"), frame.get("sha256")
    if not isinstance(vid, str) or not _ID.fullmatch(vid):
        raise ValueError("identificador de moldura inválido")
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise ValueError("SHA-256 inválido")
    count = frame.get("bytes")
    if type(count) is not int or not 33 <= count <= _MAX_PNG:
        raise ValueError("tamanho de moldura inválido")
    w, h = frame.get("width"), frame.get("height")
    if type(w) is not int or type(h) is not int or not 16 <= w <= 4096 or not 16 <= h <= 4096:
        raise ValueError("dimensões de moldura inválidas")
    out_w, out_h = framing.parse_size(frame.get("out", ""))
    if orientation == "horizontal" and out_w <= out_h:
        raise ValueError("saída horizontal inválida")
    if orientation == "vertical" and out_h <= out_w:
        raise ValueError("saída vertical inválida")
    layout = framing.validate_layout(frame.get("layout"))
    window = framing.validate_window(frame.get("window"), w, h)
    geometry = framing.compute(layout, out_w, out_h, w, h, window)
    if geometry["window"]["w"] < 64 or geometry["window"]["h"] < 64:
        raise ValueError("janela de vídeo muito pequena")
    return {"versionId": vid, "sha256": sha, "bytes": count, "width": w,
            "height": h, "out": frame["out"], "layout": layout, "window": window}


def fetch_manifest(client, *, expected_server_id: str, expected_arena_id: str) -> dict:
    value = client.call("GET", f"/remote/frames/{COURT_KEY}")
    if not isinstance(value, dict) or value.get("courtKey") != COURT_KEY:
        raise ValueError("resposta pertence a outra quadra")
    if value.get("serverId") != expected_server_id or value.get("arenaId") != expected_arena_id:
        raise ValueError("servidor/arena divergente")
    if value.get("configurationState") != "desired_unapplied":
        raise ValueError("estado de configuração desconhecido")
    formats = value.get("formats")
    if (not isinstance(formats, list) or not formats or
            len(set(formats)) != len(formats) or
            any(x not in ("horizontal", "vertical") for x in formats)):
        raise ValueError("formatos inválidos")
    enabled = value.get("framesEnabled")
    if type(enabled) is not bool:
        raise ValueError("estado de molduras inválido")
    frames = value.get("frames")
    if not isinstance(frames, dict):
        raise ValueError("manifesto sem molduras")
    selected = {}
    for orient in ("horizontal", "vertical"):
        item = frames.get(orient)
        if item is not None and (not enabled or orient not in formats):
            raise ValueError("moldura fora dos formatos ativos")
        selected[orient] = _validate_frame(item, orient) if item is not None else None
    if enabled and any(selected[x] is None for x in formats):
        raise ValueError("formato habilitado sem moldura selecionada")
    return {"courtKey": COURT_KEY, "serverId": expected_server_id,
            "arenaId": expected_arena_id, "configurationState": "desired_unapplied",
            "formats": formats, "framesEnabled": enabled, "frames": selected}


def receive_frames(client, root: Path, *, expected_server_id: str,
                   expected_arena_id: str) -> dict:
    """Baixa, confere SHA-256 e publica um recibo de cache atômico.

    Falha de rede ou PNG adulterado não altera o manifesto anterior.
    O cache não é uma ordem de aplicação remota.
    """
    root = _private(root)
    manifest = fetch_manifest(client, expected_server_id=expected_server_id,
                              expected_arena_id=expected_arena_id)
    lock_path = root / ".quadra02-frames.lock"
    if lock_path.is_symlink():
        raise ValueError("lock inválido")
    with lock_path.open("a+b") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        for orient in ("horizontal", "vertical"):
            frame = manifest["frames"][orient]
            if frame is None:
                continue
            path = root / (frame["sha256"] + ".png")
            if path.is_symlink():
                raise ValueError("cache PNG não pode ser link simbólico")
            if path.is_file():
                data = path.read_bytes()
            else:
                resp = client.request_raw("GET", "/remote/assets/" + frame["versionId"], timeout=30)
                data = resp.content
            if len(data) != frame["bytes"] or hashlib.sha256(data).hexdigest() != frame["sha256"]:
                raise ValueError("PNG não corresponde ao SHA-256 da configuração")
            if _png_size(data) != (frame["width"], frame["height"]):
                raise ValueError("PNG não corresponde às dimensões declaradas")
            if not path.exists():
                _atomic(path, data)
            frame["localPath"] = str(path)
        receipt = root / "quadra02-frames.json"
        if receipt.is_symlink():
            raise ValueError("recibo não pode ser link simbólico")
        if receipt.exists():
            old = json.loads(receipt.read_text(encoding="utf-8"))
            if (old.get("serverId"), old.get("arenaId")) != (
                    expected_server_id, expected_arena_id):
                raise ValueError("recibo anterior pertence a outra instalação")
        _atomic(receipt, json.dumps(manifest, sort_keys=True).encode("utf-8"))
    return manifest
