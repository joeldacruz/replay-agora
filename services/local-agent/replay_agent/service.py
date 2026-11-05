"""Serviço do agente da arena: captura · acionamento · renderização · entrega · ligação com a central, em um único processo.

Cada responsabilidade roda em threads próprias: a captura (FFmpeg `-c copy`) nunca espera render, relay ou rede.
"""
import json
import logging
import socket
import threading
from pathlib import Path

from . import __version__, diagnostics
from .assemble import Stack, build_stack
from .client import CentralClient, make_http
from .config import AgentCfg
from .creds import CredentialStore
from .link import CentralLink
from .metrics import SystemSampler
from .relay import RelayServer
from .renderjobs import RenderWorker

log = logging.getLogger("replay_agent")


class AgentService:
    def __init__(self, cfg: AgentCfg, http=None, credentials: dict | None = None):
        self.cfg = cfg
        self.stack: Stack = build_stack(cfg)
        data = Path(cfg.data_dir)
        cstore = CredentialStore(cfg.central.credentials_file)
        creds = credentials or cstore.load() or {}
        self.stop_flag = threading.Event()
        self.threads: list[threading.Thread] = []
        self.client = None
        self.link = self.relay = self.render = None
        if cfg.central.base_url or http is not None:
            http = http or make_http(cfg.central.base_url, (cfg.central.client_cert, cfg.central.client_key) if cfg.central.client_cert else None,
                                     cfg.central.ca_file)
            self.client = CentralClient(http, creds.get("token", ""), cfg.central.node_id or socket.gethostname(), creds.get("signingKey"))
            self.sampler = SystemSampler(str(data))
            self.link = CentralLink(self.client, self.stack.store, self.stack.engine, hostname=socket.gethostname(), sampler=self.sampler,
                                    creds=cstore, governor=self.stack.governor)
            self.relay = RelayServer(self.client, self.stack.store, [data])
            self.render = RenderWorker(self.client, self.stack.store, data / "renders", cfg.ffmpeg_bin, cfg.ffprobe_bin, nice=cfg.resources.render_nice,
                                       allowed_source_roots=[data / "replays"], governor=self.stack.governor)
            self.link.diag_provider = self.diagnostics
            self.link.command_handler = self.handle_command

    def diagnostics(self) -> dict:
        s = self.stack
        return diagnostics.collect(self.cfg, s.store, s.recorders, s.indexes, s.engine, s.triggers, s.router, s.governor,
                                   getattr(self, "sampler", None), self.link, self.relay, self.render, __version__)

    def handle_command(self, cmd: dict) -> dict:
        if cmd.get("kind") == "test_trigger" and cmd.get("courtKey") in self.stack.engine.workers:
            r = self.stack.router.manual(cmd["courtKey"], str(cmd.get("by", "painel"))[:40])
            return {"outcome": r["outcome"], "jobId": r.get("jobId")}
        return {"outcome": "unsupported"}

    def start(self, interval_s: float = 30.0) -> None:
        self.stack.start()
        if self.link:
            self.threads.append(threading.Thread(target=self.link.run_forever, args=(self.stop_flag, interval_s), daemon=True, name="link"))
            self.threads += self.relay.start(self.stop_flag, workers=2, wait=20)
            self.threads.append(threading.Thread(target=self.render.run_forever, args=(self.stop_flag,), daemon=True, name="render"))
            for t in self.threads:
                if not t.is_alive():
                    t.start()

    def stop(self) -> None:
        self.stop_flag.set()
        for t in self.threads:
            t.join(timeout=5)
        self.stack.stop()


def main(argv: list[str] | None = None) -> int:
    import argparse
    import signal
    from .config import ConfigError, load_config
    from .resources import detect_hw
    ap = argparse.ArgumentParser(prog="replay_agent", description="Agente local da arena (captura, acionamento, render e entrega)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "check-config", "diagnose"):
        sp = sub.add_parser(name)
        sp.add_argument("--config", required=True)
    sub.add_parser("hwinfo")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if a.cmd == "hwinfo":
        print(json.dumps(detect_hw(), indent=2))
        return 0
    try:
        cfg = load_config(a.config)
    except (ConfigError, OSError) as e:
        print(f"configuração inválida: {e}")
        return 2
    if a.cmd == "check-config":
        print(json.dumps(cfg.public_summary(), indent=2, ensure_ascii=False))
        return 0
    svc = AgentService(cfg)
    if a.cmd == "diagnose":
        print(json.dumps(svc.diagnostics(), indent=2, default=str, ensure_ascii=False))
        svc.stack.engine.stop()
        return 0
    svc.start()
    done = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: done.set())
    log.info("agente iniciado: %d quadra(s)", len(cfg.courts))
    done.wait()
    svc.stop()
    return 0
