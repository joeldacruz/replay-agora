"""Processo auxiliar: agente completo (2 quadras) com vídeo real em laço; é morto com SIGKILL pelo teste."""
import json
import sys
import time
from pathlib import Path

from replay_agent.assemble import build_stack
from replay_agent.config import parse

root, v1, v2 = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
cfg = parse(json.loads((root / "agent.json").read_text()))
st = build_stack(cfg)
st.start()
deadline = time.time() + 40
while time.time() < deadline and min(r.index.stats(3.5)["availableSeconds"] for r in st.recorders.values()) < 6:
    time.sleep(0.2)
a = st.router.manual("REPLAY_01", "helper")
b = st.router.manual("REPLAY_02", "helper")
print("TRIGGERED", a["outcome"], b["outcome"], flush=True)
time.sleep(600)
