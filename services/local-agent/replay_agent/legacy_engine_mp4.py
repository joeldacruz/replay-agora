"""Geração de um ÚNICO MP4 final da Quadra 02, já com a orientação aplicada.

Este módulo é a peça testável que o `replay.py` operacional da Quadra 02 deve
chamar no lugar do `ffmpeg concat -c copy` + `os.replace`. Ele:

* NÃO inicia `AgentService`, NÃO acessa câmeras, buffers, Arduino ou SQLite;
* NÃO escreve em nenhuma pasta varrida pelo sincronizador (`replay-*.mp4`)
  enquanto o arquivo não estiver completo e verificado;
* só publica (rename atômico) depois de verificar decodificação, dimensões,
  duração e nome. Em qualquer falha nada `replay-*.mp4` é deixado para trás.

Orientação: lida do recibo privado gravado por `legacy_rotation_sync`
(`receive_rotation`). Sem recibo ou com recibo inválido o comportamento é o
legado documentado (0°, sem re-encode) e a causa é devolvida ao chamador para
registro; nunca se inventa revisão.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

COURT_KEY = "QUADRA_02"
NAMESPACE = uuid.UUID("b60f62dd-b871-4d7a-94b7-f65736ca59bc")
ALLOWED_ANGLES = (0, 90, 180, 270)
FILTERS = {0: None, 90: "transpose=clock", 180: "hflip,vflip", 270: "transpose=cclock"}
FINAL_NAME = re.compile(r"^replay-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}\.mp4$")
THUMB_MAX_BYTES = 262144


def _frame_plan(frame: dict):
    """Valida arte/recibo sem depender da API ou do servidor central."""
    from . import framing
    if not isinstance(frame, dict):
        raise PublishError("perfil de moldura inválido")
    p = Path(frame.get("localPath", ""))
    if not p.is_file() or p.is_symlink() or p.suffix.lower() != ".png":
        raise PublishError("PNG privado de moldura indisponível")
    sha = frame.get("sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise PublishError("SHA-256 de moldura inválido")
    if hashlib.sha256(p.read_bytes()).hexdigest() != sha:
        raise PublishError("PNG de moldura diverge do SHA-256")
    width, height = frame.get("width"), frame.get("height")
    if type(width) is not int or type(height) is not int:
        raise PublishError("dimensões de moldura inválidas")
    try:
        size = framing.parse_size(frame["out"])
        layout = framing.validate_layout(frame["layout"])
        window = framing.validate_window(frame["window"], width, height)
        geom = framing.compute(layout, *size, width, height, window)
    except (ValueError, KeyError, TypeError) as exc:
        raise PublishError("composição da moldura inválida") from exc
    return p, size, layout, geom


def frame_from_receipt(receipt, orientation="horizontal"):
    """Seleciona a versão cacheada; nenhum segundo MP4 é criado.

    A ausência de recibo retorna None (compatível com o motor legado).
    Recibos inválidos falham explicitamente antes de gerar o MP4.
    """
    if orientation not in ("horizontal", "vertical"):
        raise PublishError("formato de moldura inválido")
    path = Path(receipt)
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise PublishError("recibo privado de moldura inválido")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise PublishError("recibo de moldura ilegível") from exc
    if data.get("courtKey") != COURT_KEY:
        raise PublishError("recibo de outra quadra")
    if not data.get("framesEnabled"):
        return None
    if orientation not in data.get("formats", []):
        raise PublishError("formato não habilitado na quadra")
    frame = data.get("frames", {}).get(orientation)
    if not frame:
        raise PublishError("formato ativo sem moldura")
    _frame_plan(frame)
    return frame


class PublishError(RuntimeError):
    """Falha ao gerar/verificar; nenhum MP4 final foi publicado."""


def local_id_for(final_name: str) -> str:
    """Identidade do catálogo: uuid5 pelo nome do arquivo final publicado."""
    return str(uuid.uuid5(NAMESPACE, COURT_KEY + ":" + final_name))


def resolve_angle(receipt_path) -> tuple[int, str]:
    """Retorna (ângulo, origem). Nunca lança: o replay não pode ser perdido.

    origem: ``receipt`` | ``legacy_no_receipt`` | ``legacy_invalid_receipt``.
    """
    path = Path(receipt_path)
    try:
        info = path.lstat()
        if path.is_symlink() or info.st_mode & 0o077:
            return 0, "legacy_invalid_receipt"
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return 0, "legacy_no_receipt"
    except (OSError, ValueError):
        return 0, "legacy_invalid_receipt"
    angle = data.get("rotationDeg") if isinstance(data, dict) else None
    version = data.get("version") if isinstance(data, dict) else None
    if (data.get("courtKey") != COURT_KEY or type(angle) is not int
            or angle not in ALLOWED_ANGLES or type(version) is not int or version < 1):
        return 0, "legacy_invalid_receipt"
    return angle, "receipt"


def _probe(path, ffprobe) -> dict:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries",
         "stream=codec_type,width,height:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30)
    if out.returncode:
        raise PublishError("ffprobe falhou")
    return json.loads(out.stdout or "{}")


def _video(meta) -> dict:
    videos = [s for s in meta.get("streams", []) if s.get("codec_type") == "video"]
    if len(videos) != 1 or not videos[0].get("width") or not videos[0].get("height"):
        raise PublishError("MP4 sem exatamente um vídeo válido")
    return videos[0]


def build_final(concat_list, final, angle, *, work_dir, expected_duration=None,
                duration_tolerance=1.5, ffmpeg="ffmpeg", ffprobe="ffprobe",
                timeout=180, frame=None) -> dict:
    """Gera, verifica e publica ``final``. Retorna metadados do arquivo FINAL."""
    if type(angle) is not int or angle not in ALLOWED_ANGLES:
        raise PublishError("orientação inválida")
    frame_info = _frame_plan(frame) if frame is not None else None
    final = Path(final)
    work_dir = Path(work_dir)
    if not FINAL_NAME.match(final.name):
        raise PublishError("nome final fora do padrão replay-AAAAMMDD-HHMMSS-id.mp4")
    if final.exists():
        raise PublishError("destino já existe; nunca sobrescrever replay publicado")
    if not final.parent.is_dir() or not work_dir.is_dir():
        raise PublishError("pasta de saída ou temporária inexistente")
    if os.stat(final.parent).st_dev != os.stat(work_dir).st_dev:
        raise PublishError("temporário e destino em filesystems diferentes (rename não atômico)")
    if work_dir.resolve() == final.parent.resolve():
        raise PublishError("temporário não pode ficar na pasta monitorada")

    fd, part_name = tempfile.mkstemp(prefix=".replay-", suffix=".part.mp4", dir=work_dir)
    os.close(fd)
    part = Path(part_name)
    try:
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
               "-f", "concat", "-safe", "0", "-i", str(concat_list),
               "-map", "0:v:0", "-map", "0:a:0?"]
        if frame_info is not None:
            from . import framing
            png, (out_w, out_h), layout, geom = frame_info
            src = _video(_probe(_first_input(concat_list), ffprobe))
            src_w, src_h = src["width"], src["height"]
            # Rotação e composição no MESMO filtro e no MESMO encode.
            rot_w, rot_h = (src_h, src_w) if angle in (90, 270) else (src_w, src_h)
            vid = framing.video_geometry(geom["window"], rot_w, rot_h, layout)
            rot_filters = [f"[0:v]{FILTERS[angle]}[oriented]"] if angle else []
            chain = framing.build_filters(
                geom, vid, layout, src="oriented" if angle else "0:v",
                frame="1:v", out="final_video")
            cmd += ["-loop", "1", "-framerate", "25", "-i", str(png),
                    "-filter_complex", ";".join(rot_filters + chain),
                    "-map", "[final_video]", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                    "-c:a", "copy"]
        elif angle:
            cmd += ["-vf", FILTERS[angle], "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "copy"]
        else:
            cmd += ["-c", "copy"]
        cmd += ["-movflags", "+faststart", "-avoid_negative_ts", "make_zero",
                "-f", "mp4", "-y", str(part)]
        try:
            run = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise PublishError("FFmpeg excedeu o tempo limite") from exc
        if run.returncode or not part.stat().st_size:
            raise PublishError("FFmpeg falhou: " + (run.stderr or "")[-300:])

        meta = _probe(part, ffprobe)
        video = _video(meta)
        duration = float(meta.get("format", {}).get("duration") or 0)
        if duration <= 0:
            raise PublishError("duração inválida")
        if expected_duration is not None and abs(duration - expected_duration) > duration_tolerance:
            raise PublishError(f"duração {duration:.2f}s fora do esperado {expected_duration:.2f}s")
        # Dimensões esperadas a partir da primeira entrada da lista concat.
        first = _first_input(concat_list)
        src = _video(_probe(first, ffprobe))
        sw, sh = src["width"], src["height"]
        want = frame_info[1] if frame_info else ((sh, sw) if angle in (90, 270) else (sw, sh))
        if (video["width"], video["height"]) != want:
            raise PublishError("dimensões inesperadas após rotação")
        check = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-i", str(part),
                                "-f", "null", "-"], capture_output=True, text=True, timeout=timeout)
        if check.returncode or check.stderr.strip():
            raise PublishError("decodificação do MP4 com erros")

        os.chmod(part, 0o644)
        # Publicação atômica e sem sobrescrita: link falha se já existir.
        os.link(part, final)
        return {"path": str(final), "name": final.name, "localId": local_id_for(final.name),
                "width": video["width"], "height": video["height"], "duration": duration,
                "bytes": final.stat().st_size, "angle": angle}
    except FileExistsError as exc:
        raise PublishError("destino surgiu durante o processamento") from exc
    finally:
        part.unlink(missing_ok=True)


def _first_input(concat_list) -> Path:
    base = Path(concat_list).parent
    for line in Path(concat_list).read_text(encoding="utf-8").splitlines():
        m = re.match(r"file '(.+)'$", line.strip())
        if m:
            p = Path(m.group(1))
            return p if p.is_absolute() else base / p
    raise PublishError("lista concat vazia")


def make_thumbnail(video, dest, *, ffmpeg="ffmpeg", timeout=30) -> int:
    """JPEG ≤256 KiB lido do MP4 FINAL (mesma política da Quadra 01)."""
    dest = Path(dest)
    for q in (5, 8, 12, 18):
        with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
            out = Path(tmp) / "imagem.jpg"
            r = subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                                "-ss", "1", "-i", str(video), "-frames:v", "1",
                                "-vf", "scale=640:-2", "-q:v", str(q), "-y", str(out)],
                               capture_output=True, timeout=timeout)
            if r.returncode or not out.is_file():
                continue
            data = out.read_bytes()
            if 64 <= len(data) <= THUMB_MAX_BYTES and data[:2] == b"\xff\xd8" and data[-2:] == b"\xff\xd9":
                tmp_jpg = dest.parent / (".thumb-" + dest.name)
                tmp_jpg.write_bytes(data)
                os.replace(tmp_jpg, dest)
                return len(data)
    raise PublishError("não foi possível gerar JPEG válido")


def cleanup_stale(work_dir, *, max_age_s=3600, now=None) -> int:
    """Remove SOMENTE `.replay-*.part.mp4` antigos da pasta temporária."""
    import time
    now = now or time.time()
    removed = 0
    for p in Path(work_dir).glob(".replay-*.part.mp4"):
        if p.is_file() and not p.is_symlink() and now - p.stat().st_mtime > max_age_s:
            p.unlink()
            removed += 1
    return removed
