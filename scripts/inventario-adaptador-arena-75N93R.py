#!/usr/bin/env python3
"""Replay Agora 75N.93R — inventário somente leitura do sincronizador da arena.

Não abre fontes .env/credenciais; não imprime valores do banco ou configurações.
Somente stdlib. Não usa sudo nem escreve arquivos. Saída segura para compartilhar
após revisão humana (nomes de arquivos e colunas podem ser sensíveis).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess

EXPECTED_HOST = "replayagora-srv01"
ROOT = Path("/opt/replayagora-stage")
CATALOG = ROOT / "media-only/catalogo.sqlite"
FILES = [
    ROOT / "adaptador-incremental/auto_replays.py",
    ROOT / "media-only/relay_service.py",
    ROOT / "heartbeat.py",
    Path("/srv/replayagora/quadra01/app/buffer.py"),
    Path("/srv/replayagora/quadra01/app/replay.py"),
    Path("/srv/replayagora/quadra02/app/buffer.py"),
    Path("/srv/replayagora/quadra02/app/replay.py"),
]
UNITS = [
    "replayagora-auto-replays.service",
    "replayagora-auto-replays.timer",
    "replayagora-heartbeat-stage.service",
    "replayagora-media-relay.service",
    "replayagora-buffer01.service",
    "replayagora-buffer02.service",
    "replayagora-replay01.service",
    "replayagora-replay02.service",
]
# Nunca mostrar ExecStart/Environment: podem conter tokens, URLs assinadas e senhas.
PROPS = ["LoadState", "ActiveState", "SubState", "Type", "Result",
         "ExecMainStatus", "UnitFileState", "LastTriggerUSec",
         "NextElapseUSecRealtime"]


def run(args: list[str]) -> str:
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           timeout=8, check=False)
        if p.returncode not in (0, 1, 3, 4):
            return "indisponivel"
        return p.stdout.strip() or "sem retorno"
    except (OSError, subprocess.TimeoutExpired):
        return "indisponivel"


def fingerprint(path: Path) -> None:
    # lstat evita percorrer symlink; arquivos regulares pequenos somente.
    if path.is_symlink():
        print(f"{path}: LINK SIMBOLICO (nao seguido)")
        return
    if not path.is_file():
        print(f"{path}: ausente/inacessivel")
        return
    st = path.stat()
    if st.st_size > 3 * 1024 * 1024:
        print(f"{path}: presente, tamanho={st.st_size}, SHA nao calculado (>3MiB)")
        return
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        print(f"{path}: presente, sem permissao para leitura")
        return
    print(f"{path}: size={st.st_size}, sha256={h.hexdigest()}")


def schema_readonly(path: Path) -> None:
    print(f"Catalogo: {path}")
    if not path.is_file():
        print("  ausente/inacessivel")
        return
    # immutable=1 garante que SQLite nao cria -wal/-shm e nem escreve.
    # Schema pode estar defasado se houver transacao WAL em curso.
    uri = f"{path.as_uri()}?mode=ro&immutable=1"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=2)
        try:
            con.execute("PRAGMA query_only=ON")
            rows = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            print(f"  tabelas: {len(rows)}")
            for (name,) in rows[:30]:
                # nomes entre aspas e escapados, sem consulta de linhas
                escaped = name.replace('"', '""')
                cols = con.execute(f'PRAGMA table_info("{escaped}")').fetchall()
                # nomes podem revelar detalhes operacionais, mas NUNCA valores.
                print(f"  {name}: {', '.join(col[1] for col in cols)}")
            if len(rows) > 30:
                print("  outras tabelas omitidas")
        finally:
            con.close()
    except (OSError, sqlite3.Error) as exc:
        print(f"  leitura indisponivel: {type(exc).__name__}")


def main() -> int:
    print("=== Replay Agora 75N.93R: inventario de compatibilidade (somente leitura) ===")
    host = run(["hostname"])
    print("hostname:", host)
    if host != EXPECTED_HOST:
        print("BLOQUEADO: executar somente no servidor da arena")
        return 2
    print("\n=== unidades systemd: estado sem credenciais ===")
    for unit in UNITS:
        values = run(["systemctl", "show", unit,
                      "--no-pager", *[f"-p{p}" for p in PROPS]])
        print(f"\n{unit}")
        for line in values.splitlines():
            if line.startswith(tuple(p + "=" for p in PROPS)):
                print(" ", line)
    print("\n=== SHA256 do codigo operacional: sem mostrar fonte ===")
    for path in FILES:
        fingerprint(path)
    print("\n=== catalogo SQLite: somente nomes de tabelas/colunas ===")
    schema_readonly(CATALOG)
    print("\n=== diretórios de mídia: somente existencia ===")
    for folder in [
        "/srv/replayagora-storage/quadra01/replays",
        "/srv/replayagora-storage/quadra02/replays",
        "/srv/replayagora/quadra01/replays",
        "/srv/replayagora/quadra02/replays",
    ]:
        p = Path(folder)
        print(folder, "existe" if p.is_dir() else "ausente")
    print("\nNenhum servico, banco ou arquivo foi modificado por este diagnostico.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
