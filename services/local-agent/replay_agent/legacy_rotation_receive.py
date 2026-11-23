"""Recebe a orientação pendente para conferência local, sem aplicar a revisão."""
import argparse
from contextlib import contextmanager
import fcntl
from getpass import GetPassWarning, getpass
import json
import os
from pathlib import Path
import sys
import warnings
from urllib.parse import quote

from .legacy_rotation import FILTERS
from .legacy_rotation_portal import portal_getter
from .legacy_rotation_shadow import COURT_KEY
from .remote import SCHEMA
from .vault import _atomic_write


class RotationReceipt:
    """Callback do heartbeat. read_revision consulta a revisão via Portal autenticado."""

    def __init__(self, directory, server_id, arena_id, read_revision):
        self.directory = directory
        self.path = directory / "rotation-receipt.json"
        self.server_id = server_id
        self.arena_id = arena_id
        self.read_revision = read_revision

    @contextmanager
    def _state(self):
        with (self.directory / "rotation-receipt.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                state = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                state = {"serverId": self.server_id, "arenaId": self.arena_id,
                         "highestVersion": 0, "received": None, "confirmed": None, "previous": None}
            if (state["serverId"], state["arenaId"]) != (self.server_id, self.arena_id):
                raise ValueError("recibo pertence a outro servidor ou arena")
            yield state

    def _save(self, state):
        _atomic_write(self.path, json.dumps(state, allow_nan=False).encode())

    def tick(self, hint):
        """Recebe a versão indicada pelo heartbeat e persiste antes de retornar."""
        if not hint or hint.get("linked") is not True:
            return None
        version = hint.get("pendingVersion")
        if version is None:
            return None
        if type(version) is not int or version < 1:
            raise ValueError("versão pendente inválida")
        with self._state() as state:
            if version <= state["highestVersion"]:
                return None
        revision = self.read_revision(version)
        snapshot = revision["snapshot"]
        if (revision.get("serverId") != self.server_id
                or type(revision.get("version")) is not int or revision["version"] != version
                or revision.get("status") != "pendente"
                or not isinstance(revision.get("id"), str) or not revision["id"]
                or snapshot.get("serverId") != self.server_id
                or snapshot.get("arenaId") != self.arena_id
                or type(snapshot.get("schema")) is not int or snapshot["schema"] != SCHEMA):
            raise ValueError("revisão pendente não corresponde ao servidor, arena ou esquema")
        courts = [court for court in snapshot["courts"] if court.get("key") == COURT_KEY]
        if len(courts) != 1 or courts[0].get("active") is not True:
            raise ValueError("Quadra 02 ausente, inativa ou duplicada")
        angle = (courts[0].get("camera") or {}).get("rotationDeg")
        if type(angle) is not int or angle not in FILTERS:
            raise ValueError("orientação da Quadra 02 inválida ou ausente")
        receipt = {"revisionId": revision["id"], "version": version, "courtKey": COURT_KEY,
                   "rotationDeg": angle}
        with self._state() as state:
            if version <= state["highestVersion"]:
                return None
            state.update(highestVersion=version, received=receipt)
            self._save(state)
        return receipt

    def confirm(self, revision_id):
        """Confirma apenas a conferência local do recibo persistido."""
        with self._state() as state:
            received = state["received"]
            if not received or received["revisionId"] != revision_id:
                confirmed = state["confirmed"]
                if confirmed and confirmed["revisionId"] == revision_id:
                    return confirmed
                raise ValueError("recibo não está disponível para confirmação")
            state.update(previous=state["confirmed"], confirmed=received, received=None)
            self._save(state)
            return received

    def rollback(self):
        """Descarta o recebimento pendente e restaura a última confirmação anterior."""
        with self._state() as state:
            if state["confirmed"] is None and state["received"] is None:
                return False
            if state["received"] is None and state["confirmed"]["version"] != state["highestVersion"]:
                return False
            if state["received"] is None:
                state.update(confirmed=state["previous"], previous=None)
            state["received"] = None
            self._save(state)
            return True


def portal_receipt(directory, server_id, arena_id, token):
    """Liga o callback à leitura HTTPS de uma revisão no Portal Dev."""
    get_json = portal_getter(token)
    server_path = "/api/platform/servers/" + quote(server_id, safe="")
    def read_revision(version):
        revision = get_json(f"{server_path}/revisions/{version}")
        server = get_json(server_path)
        if (server.get("id") != server_id or server.get("arenaId") != arena_id
                or server.get("status") == "revoked" or server.get("hostname") != "replayagora-srv01"
                or server.get("latestVersion") != version or server.get("latestStatus") != "pendente"):
            raise ValueError("servidor, arena ou revisão vigente divergem do Portal")
        return revision

    return RotationReceipt(directory, server_id, arena_id, read_revision)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-id", required=True)
    parser.add_argument("--arena-id", required=True)
    parser.add_argument("--version", required=True, type=int)
    parser.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    if args.version < 1 or not sys.stdin.isatty():
        parser.error("informe versão positiva e use um terminal interativo")
    try:
        info = args.directory.stat()
        if not args.directory.is_dir() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("use uma pasta privada de sua propriedade")
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            token = getpass("Token da sessão MFA do Portal Dev: ").strip()
        if not token:
            raise ValueError("sessão ausente")
        adapter = portal_receipt(args.directory, args.server_id, args.arena_id, token)
        # A versão solicitada é validada contra os dois recursos autenticados.
        receipt = adapter.tick({"linked": True, "pendingVersion": args.version})
        if receipt is None:
            raise ValueError("versão já processada neste diretório")
    except (GetPassWarning, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        parser.exit(1, f"Consulta interrompida ({type(exc).__name__}).\n")
    print(json.dumps({"status": "orientacao_recebida", **receipt}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
