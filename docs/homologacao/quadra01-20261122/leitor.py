#!/usr/bin/env python3
import os
import sys
import time
import termios
import select
import datetime

SERIAL = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
LOG = "/srv/replayagora/logs/botoeira-quadra01.log"

def registrar(mensagem):
    data = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    linha = f"{data} {mensagem}"
    print(linha, flush=True)
    with open(LOG, "a", encoding="utf-8") as arquivo:
        arquivo.write(linha + "\n")

def monitorar():
    fd = os.open(SERIAL, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] = 0
        attrs[1] = 0
        attrs[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
        attrs[3] = 0
        attrs[4] = termios.B9600
        attrs[5] = termios.B9600
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)

        registrar("ARDUINO CONECTADO - QUADRA 01")
        buffer = bytearray()

        while True:
            pronto, _, _ = select.select([fd], [], [], 1)
            if not pronto:
                continue

            dados = os.read(fd, 1024)
            if not dados:
                raise OSError("Porta serial desconectada")

            buffer.extend(dados)

            if len(buffer) > 4096:
                buffer.clear()
                registrar("AVISO: buffer serial descartado")

            while b"\n" in buffer:
                linha, _, restante = buffer.partition(b"\n")
                buffer = bytearray(restante)
                comando = linha.decode("ascii", errors="replace").strip()

                if comando in ("REPLAY", "REPLAY_01"):
                    registrar("REPLAY ACIONADO - QUADRA 01")
                elif comando == "REPLAY_02":
                    registrar("REPLAY ACIONADO - QUADRA 02")
                elif comando:
                    registrar(f"SERIAL: {comando[:100]}")
    finally:
        os.close(fd)

while True:
    try:
        monitorar()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as erro:
        registrar(f"AGUARDANDO RECONEXAO: {erro}")
        time.sleep(3)
