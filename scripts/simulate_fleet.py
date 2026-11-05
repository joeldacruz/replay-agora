#!/usr/bin/env python3
"""Simula N arenas × M quadras (câmeras e botoeiras FICTÍCIAS) contra uma API central EM PROCESSO com banco/armazenamento
temporários. Mede tempo e recursos, injeta queda da central e verifica: zero duplicações, nenhum arquivo de vídeo na central.

NÃO toca em servidores, câmeras, Arduino, DNS ou bancos reais.
Uso:  python scripts/simulate_fleet.py --arenas 4 --courts 2 --rounds 5 --renderer ffmpeg --outage
"""
import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services" / "api"), str(ROOT / "services" / "local-agent")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arenas", type=int, default=3)
    ap.add_argument("--courts", type=int, default=2)
    ap.add_argument("--rounds", type=int, default=5, help="acionamentos simultâneos por quadra")
    ap.add_argument("--renderer", choices=("stub", "ffmpeg"), default="ffmpeg")
    ap.add_argument("--clip-seconds", type=float, default=3.0)
    ap.add_argument("--outage", action="store_true", help="derruba a central na metade e reconecta no fim")
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="fleet-sim-"))
    os.environ.update({"DATABASE_URL": f"sqlite:///{tmp}/central.db", "STORAGE_DIR": str(tmp / "central-storage"),
                       "SECRET_KEY": "sim-only-secret-not-for-production-0123456789", "APP_ENV": "development"})
    from fastapi.testclient import TestClient
    from app.db import Base, SessionLocal, engine
    from app.main import app
    from app.models import Video
    from app.simulation import provision_arenas
    from replay_agent.metrics import ProcessMeter
    from replay_agent.render import SimRenderer
    from replay_agent.simulator import Fleet, build_arena

    Base.metadata.create_all(engine)
    db = SessionLocal()
    prov = provision_arenas(db, a.arenas, a.courts)
    db.close()
    http = TestClient(app)
    arenas = [build_arena(p["tag"], p["token"], p["courtKeys"], http, root=tmp / p["tag"],
                          renderer=SimRenderer(mode=a.renderer, clip_seconds=a.clip_seconds)) for p in prov]
    fleet = Fleet(arenas)
    fleet.tick_all()  # registro + config
    base = datetime(2026, 11, 5, 12, 0, 0)
    meter = ProcessMeter()
    meter.start()
    t0 = time.perf_counter()
    cycle_walls = []
    for r in range(a.rounds):
        if a.outage and r == a.rounds // 2:
            for x in arenas:
                x.http.down = True
        c0 = time.perf_counter()
        for x in arenas:
            for k in x.court_keys:
                x.engine.trigger(k, base + timedelta(seconds=10 * r))
        fleet.wait_idle(600)
        cycle_walls.append(time.perf_counter() - c0)
        fleet.tick_all()
    outage_pending = sum(x.store.count("pending") for x in arenas)
    for x in arenas:
        x.http.down = False
    t_sync = time.perf_counter()
    fleet.tick_all()
    sync_s = time.perf_counter() - t_sync
    usage = meter.stop()
    total_s = time.perf_counter() - t0

    db = SessionLocal()
    central = db.query(Video).filter(Video.local_id.isnot(None)).count()
    dup = db.execute(__import__("sqlalchemy").text("SELECT COUNT(*) FROM (SELECT arena_id, local_id FROM videos WHERE local_id IS NOT NULL GROUP BY 1,2 HAVING COUNT(*)>1)")).scalar()
    db.close()
    central_files = [p for p in (tmp / "central-storage").rglob("*") if p.is_file()] if (tmp / "central-storage").exists() else []
    local = sum(x.store.count() for x in arenas)
    stats = [w.stats() for x in arenas for w in x.engine.workers.values()]
    rendered = [s["avgRenderS"] for s in stats if s["avgRenderS"]]
    rep = {
        "arenas": a.arenas, "courtsPerArena": a.courts, "rounds": a.rounds, "renderer": a.renderer, "outage": a.outage,
        "replaysLocal": local, "replaysCentral": central, "duplicatesCentral": dup, "pendingDuringOutage": outage_pending,
        "pendingAfter": sum(x.store.count("pending") for x in arenas), "videoFilesInCentral": len(central_files),
        "failed": sum(s["failed"] for s in stats), "avgRenderS": round(sum(rendered) / len(rendered), 3) if rendered else None,
        "p95RenderS": max((s["p95RenderS"] or 0) for s in stats), "avgCycleWallS": round(sum(cycle_walls) / len(cycle_walls), 2),
        "resyncAfterReconnectS": round(sync_s, 3), "totalWallS": round(total_s, 2), "process": usage, "cpus": os.cpu_count(),
    }
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    fleet.stop()
    ok = local == central and dup == 0 and not central_files and rep["pendingAfter"] == 0
    print("RESULTADO:", "OK" if ok else "FALHA")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
