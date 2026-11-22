"""Rotação mínima de um replay legado, mantendo o MP4 original intocado.

Este módulo NÃO consulta a central, não inicia captura e não publica mídia.
A orientação vem do snapshot já confirmado pelo adaptador legado v3.
O sincronizador só deve usar o destino depois que esta função retornar.
"""
from pathlib import Path
import json
import os
import subprocess
import tempfile


FILTERS = {
    0: None,
    90: "transpose=clock",
    180: "hflip,vflip",
    270: "transpose=cclock",
}


def rotation_for_court(state: dict, court_key: str) -> tuple[int, int]:
    """Retorna (versão aplicada, graus) da câmera principal da quadra.

    Rejeita ausência de versão e configuração inválida: nunca supor 0°.
    A fonte deve ser o estado *ativo* do LegacyRemote, não uma revisão pendente.
    """
    if not isinstance(state, dict):
        raise ValueError("estado remoto inválido")
    version = state.get("activeVersion")
    if type(version) is not int or version < 1:
        raise ValueError("orientação remota ainda não aplicada")
    courts = (state.get("snapshot") or {}).get("courts")
    if not isinstance(courts, list):
        raise ValueError("quadras ausentes da revisão aplicada")
    matches = [c for c in courts if c.get("key") == court_key]
    if len(matches) != 1 or not matches[0].get("active"):
        raise ValueError("quadra ausente, duplicada ou inativa")
    camera = matches[0].get("camera")
    angle = camera.get("rotationDeg") if isinstance(camera, dict) else None
    if type(angle) is not int or angle not in FILTERS:
        raise ValueError("orientação de câmera inválida")
    return version, angle


def rotate_replay(source, destination, angle: int, *, ffmpeg="ffmpeg", ffprobe="ffprobe"):
    """Grava MP4 processado em destino *novo*, com publicação atômica.

    A variante deve ficar fora de pastas varridas automaticamente pelo catálogo
    legado até ser associada à identidade de replay original.
    """
    if type(angle) is not int or angle not in FILTERS:
        raise ValueError("rotação deve ser 0, 90, 180 ou 270 graus")
    source = Path(source).resolve(strict=True)
    destination = Path(destination).absolute()
    if destination.exists() or source == destination:
        raise FileExistsError("destino já existe ou coincide com o original")
    if not destination.parent.is_dir():
        raise FileNotFoundError("pasta de saída inexistente")
    if not source.is_file() or source.stat().st_size <= 0:
        raise ValueError("MP4 original inexistente ou vazio")
    info = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_type,width,height:stream_side_data=rotation",
         "-of", "json", str(source)],
        capture_output=True, text=True, timeout=25, check=True
    )
    meta = json.loads(info.stdout)
    streams = meta.get("streams") or []
    if len(streams) != 1 or streams[0].get("codec_type") != "video":
        raise ValueError("MP4 sem vídeo válido")
    # A orientação existente deve ser normalizada explicitamente antes de
    # usar este filtro; rejeitar evita aplicar rotação duas vezes.
    if any("rotation" in entry for entry in streams[0].get("side_data_list", [])):
        raise ValueError("MP4 possui rotação embutida; normalização necessária")

    fd, temp_name = tempfile.mkstemp(prefix=".rotate-", suffix=".mp4", dir=destination.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-noautorotate", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
               "-map_metadata", "-1"]
        if angle:
            cmd += ["-vf", FILTERS[angle], "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "copy"]
        else:
            cmd += ["-c", "copy"]
        cmd += ["-movflags", "+faststart", "-y", str(temp)]
        result = subprocess.run(cmd, capture_output=True, timeout=180)
        if result.returncode or not temp.stat().st_size:
            raise RuntimeError("FFmpeg não conseguiu gerar variante rotacionada")
        final = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(temp)],
            capture_output=True, text=True, timeout=25, check=True
        )
        video = (json.loads(final.stdout).get("streams") or [{}])[0]
        w, h = streams[0]["width"], streams[0]["height"]
        expected = (h, w) if angle in (90, 270) else (w, h)
        if (video.get("width"), video.get("height")) != expected:
            raise RuntimeError("dimensões inesperadas após rotação")
        # Evita sobrescrever um destino surgido durante o processamento.
        # hardlink atômico: falha se já existir; remove o temporário ao final.
        os.link(temp, destination)
        return {"angle": angle, "width": expected[0], "height": expected[1],
                "path": str(destination), "bytes": destination.stat().st_size}
    finally:
        temp.unlink(missing_ok=True)
