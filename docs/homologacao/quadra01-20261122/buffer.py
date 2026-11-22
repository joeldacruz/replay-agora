#!/usr/bin/env python3
import csv
import json
import os
import signal
import subprocess
import time
from pathlib import Path

BASE = Path("/srv/replayagora/quadra01")
BUFFER = BASE / "buffer"
CONFIG = BASE / "camera.env"
LISTA = BUFFER / "segmentos.csv"
RUNNING = True

def parar(*args):
    global RUNNING
    RUNNING = False

signal.signal(signal.SIGTERM, parar)
signal.signal(signal.SIGINT, parar)

def limpar():
    agora = time.time()

    # Preserva margem para replays em processamento.
    for arq in BUFFER.glob("*.ts"):
        try:
            if agora - arq.stat().st_mtime > 90:
                arq.unlink()
        except FileNotFoundError:
            pass

def executar():
    with CONFIG.open() as f:
        url = json.load(f)["rtsp"]

    comando = [
        "/usr/bin/ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel", "warning",
        "-rtsp_transport", "tcp",
        "-timeout", "10000000",
        "-i", url,
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-c", "copy",
        "-f", "segment",
        "-segment_time", "1",
        "-segment_format", "mpegts",
        "-segment_list", str(LISTA),
        "-segment_list_type", "csv",
        "-segment_list_size", "0",
        "-strftime", "1",
        str(BUFFER / "%Y%m%dT%H%M%S.ts")
    ]

    # Não envia a URL RTSP para o journal.
    with open(os.devnull, "wb") as destino:
        processo = subprocess.Popen(
            comando,
            stdout=destino,
            stderr=destino
        )

        print(
            f"Gravador iniciado. PID={processo.pid}",
            flush=True
        )

        while RUNNING and processo.poll() is None:
            limpar()
            time.sleep(2)

        if processo.poll() is None:
            processo.terminate()
            try:
                processo.wait(timeout=8)
            except subprocess.TimeoutExpired:
                processo.kill()
                processo.wait()

        print(
            f"Gravador encerrado. Codigo={processo.returncode}",
            flush=True
        )

while RUNNING:
    try:
        executar()
    except Exception as erro:
        print(f"Falha no gravador: {erro}", flush=True)

    if RUNNING:
        print("Tentando reconectar RTSP em 3 segundos...", flush=True)
        time.sleep(3)
