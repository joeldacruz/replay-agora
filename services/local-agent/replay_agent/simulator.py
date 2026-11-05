"""Frota simulada: N arenas × M quadras, cada arena com seu próprio computador (store, motor, ligação).
Injeção de falhas: queda de rede, resposta perdida, câmera sem sinal, disco cheio."""
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .client import CentralClient
from .engine import Engine
from .link import CentralLink
from .metrics import SystemSampler
from .render import SimRenderer
from .store import LocalStore


class FaultyHttp:
    """Envolve um cliente HTTP (httpx.Client / TestClient) e injeta falhas de rede."""

    def __init__(self, inner):
        self.inner, self.down, self.lose_responses, self.lose_match = inner, False, 0, ""

    def request(self, method, url, **kw):
        if self.down:
            raise httpx.ConnectError("rede indisponível (simulado)")
        r = self.inner.request(method, url, **kw)
        if self.lose_responses > 0 and self.lose_match in url:  # o servidor PROCESSOU, mas o agente não viu a resposta
            self.lose_responses -= 1
            raise httpx.ReadTimeout("resposta perdida (simulado)")
        return r


@dataclass
class SimArena:
    tag: str
    token: str
    court_keys: list[str]
    http: FaultyHttp
    renderer: SimRenderer
    store: LocalStore
    engine: Engine
    link: CentralLink
    meta: dict = field(default_factory=dict)


def build_arena(tag: str, token: str, court_keys: list[str], http, root: Path | None = None, renderer: SimRenderer | None = None,
                **engine_kw) -> SimArena:
    root = Path(root or tempfile.mkdtemp(prefix=f"sim-{tag}-"))
    root.mkdir(parents=True, exist_ok=True)
    fh = FaultyHttp(http)
    renderer = renderer or SimRenderer()
    store = LocalStore(str(root / "local.db"))
    engine = Engine(tag, root / "replays", court_keys, store, renderer, **engine_kw)
    client = CentralClient(fh, token, node_id=f"node-{tag}")
    link = CentralLink(client, store, engine, hostname=f"sim-{tag}", sampler=SystemSampler(str(root)))
    return SimArena(tag, token, court_keys, fh, renderer, store, engine, link)


class Fleet:
    def __init__(self, arenas: list[SimArena]):
        self.arenas = arenas

    def trigger_all(self) -> list[dict]:
        return [a.engine.trigger(k) for a in self.arenas for k in a.court_keys]

    def wait_idle(self, timeout: float = 120) -> bool:
        return all(a.engine.wait_idle(timeout) for a in self.arenas)

    def tick_all(self) -> list[dict]:
        return [a.link.tick() for a in self.arenas]

    def stop(self) -> None:
        for a in self.arenas:
            a.engine.stop()
            a.store.close()
