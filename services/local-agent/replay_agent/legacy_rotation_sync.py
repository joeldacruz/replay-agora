"""Sincroniza somente o ângulo desejado da Quadra 02, via credenciais do agente.

A revisão remota integral NÃO é marcada como aplicada. Este arquivo é uma
política independente: 'orientação desejada recebida' ≠ 'v4 aplicada'.
A implantação deve passar um CentralClient autenticado já existente e escolher
uma pasta privada FORA dos diretórios monitorados de replay.
"""
import json
import os
from pathlib import Path
import tempfile
import fcntl

COURT_KEY = "QUADRA_02"
ALLOWED_ANGLES = (0, 90, 180, 270)


def fetch_rotation(client, *, expected_server_id=None, expected_arena_id=None):
    """Busca a configuração projetada, sem usar /remote/pending ou alterar status."""
    value = client.call("GET", f"/remote/rotation/{COURT_KEY}")
    if not isinstance(value, dict):
        raise ValueError("resposta de orientação inválida")
    if value.get("courtKey") != COURT_KEY:
        raise ValueError("quadra divergente")
    if not isinstance(value.get("serverId"), str) or not value["serverId"]:
        raise ValueError("identidade de servidor ausente")
    if not isinstance(value.get("arenaId"), str) or not value["arenaId"]:
        raise ValueError("identidade de arena ausente")
    if expected_server_id is not None and value["serverId"] != expected_server_id:
        raise ValueError("servidor divergente")
    if expected_arena_id is not None and value["arenaId"] != expected_arena_id:
        raise ValueError("arena divergente")
    angle = value.get("rotationDeg")
    if type(angle) is not int or angle not in ALLOWED_ANGLES:
        raise ValueError("ângulo de rotação inválido")
    version = value.get("version")
    if type(version) is not int or version < 1:
        raise ValueError("versão inválida")
    if not isinstance(value.get("revisionId"), str) or not value["revisionId"]:
        raise ValueError("identidade de revisão inválida")
    status = value.get("configurationState")
    if status not in ("desired_unapplied", "applied"):
        raise ValueError("estado da orientação desconhecido")
    if value.get("revisionStatus") not in (
            "salvo", "pendente", "enviado", "recebido", "aplicando", "aplicado"):
        raise ValueError("status de revisão não utilizável")
    return {
        "serverId": value["serverId"], "arenaId": value["arenaId"],
        "courtKey": COURT_KEY, "revisionId": value["revisionId"],
        "version": version, "rotationDeg": angle,
        "configurationState": status, "revisionStatus": value["revisionStatus"],
    }


def receive_rotation(client, target: Path, *, expected_server_id=None, expected_arena_id=None):
    """Persiste um recibo privado, atômico e antirregressão de orientação.

    Não chama FFmpeg, não toca no catálogo, não cria serviços e não apaga replays.
    Operação opt-in: o chamador deve verificar se a ligação foi homologada.
    """
    target = Path(target)
    parent = target.parent.resolve(strict=True)
    if target.name in ("", ".", "..") or target.is_symlink():
        raise ValueError("arquivo de orientação inválido")
    info = parent.stat()
    if info.st_mode & 0o077:
        raise ValueError("pasta de orientação não é privada")
    received = fetch_rotation(client, expected_server_id=expected_server_id,
                              expected_arena_id=expected_arena_id)
    lock_path = parent / (target.name + ".lock")
    with lock_path.open("a", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.exists():
            old = json.loads(target.read_text(encoding="utf-8"))
            if (old.get("serverId"), old.get("arenaId")) != (
                    received["serverId"], received["arenaId"]):
                raise ValueError("recibo existente é de outra instalação")
            if old["version"] > received["version"]:
                raise ValueError("regressão de revisão não autorizada")
            if old["version"] == received["version"]:
                if old["revisionId"] != received["revisionId"] or old["rotationDeg"] != received["rotationDeg"]:
                    raise ValueError("mesma versão com orientação divergente")
                return old
        fd, temp = tempfile.mkstemp(prefix=".orientation-", suffix=".json", dir=parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(received, file, sort_keys=True)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, target)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return received


def as_rotation_state(receipt: dict):
    """Exibe a orientação como entrada local para o motor, NÃO como aplicação de v4.

    O campo activeVersion é usado SOMENTE na interface de renderização legada.
    É intencional e não deve ser enviado a /remote/*/result nem ao Portal.
    """
    if receipt.get("courtKey") != COURT_KEY or receipt.get("rotationDeg") not in ALLOWED_ANGLES:
        raise ValueError("recibo inválido")
    return {
        "activeVersion": receipt["version"],
        "snapshot": {"courts": [
            {"key": COURT_KEY, "active": True, "camera": {"rotationDeg": receipt["rotationDeg"]}},
        ]},
    }
