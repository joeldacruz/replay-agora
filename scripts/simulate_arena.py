#!/usr/bin/env python3
"""Simula o motor local da arena SEM câmeras: dispara eventos de botoeira (REPLAY_01 / REPLAY_02) e envia MP4 de amostra.

NÃO substitui nem toca o leitor real (/srv/replayagora/botoeira/leitor.py). Fala apenas com a API central indicada.
Uso:
  python scripts/simulate_arena.py --base-url http://127.0.0.1:8000 --token "$AGENT_TOKEN" --courts REPLAY_01 REPLAY_02
"""
import argparse
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--token", required=True, help="token de agente da arena (criado no app da arena ou pelo seed)")
    ap.add_argument("--courts", nargs="+", default=["REPLAY_01", "REPLAY_02"])
    ap.add_argument("--video", help="MP4 de amostra; se omitido, gera um sintético com FFmpeg")
    a = ap.parse_args()
    h = {"Authorization": f"Bearer {a.token}"}
    video = Path(a.video) if a.video else None
    if not video:
        from app import samples  # gerador sintético (sem direitos de terceiros)
        video = samples.make_video(Path(tempfile.mkdtemp()) / "sim.mp4", 5, True)
    with httpx.Client(base_url=a.base_url, headers=h, timeout=120) as c:
        for key in a.courts:
            idem = f"sim-{key}-{uuid.uuid4().hex[:12]}"
            now = datetime.now(timezone.utc).isoformat()
            ev = c.post("/api/ingest/events", json={"courtKey": key, "idempotencyKey": idem, "capturedAt": now, "source": "simulador"})
            ev.raise_for_status()
            print(f"[{key}] evento {ev.json()['eventId']} status={ev.json()['status']}")
            with video.open("rb") as f:
                up = c.post("/api/ingest/videos", data={"idempotencyKey": idem, "courtKey": key, "capturedAt": now},
                            files={"file": (f"{key}.mp4", f, "video/mp4")})
            up.raise_for_status()
            print(f"[{key}] vídeo {up.json()['videoId']} código={up.json()['code']} duplicado={up.json()['duplicate']}")
        c.post("/api/ingest/heartbeat", json={"version": "simulador-0.1", "pendingSync": 0}).raise_for_status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
