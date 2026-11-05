"""Construção determinística do comando FFmpeg (lista de argumentos; nunca shell).

CÓPIA AUTOCONTIDA de services/media-worker/worker/ffmpeg.py: o computador da arena não importa código do servidor.
O teste `test_ffplan_matches_central_worker` garante que os dois geram exatamente o mesmo comando.
"""
from dataclasses import dataclass, field


MARGIN_PCT = 2.0


def overlay_geometry(W: int, H: int, lw: int, lh: int, position: str, size_pct: int) -> tuple[int, int, int, int]:
    """(x, y, w, h) do logo — idêntico a app.policy.overlay_geometry."""
    w = max(2, round(W * size_pct / 100 / 2) * 2)
    h = max(2, round(w * lh / max(lw, 1) / 2) * 2)
    m = round(W * MARGIN_PCT / 100)
    x = m if position.endswith("left") else W - w - m
    y = m if position.startswith("top") else H - h - m
    return x, y, w, h



@dataclass
class SourceMeta:
    duration: float
    width: int
    height: int
    video_codec: str
    has_audio: bool
    audio_codec: str | None = None


@dataclass
class Overlay:
    path: str
    logo_w: int
    logo_h: int
    position: str
    size_pct: int
    opacity: float
    duration_s: float | None = None


@dataclass
class ExportPlan:
    src: str
    out: str
    meta: SourceMeta
    mode: str                       # original | music | mix | mute
    music_path: str | None = None
    original_volume: float = 1.0
    music_volume: float = 0.6
    overlays: list[Overlay] = field(default_factory=list)


class PlanError(ValueError):
    pass


def _stereo() -> str:
    return "aresample=44100,aformat=channel_layouts=stereo"


def build_command(p: ExportPlan, ffmpeg: str = "ffmpeg", hw=None) -> list[str]:
    m = p.meta
    if p.mode in ("original", "mix") and not m.has_audio:
        raise PlanError("vídeo sem áudio original não suporta os modos original/mix")
    if p.mode in ("music", "mix") and not p.music_path:
        raise PlanError("modo music/mix exige faixa musical")

    cmd = [ffmpeg, "-y", "-v", "error", "-nostdin", *(hw.input_args() if hw else []), "-i", p.src]
    idx = 1
    logo_idx = []
    for o in p.overlays:
        cmd += ["-loop", "1", "-framerate", "25", "-i", o.path]
        logo_idx.append(idx)
        idx += 1
    music_idx = None
    if p.mode in ("music", "mix"):
        cmd += ["-stream_loop", "-1", "-i", p.music_path]
        music_idx = idx

    filters: list[str] = []
    vmap, vcodec = "0:v:0", ["-c:v", "copy"]
    if p.overlays:
        cur = "[0:v]"
        for n, (o, li) in enumerate(zip(p.overlays, logo_idx)):
            x, y, w, h = overlay_geometry(m.width, m.height, o.logo_w, o.logo_h, o.position, o.size_pct)
            filters.append(f"[{li}:v]format=rgba,scale={w}:{h},colorchannelmixer=aa={o.opacity:.3f}[lg{n}]")
            en = f":enable='between(t,0,{o.duration_s:.3f})'" if o.duration_s else ""
            filters.append(f"{cur}[lg{n}]overlay=x={x}:y={y}:shortest=1{en}[v{n}]")
            cur = f"[v{n}]"
        vmap = cur
        vcodec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
        if hw:   # os overlays continuam na CPU; só a codificação final vai para a GPU
            filters.append(f"{cur}{hw.upload()}[vhw]")
            vmap, vcodec = "[vhw]", hw.encoder()
    elif m.video_codec != "h264":
        vcodec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
        if hw:
            vcodec = ["-vf", hw.upload(), *hw.encoder()]

    fade = f"afade=t=out:st={max(m.duration - 1, 0):.3f}:d=1"
    amap, acodec = None, ["-an"]
    if p.mode == "original":
        if m.audio_codec == "aac" and abs(p.original_volume - 1.0) < 1e-6:
            amap, acodec = "0:a:0", ["-c:a", "copy"]
        else:
            filters.append(f"[0:a:0]volume={p.original_volume:.3f},alimiter=limit=0.95[aout]")
            amap, acodec = "[aout]", ["-c:a", "aac", "-b:a", "128k"]
    elif p.mode == "music":
        filters.append(f"[{music_idx}:a:0]{_stereo()},volume={p.music_volume:.3f},{fade},alimiter=limit=0.95[aout]")
        amap, acodec = "[aout]", ["-c:a", "aac", "-b:a", "128k"]
    elif p.mode == "mix":
        filters.append(f"[0:a:0]{_stereo()},volume={p.original_volume:.3f},apad[a0]")
        filters.append(f"[{music_idx}:a:0]{_stereo()},volume={p.music_volume:.3f}[a1]")
        filters.append(f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,{fade},alimiter=limit=0.95[aout]")
        amap, acodec = "[aout]", ["-c:a", "aac", "-b:a", "128k"]

    if filters:
        cmd += ["-filter_complex", ";".join(filters)]
    cmd += ["-map", vmap]
    if amap:
        cmd += ["-map", amap]
    cmd += vcodec + acodec
    cmd += ["-sn", "-dn", "-map_metadata", "-1", "-t", f"{m.duration:.3f}", "-movflags", "+faststart",
            "-progress", "pipe:1", "-nostats", p.out]
    return cmd
