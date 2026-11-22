#!/usr/bin/env python3
import csv
import datetime
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

BASE = Path("/srv/replayagora/quadra01")
BUFFER = BASE / "buffer"
REPLAYS = BASE / "replays"
STAGING = BASE / "staging"
LISTA = BUFFER / "segmentos.csv"
LOG = Path("/srv/replayagora/logs/botoeira-quadra01.log")
OFFSET = BASE / "app" / "offset.txt"

DURACAO = 15.0

def registrar(msg):
    agora = datetime.datetime.now(datetime.timezone.utc)
    print(agora.isoformat(timespec="seconds"), msg, flush=True)

def segmentos_anteriores(instante):
    if not LISTA.exists():
        return []

    try:
        with LISTA.open(newline="") as f:
            linhas = list(csv.reader(f))
    except (OSError, csv.Error):
        return []

    itens = []

    for linha in linhas:
        if len(linha) < 3:
            continue

        arquivo = BUFFER / Path(linha[0]).name

        try:
            inicio = float(linha[1])
            fim = float(linha[2])
            stat = arquivo.stat()
        except (ValueError, OSError):
            continue

        duracao = fim - inicio

        if not (0 < duracao <= 30):
            continue

        if stat.st_mtime > instante:
            continue

        itens.append((arquivo, duracao, stat.st_mtime))

    itens.sort(key=lambda item: item[2])

    selecionados = []
    acumulado = 0.0

    for arquivo, duracao, _ in reversed(itens):
        selecionados.append((arquivo, duracao))
        acumulado += duracao

        if acumulado >= DURACAO:
            break

    if acumulado < DURACAO:
        registrar(
            f"BUFFER INSUFICIENTE: {acumulado:.2f}s"
        )
        return []

    selecionados.reverse()
    return selecionados

def salvar(instante):
    time.sleep(0.3)

    selecionados = segmentos_anteriores(instante)

    if not selecionados:
        registrar("REPLAY NAO SALVO: buffer insuficiente")
        return False

    identificador = uuid.uuid4().hex[:8]
    pasta = STAGING / identificador
    pasta.mkdir(parents=True, mode=0o700)

    try:
        copias = []

        for numero, (origem, _) in enumerate(selecionados):
            destino = pasta / f"{numero:04d}.ts"
            shutil.copy2(origem, destino)
            copias.append(destino)

        lista = pasta / "concat.txt"

        with lista.open("w") as f:
            for arquivo in copias:
                f.write(f"file '{arquivo.name}'\n")

        data = datetime.datetime.fromtimestamp(
            instante, datetime.timezone.utc
        ).strftime("%Y%m%d-%H%M%S")

        final = REPLAYS / (
            f"replay-{data}-{identificador}.mp4"
        )

        temporario = pasta / "replay.mp4"

        comando = [
            "/usr/bin/ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel", "error",
            "-f", "concat",
            "-safe", "0",
            "-i", str(lista),
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-c", "copy",
            "-movflags", "+faststart",
            "-avoid_negative_ts", "make_zero",
            "-y", str(temporario)
        ]

        resultado = subprocess.run(
            comando,
            capture_output=True,
            text=True,
            timeout=90
        )

        if resultado.returncode != 0:
            registrar(
                "ERRO AO GERAR MP4: "
                + resultado.stderr[-500:]
            )
            return False

        if not temporario.exists() or temporario.stat().st_size == 0:
            registrar("ERRO: MP4 vazio")
            return False

        duracao_real = "indisponivel"

        probe = subprocess.run(
            [
                "/usr/bin/ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(temporario)
            ],
            capture_output=True,
            text=True,
            timeout=20
        )

        if probe.returncode == 0:
            duracao_real = probe.stdout.strip()

        os.replace(temporario, final)

        registrar(
            f"REPLAY SALVO: {final} "
            f"duracao_real={duracao_real}s"
        )
        return True

    except Exception as erro:
        registrar(f"ERRO NO REPLAY: {erro}")
        return False

    finally:
        shutil.rmtree(pasta, ignore_errors=True)

def ler_offset():
    try:
        return int(OFFSET.read_text().strip())
    except (OSError, ValueError):
        return LOG.stat().st_size

def gravar_offset(posicao):
    temporario = OFFSET.with_suffix(".tmp")
    temporario.write_text(str(posicao))
    os.replace(temporario, OFFSET)

registrar("Processador de replay iniciado")

posicao = ler_offset()

while True:
    try:
        tamanho = LOG.stat().st_size

        if tamanho < posicao:
            posicao = 0

        with LOG.open("r", encoding="utf-8") as f:
            f.seek(posicao)

            while True:
                inicio_linha = f.tell()
                linha = f.readline()

                if not linha or not linha.endswith("\n"):
                    break

                proxima_posicao = f.tell()

                if "REPLAY ACIONADO - QUADRA 01" not in linha:
                    posicao = proxima_posicao
                    gravar_offset(posicao)
                    continue

                try:
                    instante = datetime.datetime.fromisoformat(
                        linha.split(" ", 1)[0]
                    ).timestamp()
                except (ValueError, IndexError):
                    registrar("ACIONAMENTO COM DATA INVALIDA")
                    posicao = proxima_posicao
                    gravar_offset(posicao)
                    continue

                registrar("BOTOEIRA ACIONADA")

                if salvar(instante):
                    posicao = proxima_posicao
                    gravar_offset(posicao)
                else:
                    idade = time.time() - instante

                    if idade >= 90:
                        registrar(
                            f"ACIONAMENTO EXPIRADO: {idade:.1f}s; "
                            "buffer circular indisponivel"
                        )
                        posicao = proxima_posicao
                        gravar_offset(posicao)
                    else:
                        registrar(
                            "REPLAY PENDENTE: acionamento preservado"
                        )
                        break

    except Exception as erro:
        registrar(f"FALHA NA LEITURA DO LOG: {erro}")

    time.sleep(0.5)
