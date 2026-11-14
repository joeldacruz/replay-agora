"""Medição (não é prova de produção): 2 arenas × 2 quadras gerando MP4 real de teste (FFmpeg) ao mesmo tempo."""
import json
import os
from datetime import datetime, timedelta

from replay_agent.metrics import ProcessMeter
from replay_agent.render import SimRenderer
from replay_agent.simulator import Fleet, build_arena

from app.db import SessionLocal
from app.models import Video

T0 = datetime(2026, 11, 5, 12, 0, 0)


def test_parallel_courts_with_real_ffmpeg_are_faster_than_serial_and_lose_nothing(client, world, tmp_path):
    arenas = []
    for tag in ("A", "B"):
        arenas.append(build_arena(tag, getattr(world, tag).token, [f"{tag}_01", f"{tag}_02"], client, root=tmp_path / tag,
                                  renderer=SimRenderer(mode="ffmpeg", clip_seconds=3)))
    fleet = Fleet(arenas)
    N = 3
    meter = ProcessMeter()
    meter.start()
    for r in range(N):
        for a in arenas:
            for k in a.court_keys:
                assert a.engine.trigger(k, T0 + timedelta(seconds=10 * r))["accepted"]
    assert fleet.wait_idle(180)
    usage = meter.stop()
    assert all(r["online"] for r in fleet.tick_all())
    stats = [w.stats() for a in arenas for w in a.engine.workers.values()]
    total = sum(s["completed"] for s in stats)
    assert total == 4 * N and sum(s["failed"] for s in stats) == 0
    assert max(a.renderer.max_concurrent for a in arenas) >= 2          # houve processamento realmente simultâneo
    avg = sum(s["avgRenderS"] for s in stats) / len(stats)
    serial_estimate = avg * total
    assert usage["wallS"] < serial_estimate                              # paralelismo ganhou do pior caso serial
    db = SessionLocal()
    assert db.query(Video).filter(Video.local_id.isnot(None)).count() == total
    db.close()
    report = {"courts": 4, "replays": total, "avgRenderS": round(avg, 2), "wallS": usage["wallS"], "serialEstimateS": round(serial_estimate, 2),
              "cpuCoresAvg": usage["cpuCoresAvg"], "peakRssMb": usage["peakRssMb"], "cpus": os.cpu_count()}
    print("BENCH", json.dumps(report))
    fleet.stop()
