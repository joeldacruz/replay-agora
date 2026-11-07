"""Agente SIMULADO para homologação da configuração remota (sem câmeras, Arduino ou FFmpeg de produção).

Executa um AgentService REAL (cofre cifrado, cache de molduras, reconfiguração por quadra, composição com FFmpeg) conectado à API central,
mas com: câmeras sintéticas (lavfi), portas seriais simuladas e resultados de teste de câmera scriptados por IP.
NÃO toca em equipamentos reais. Uso:
  python scripts/simulate_remote_agent.py --base-url http://127.0.0.1:8765 --credentials cred.json --data-dir /tmp/agente --courts REPLAY_01 REPLAY_02
Comportamento scriptado: *.19 -> câmera OK; *.18 -> falha de autenticação; *.99 -> porta fechada; demais -> OK.
"""
import argparse
import json
import signal
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "local-agent"))

import httpx  # noqa: E402

from replay_agent.config import parse  # noqa: E402
from replay_agent.devices import Devices, SimSerial  # noqa: E402
from replay_agent.service import AgentService  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--credentials", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--node", default="node-simulado-0001")
    ap.add_argument("--courts", nargs="+", default=["REPLAY_01", "REPLAY_02"])
    ap.add_argument("--interval", type=float, default=1.0)
    a = ap.parse_args()
    cred = json.loads(Path(a.credentials).read_text())
    raw = {"arena_tag": "simulado", "data_dir": a.data_dir, "courts": [{"key": k, "label": k, "camera": {"enabled": False}} for k in a.courts],
           "central": {"base_url": a.base_url, "node_id": a.node}}
    ports = [{"id": "by-id:usb-1a86_USB_Serial-if00-port0", "device": "/dev/ttyUSB0", "description": "CH340 simulado (Nano V3)", "stable": True, "vendorId": "1a86", "productId": "7523", "serial": None, "hint": "arduino_candidate"},
             {"id": "by-id:usb-1a86_USB_Serial-if00-port1", "device": "/dev/ttyUSB1", "description": "CH340 simulado 2", "stable": True, "vendorId": "1a86", "productId": "7523", "serial": None, "hint": "arduino_candidate"}]

    def connect(host, port, timeout):
        return "refused" if host.endswith(".99") else "ok"

    def runner(cmd, timeout):
        url = next((x for x in cmd if str(x).startswith("rtsp://")), "")
        host = url.split("@")[-1].split(":")[0]
        if "ffprobe" in cmd[0]:
            if host.endswith(".18"):
                return 1, b"", "Server returned 401 Unauthorized (authorization failed)"
            return 0, json.dumps({"streams": [{"codec_name": "h264", "width": 1280, "height": 720, "avg_frame_rate": "25/1"}]}).encode(), ""
        return 0, b"\xff\xd8\xff\xe0" + b"\x00" * 600, ""

    svc = AgentService(parse(raw), http=httpx.Client(base_url=a.base_url, timeout=30), credentials={"token": cred["token"], "signingKey": cred["signingKey"]},
                       devices=Devices(SimSerial(ports)), url_builder=lambda cam, sec: "lavfi:testsrc2=size=320x240:rate=15", runner=runner, connect=connect)
    svc.stack.start()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    print("agente simulado iniciado", flush=True)
    while not stop.is_set():
        svc.link.tick()
        stop.wait(a.interval)
    svc.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
