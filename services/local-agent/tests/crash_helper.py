"""Processo auxiliar: sobe o motor com renderização LENTA, enfileira acionamentos e é morto (SIGKILL) pelo teste."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

from replay_agent.engine import Engine
from replay_agent.render import SimRenderer
from replay_agent.store import LocalStore

root = Path(sys.argv[1])
rend = SimRenderer(delay_s=float(sys.argv[2]), size_bytes=1000)
store = LocalStore(str(root / "local.db"))
e = Engine("crash", root / "r", ["REPLAY_01", "REPLAY_02"], store, rend, dedup_s=0)
t0 = datetime(2026, 11, 5, 12, 0, 0)
for i in range(3):
    for k in ("REPLAY_01", "REPLAY_02"):
        e.trigger(k, t0 + timedelta(seconds=10 * i))
print("READY", flush=True)
import time
time.sleep(600)
