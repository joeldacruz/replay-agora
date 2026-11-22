"""Processa uma amostra com a revisão v3 local e cadastra a mídia no agente de homologação."""

from datetime import datetime, timezone
import hashlib
import json
import math
import re
import os
from pathlib import Path
import tempfile

from .legacy_bridge import _run, render_legacy_replay, replay_profile
from .remote import RemoteManager
from .renderjobs import RenderError, probe, source_meta


def load_offline_remote(data_dir: Path) -> RemoteManager:
    """Carrega apenas o estado local da cópia de homologação."""
    return RemoteManager(None, None, data_dir)


def cut_window(source, dest, trigger_offset, pre, post, *, ffmpeg="ffmpeg", ffprobe="ffprobe"):
    """Offset em segundos desde o primeiro quadro do MP4 contínuo desta quadra."""
    start, end = trigger_offset - pre, trigger_offset + post
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
        raise RenderError("janela solicitada fora da origem")
    data = probe(source, ffprobe)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if video is None or any("rotation" in item for item in video.get("side_data_list", [])):
        raise RenderError("origem sem vídeo ou com rotação embutida")
    try:
        duration = float(video.get("duration", data["format"]["duration"]))
    except (ValueError, KeyError) as exc:
        raise RenderError("duração do vídeo desconhecida") from exc
    if not math.isfinite(duration) or end > duration:
        raise RenderError("janela solicitada fora da origem ou pós-roll incompleto")
    _run([ffmpeg, "-n", "-v", "error", "-nostdin", "-noautorotate", "-i", str(source),
          "-ss", f"{start:.6f}", "-t", f"{end - start:.6f}",
          "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-map_metadata", "-1", str(dest)])


def publish_sample(remote, store, root, allowed_courts, *, court_key, local_id, captured_at,
                   source, trigger_offset, ffmpeg="ffmpeg", ffprobe="ffprobe"):
    """Cadastra a variante do jogador via LocalStore, usado pelo sync e relay existentes.

    Use um catálogo exclusivo de homologação e a raiz de mídia permitida pelo relay.
    Um replay já cadastrado ou renderizado mantém a versão usada inicialmente.
    """
    if court_key not in allowed_courts:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.:\-]{8,120}", local_id) or captured_at.utcoffset() is None:
        raise ValueError("identidade ou horário de replay inválido")
    captured_at = captured_at.astimezone(timezone.utc)
    source = Path(source).resolve(strict=True)
    root = Path(root).resolve()
    if source.is_relative_to(root):
        raise ValueError("origem deve ficar fora da raiz de publicação")
    root.mkdir(parents=True, exist_ok=True)
    identity = {"localId": local_id, "courtKey": court_key, "capturedAt": captured_at.isoformat(),
                "source": str(source), "offsetS": trigger_offset}
    target = root / hashlib.sha256(local_id.encode()).hexdigest()
    current = store.get(local_id)
    if current is not None and Path(current["path"]).parent != target / "media":
        raise ValueError("identidade já cadastrada por outro fluxo")
    if current is not None and not target.is_dir():
        raise RenderError("lote publicado ausente")
    if not target.is_dir():
        profile = replay_profile(remote, court_key)
        with tempfile.TemporaryDirectory(dir=root, prefix=".publish-") as temp:
            work = Path(temp)
            bundle = work / "bundle"
            bundle.mkdir()
            cut = work / "cut.mp4"
            cut_window(source, cut, trigger_offset, profile[1], profile[2], ffmpeg=ffmpeg, ffprobe=ffprobe)
            render_legacy_replay(remote, court_key, cut, bundle / "media", profile=profile,
                                 ffmpeg=ffmpeg, ffprobe=ffprobe)
            (bundle / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
            try:
                os.rename(bundle, target)
            except OSError:
                if not target.is_dir():
                    raise
    if json.loads((target / "identity.json").read_text(encoding="utf-8")) != identity:
        raise ValueError("identidade de replay já usada para outro acionamento")
    media = target / "media"
    manifest = json.loads((media / "manifest.json").read_text(encoding="utf-8"))
    orientation = manifest["playerFormat"]
    if (orientation not in {"vertical", "horizontal"} or manifest["courtKey"] != court_key
            or manifest["rotationDeg"] != 0):
        raise RenderError("manifesto incompatível com o acionamento")
    variant = manifest["variants"][orientation]
    path = media / f"{orientation}.mp4"
    if variant["path"] != path.name or path.stat().st_size != variant["bytes"]:
        raise RenderError("variante de reprodução inválida")
    if current is not None and Path(current["path"]) != path:
        raise RenderError("catálogo diverge do manifesto")
    meta = source_meta(path, ffprobe)
    store.add_replay(local_id, court_key, captured_at, str(path), variant["bytes"], meta.duration,
                     meta.width, meta.height, meta.has_audio, meta.video_codec)
    registered = store.get(local_id)
    if registered["court_key"] != court_key or Path(registered["path"]) != path:
        raise ValueError("identidade cadastrada por outro fluxo")
    return path
