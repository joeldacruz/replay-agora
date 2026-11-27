"""Entrega isolada de rotação ao catálogo incremental, sem acesso à central.

O chamador legado decide SE e QUANDO registrar: esta função só prepara a
variante e retorna o caminho e os metadados. Preserva o nome/identidade original.
Não executar sobre replays já registrados na central.
"""
from pathlib import Path
import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile

from .legacy_rotation import rotate_replay, rotation_for_court


def prepare_rotation_handoff(source, output_root, *, court_key, local_id, applied_state,
                             ffmpeg="ffmpeg", ffprobe="ffprobe"):
    """Prepara variante em diretório isolado e determinístico.

    O consumidor deve verificar que local_id ainda não existe no catálogo
    ANTES de chamar, pois essa função NÃO modifica SQLite nem faz sync.
    Rotação e revisão ficam fixadas na primeira tentativa, mesmo se o Portal
    aplicar nova revisão posteriormente.
    """
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,120}", local_id):
        raise ValueError("local_id inválido")
    if not re.fullmatch(r"[A-Za-z0-9_]{2,80}", court_key):
        raise ValueError("identificador de quadra inválido")
    source = Path(source).resolve(strict=True)
    root = Path(output_root).resolve(strict=True)
    if not root.is_dir() or source.is_relative_to(root):
        raise ValueError("origem ou diretório de saída inválido")

    # O caminho é independente do ângulo: o mesmo replay nunca muda de
    # revisão após uma interrupção ou reprocessamento.
    key = hashlib.sha256((court_key + ":" + local_id).encode()).hexdigest()
    target = root / key
    lock_path = root / (".lock-" + key)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.exists():
            manifest = json.loads((target / "receipt.json").read_text(encoding="utf-8"))
            if (manifest.get("localId") != local_id
                    or manifest.get("courtKey") != court_key
                    or manifest.get("source") != str(source)):
                raise ValueError("identidade conflita com publicação anterior")
            video = target / "player.mp4"
            if not video.is_file() or video.stat().st_size != manifest["bytes"]:
                raise ValueError("variante registrada ausente ou alterada")
            return {**manifest, "path": str(video)}

        version, angle = rotation_for_court(applied_state, court_key)
        with tempfile.TemporaryDirectory(prefix=".rotation-", dir=root) as temp:
            staging = Path(temp)
            result = rotate_replay(source, staging / "player.mp4", angle,
                                   ffmpeg=ffmpeg, ffprobe=ffprobe)
            manifest = {
                "localId": local_id, "courtKey": court_key,
                "source": str(source), "configVersion": version,
                "rotationAppliedDeg": angle, "rotationDeg": 0,
                "width": result["width"], "height": result["height"],
                "bytes": result["bytes"],
            }
            (staging / "receipt.json").write_text(json.dumps(manifest), encoding="utf-8")
            # Mesmo filesystem: diretório final só aparece após toda a variante
            # e o comprovante terem sido gravados. Sem sobrescrever destino.
            os.rename(staging, target)
            return {**manifest, "path": str(target / "player.mp4")}
