#!/usr/bin/env python3
import ast
import datetime as dt
import fcntl
import hashlib
import json
import os
import runpy
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path("/opt/replayagora-stage")
DIR = ROOT / "miniaturas"
DB = ROOT / "media-only/catalogo.sqlite"
AUTO = ROOT / "adaptador-incremental/auto_replays.py"
STATUS = DIR / "upload-status.json"
IMAGES = DIR / "imagens"

def log(*args):
    print(*args, flush=True)

def credentials_path():
    tree = ast.parse(AUTO.read_text(encoding="utf-8"))

    def resolve(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            return Path(n.value)
        if isinstance(n, ast.Name):
            if n.id == "ROOT":
                return ROOT
            if n.id == "ADAPTER":
                return ROOT / "adaptador-incremental"
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            if n.func.id == "Path" and len(n.args) == 1:
                return resolve(n.args[0])
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            return resolve(n.left) / resolve(n.right)
        raise ValueError("Caminho nao reconhecido")

    for n in tree.body:
        if isinstance(n, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "CREDS"
                   for t in n.targets):
                return resolve(n.value)
    raise ValueError("CREDS nao encontrado")

def save(data):
    fd, tmp = tempfile.mkstemp(prefix=".status-", dir=DIR)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATUS)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def gerar(video, destino):
    for q in (5, 8, 12, 18):
        with tempfile.TemporaryDirectory(dir=DIR) as tmp:
            saida = Path(tmp) / "imagem.jpg"
            cmd = [
                "/usr/bin/ffmpeg", "-nostdin",
                "-hide_banner", "-loglevel", "error",
                "-ss", "1", "-i", str(video),
                "-frames:v", "1",
                "-vf", "scale=640:-2",
                "-q:v", str(q),
                "-y", str(saida)
            ]
            try:
                p = subprocess.run(
                    cmd, capture_output=True, timeout=30
                )
            except subprocess.TimeoutExpired:
                continue

            if p.returncode or not saida.is_file():
                continue

            data = saida.read_bytes()
            if (64 <= len(data) <= 262144
                    and data.startswith(b"\xff\xd8")
                    and data.endswith(b"\xff\xd9")):
                fd, temp = tempfile.mkstemp(
                    prefix=".jpeg-", dir=IMAGES
                )
                try:
                    os.fchmod(fd, 0o644)
                    with os.fdopen(fd, "wb") as f:
                        f.write(data)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(temp, destino)
                finally:
                    if os.path.exists(temp):
                        os.unlink(temp)
                return data
    raise ValueError("Nao foi possivel gerar JPEG valido")

def main():
    if os.uname().nodename != "replayagora-srv01":
        raise RuntimeError("Servidor incorreto")

    execute = "--execute" in sys.argv
    limite = dt.datetime.fromisoformat(
        (DIR / "inicio-automatico.txt").read_text().strip()
    )

    with sqlite3.connect(
        DB.resolve().as_uri() + "?mode=ro", uri=True
    ) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("""
            SELECT local_id, court_key, captured_at, path
            FROM replays
            WHERE sync_state='synced'
              AND deleted=0
              AND file_ok=1
              AND court_key IN ('QUADRA_01','QUADRA_02')
            ORDER BY captured_at
        """).fetchall()

    novos = []
    for row in rows:
        try:
            captura = dt.datetime.fromisoformat(
                row["captured_at"].replace("Z", "+00:00")
            )
            if captura.tzinfo is None or captura < limite:
                continue
        except (ValueError, TypeError):
            continue
        novos.append(dict(row))

    estado = json.loads(STATUS.read_text()) if STATUS.exists() else {}
    if not isinstance(estado, dict):
        raise ValueError("Registro de uploads invalido")

    pendentes = [
        r for r in novos
        if not (
            isinstance(estado.get(r["local_id"]), dict)
            and estado[r["local_id"]].get("ok") is True
        )
    ]

    log("Novos replays:", len(novos))
    log("Pendentes:", len(pendentes))

    if not execute:
        log("SIMULACAO: nenhum JPEG gerado ou enviado")
        return

    if not pendentes:
        log("SEM PENDENCIAS")
        return

    sys.path.insert(
        0, str(ROOT / "repo/services/local-agent")
    )
    from replay_agent.client import CentralClient, make_http

    launcher = runpy.run_path(
        str(ROOT / "media-only/relay_service.py"),
        run_name="miniaturas_config"
    )
    url, node = launcher["identity"]()
    if not isinstance(url, str) or not url.startswith("https://") or not node:
        raise ValueError("Identidade HTTPS invalida")

    cred = json.loads(credentials_path().read_text())
    if not cred.get("token") or not cred.get("signingKey"):
        raise ValueError("Credenciais incompletas")

    falhas = 0
    confirmados = 0

    with make_http(url, timeout=20) as http:
        client = CentralClient(
            http, cred["token"], node, cred["signingKey"]
        )

        for r in pendentes:
            local_id = r["local_id"]

            # A miniatura pertence somente ao MP4
            # registrado no catalogo.
            video = Path(r["path"])
            if not video.is_file() or video.stat().st_size == 0:
                log("VIDEO INDISPONIVEL:", r["court_key"])
                falhas += 1
                continue

            destino = IMAGES / (local_id + ".jpg")

            try:
                if destino.is_file():
                    dados = destino.read_bytes()
                else:
                    dados = gerar(video, destino)

                if not (
                    64 <= len(dados) <= 262144
                    and dados.startswith(b"\xff\xd8")
                    and dados.endswith(b"\xff\xd9")
                ):
                    raise ValueError("JPEG fora dos limites")

                sha = hashlib.sha256(dados).hexdigest()

                resposta = client.request_raw(
                    "POST",
                    f"/sync/replays/{local_id}/thumbnail",
                    content=dados,
                    headers={
                        "Content-Type": "image/jpeg",
                        "X-Thumb-SHA256": sha
                    },
                    timeout=30
                )

                resultado = resposta.json()
                if not isinstance(resultado, dict) or resultado.get("ok") is not True:
                    raise ValueError("Central nao confirmou JPEG")

                estado[local_id] = {
                    "ok": True,
                    "courtKey": r["court_key"],
                    "sha256": sha,
                    "bytes": len(dados),
                    "status": resultado.get("status")
                }
                save(estado)
                confirmados += 1
                log("UPLOAD OK:", r["court_key"], resultado.get("status"))

            except Exception as e:
                falhas += 1
                log("FALHA:", r["court_key"], type(e).__name__)

    log("Confirmados:", confirmados)
    log("Falhas:", falhas)
    if falhas:
        raise SystemExit(1)

if __name__ == "__main__":
    DIR.mkdir(exist_ok=True)
    IMAGES.mkdir(exist_ok=True)
    with open(DIR / ".sincronizar.lock", "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Outra sincronizacao em andamento")
            raise SystemExit(0)
        main()
