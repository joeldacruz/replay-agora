"""Ponte offline entre a revisão do agente e um MP4 já cortado pelo motor.

O chamador entrega um MP4 finalizado na janela solicitada e um diretório de
homologação fora das pastas observadas pelo sincronizador. Este módulo não
abre câmera, buffer, serial ou conexão com a central.
"""

from contextlib import nullcontext
from pathlib import Path
import json
import math
import os
import subprocess
import tempfile

from . import framing
from .ffplan import ExportPlan, FramePlan, build_command
from .renderjobs import RenderError, probe, source_meta, verify_output
from .thumbnail import extract_thumbnail


_ROTATION = {0: "", 90: "transpose=clock", 180: "hflip,vflip", 270: "transpose=cclock"}
_FORMATS = {"horizontal": (1920, 1080), "vertical": (1080, 1920)}


def replay_profile(remote, court_key):
    """Copia a configuração e os PNGs da mesma revisão sob o lock do agente."""
    with remote.lock:
        snapshot = remote.state.get("snapshot") or {}
        court = next((c for c in snapshot.get("courts", []) if c["key"] == court_key), None)
        if court is None or not court.get("active"):
            raise ValueError("quadra sem configuração ativa")
        rp = court["replay"]
        rotation = (court.get("camera") or {}).get("rotationDeg", 0)
        pre, post = rp["preS"], rp["postS"]
        formats = rp["formats"]
        if type(rotation) is not int or rotation not in _ROTATION:
            raise ValueError("rotação inválida")
        if type(pre) not in (int, float) or type(post) not in (int, float) or not 1 <= pre <= 120 or not 0 <= post <= 60:
            raise ValueError("janela inválida")
        if not isinstance(formats, list) or not formats or any(type(f) is not str or f not in _FORMATS for f in formats):
            raise ValueError("formatos inválidos")
        if len(set(formats)) != len(formats):
            raise ValueError("formatos duplicados")
        frames = {}
        for orientation in formats:
            frame = remote.frame_for(court_key, orientation)
            if frame is None:
                frames[orientation] = None
                continue
            if (frame.out_w % 2 or frame.out_h % 2
                    or (orientation == "horizontal" and frame.out_w <= frame.out_h)
                    or (orientation == "vertical" and frame.out_h <= frame.out_w)):
                raise ValueError("resolução incompatível com o formato")
            framing.validate_window(frame.window, frame.width, frame.height)
            framing.compute(frame.layout, frame.out_w, frame.out_h, frame.width, frame.height, frame.window)
            frames[orientation] = (frame, Path(frame.path).read_bytes())
        return remote.active_version, pre, post, rotation, frames


def _run(cmd):
    result = subprocess.run([cmd[0], "-filter_threads", "1", "-filter_complex_threads", "1", *cmd[1:]],
                            capture_output=True, timeout=180)
    if result.returncode:
        raise RenderError("falha no FFmpeg durante o processamento do replay")


def render_legacy_replay(remote, court_key, source, destination, *, ffmpeg="ffmpeg",
                         ffprobe="ffprobe", governor=None, profile=None):
    """Gera os MP4s e a prévia em lote. O primeiro formato é o destino do jogador.

    O chamador pode obter o perfil antes do corte e passá-lo aqui após cortar
    pre/post, mantendo a mesma revisão mesmo se o Portal mudar nesse intervalo.
    """
    version, pre, post, rotation, frames = profile if profile is not None else replay_profile(remote, court_key)
    source, destination = Path(source).resolve(), Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    data = probe(source, ffprobe)
    stream = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if not stream or any("rotation" in item for item in stream.get("side_data_list", [])):
        raise RenderError("origem sem vídeo ou com rotação embutida")
    meta = source_meta(source, ffprobe)
    if not math.isfinite(meta.duration) or abs(meta.duration - (pre + post)) > 0.25:
        raise RenderError("MP4 de origem fora da janela de duração solicitada")
    mode = "original" if meta.has_audio else "mute"
    manifest = {"courtKey": court_key, "configVersion": version, "variants": {},
                "playerFormat": next(iter(frames)), "rotationDeg": 0}
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".replay-") as work:
        work = Path(work)
        ready = work / "ready"
        ready.mkdir()
        with governor.slot() if governor else nullcontext():
            for orientation, cached in frames.items():
                intermediate = work / "oriented.mp4"
                out = ready / f"{orientation}.mp4"
                filters = [_ROTATION[rotation]] if rotation else []
                if cached is None:
                    w, h = _FORMATS[orientation]
                    filters += [f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2",
                                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2", "setsar=1"]
                input_path, input_meta = source, meta
                if filters:
                    _run([ffmpeg, "-y", "-v", "error", "-nostdin", "-noautorotate", "-i", str(source),
                          "-map", "0:v:0", "-map", "0:a:0?", "-vf", ",".join(filters),
                          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy",
                          "-map_metadata", "-1", str(intermediate)])
                    input_path, input_meta = intermediate, source_meta(intermediate, ffprobe)
                frame = None
                if cached is not None:
                    original, png = cached
                    art = work / "frame.png"
                    art.write_bytes(png)
                    frame = FramePlan(str(art), original.width, original.height, original.window,
                                      original.layout, original.out_w, original.out_h)
                _run(build_command(ExportPlan(str(input_path), str(out), input_meta, mode, frame=frame), ffmpeg))
                verify_output(out, mode, meta.duration, ffprobe)
                result = source_meta(out, ffprobe)
                expected = (frame.out_w, frame.out_h) if frame else _FORMATS[orientation]
                if (result.width, result.height) != expected:
                    raise RenderError("dimensões da saída diferentes do formato escolhido")
                manifest["variants"][orientation] = {"path": out.name, "width": result.width,
                                                      "height": result.height, "durationS": result.duration,
                                                      "bytes": out.stat().st_size, "videoCodec": result.video_codec}
            player = manifest["variants"][manifest["playerFormat"]]
            (ready / "thumbnail.jpg").write_bytes(extract_thumbnail(
                str(ready / player["path"]), ffmpeg, min(1.0, meta.duration / 2)))
            (ready / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        os.rename(ready, destination)
    return manifest
