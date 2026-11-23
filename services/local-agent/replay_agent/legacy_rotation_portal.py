"""Exporta a orientação aplicada da Quadra 02 para um ensaio avulso."""
import argparse
from getpass import getpass
import json
import os
from pathlib import Path
import sys
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .legacy_rotation import rotation_for_court
from .legacy_rotation_shadow import COURT_KEY

ORIGIN = "https://replayagora.com.br"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def portal_getter(token):
    opener = build_opener(NoRedirect())

    def get_json(path):
        req = Request(ORIGIN + path, headers={"Authorization": "Bearer " + token})
        with opener.open(req, timeout=15) as response:
            return json.load(response)

    return get_json


def export_state(get_json, server_id, version):
    if version < 1:
        raise ValueError("a versão esperada deve ser positiva")
    server_path = "/api/platform/servers/" + quote(server_id, safe="")
    revision = get_json(f"{server_path}/revisions/{version}")
    server = get_json(server_path)
    result = revision.get("result") or {}
    items = result.get("items") or []
    if (server.get("id") != server_id or server.get("status") == "revoked"
            or server.get("hostname") != "replayagora-srv01"
            or type(server.get("appliedVersion")) is not int
            or server["appliedVersion"] != version
            or revision.get("serverId") != server_id or revision.get("version") != version
            or revision.get("status") != "aplicado" or not revision.get("finishedAt")
            or result.get("activeVersion") != version or result.get("rolledBack") is not False
            or not items
            or any(item.get("ok") is not True for item in items)
            or (len(items) == 1 and items[0].get("kind") == "replay"
                and items[0].get("key") == "")):
        raise ValueError("revisão esperada sem confirmação ativa para replayagora-srv01")
    snapshot = revision["snapshot"]
    if snapshot.get("serverId") != server_id or snapshot.get("arenaId") != server.get("arenaId"):
        raise ValueError("snapshot divergente do servidor ou da arena")
    _, angle = rotation_for_court({"activeVersion": version, "snapshot": snapshot}, COURT_KEY)
    return {
        "activeVersion": version,
        "snapshot": {"courts": [
            {"key": COURT_KEY, "active": True, "camera": {"rotationDeg": angle}},
        ]},
        "origin": {"url": ORIGIN, "serverId": server_id, "revisionId": revision["id"],
                   "finishedAt": revision["finishedAt"], "revertedFrom": revision.get("revertedFrom")},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-id", required=True)
    parser.add_argument("--version", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error("a sessão do Portal deve ser informada em um terminal interativo")
    token = getpass("Token da sessão MFA do Portal Dev: ").strip()
    if not token:
        parser.error("sessão do Portal Dev ausente")
    try:
        state = export_state(portal_getter(token), args.server_id, args.version)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump(state, target)
            target.write("\n")
    except (URLError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        parser.exit(1, f"Exportação interrompida ({type(exc).__name__}).\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
