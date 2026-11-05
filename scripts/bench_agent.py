#!/usr/bin/env python3
"""Mede CPU, memória, disco e tempo de geração do agente com câmeras SIMULADAS (arquivo de vídeo em laço, fluxo ~ câmera IP).

NÃO usa câmeras, portas seriais nem servidores reais; escreve só em diretório temporário. Os números dependem do hardware onde
roda e de FFmpeg em `-c copy` (sem decodificar): servem para dimensionar e para comparar com a medição no replayagora-srv01.
Uso: python scripts/bench_agent.py --courts 2 --size 1280x720 --bitrate-k 2500 --seconds 60 [--mode copy|encode] [--json out.json]
"""
import argparse
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services" / "local-agent")]

from replay_agent.assemble import build_stack            # noqa: E402
from replay_agent.capture import procstat                 # noqa: E402
from replay_agent.config import parse                     # noqa: E402
from replay_agent.metrics import ProcessMeter, SystemSampler, mem_used_pct  # noqa: E402
from replay_agent.samples import make_gop_video           # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--courts", type=int, default=2)
    ap.add_argument("--size", default="1280x720")
    ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--bitrate-k", type=int, default=2500)
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--trigger-every", type=float, default=15.0)
    ap.add_argument("--mode", choices=("copy", "encode"), default="copy")
    ap.add_argument("--json")
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="bench-agent-"))
    vid = make_gop_video(tmp / "cam.mp4", 20, size=a.size, fps=a.fps, gop=a.fps, bitrate_k=a.bitrate_k)
    clip = {"pre_s": 15, "post_s": 5, "min_pre_s": 5, "min_post_s": 2, "mode": a.mode, "gap_tolerance_s": 3.5}
    courts = [{"key": f"REPLAY_{i:02d}", "label": f"Quadra {i:02d}", "camera": {"url": f"file:{vid}", "segment_s": 2, "buffer_s": 60},
               "trigger": {"buttons": [f"REPLAY_{i:02d}"], "cooldown_s": 1}, "clip": clip} for i in range(1, a.courts + 1)]
    st = build_stack(parse({"arena_tag": "bench", "data_dir": str(tmp / "data"), "courts": courts}))
    st.start()
    sampler = SystemSampler(str(tmp))
    me = ProcessMeter()
    t_end_warm = time.time() + 25
    while time.time() < t_end_warm and min(r.index.stats(3.5)["availableSeconds"] for r in st.recorders.values()) < 22:
        time.sleep(0.5)
    me.start()
    sampler.sample()
    samples, lat = [], []
    t0 = time.time()
    next_trig = t0 + 2
    prev_cpu = {k: (time.monotonic(), (procstat(r.proc.pid) or {}).get("cpuS", 0)) for k, r in st.recorders.items() if r.proc}
    sent: dict[str, float] = {}
    while time.time() - t0 < a.seconds:
        if time.time() >= next_trig:
            for k in st.recorders:
                r = st.router.manual(k, "bench")
                if r.get("jobId"):
                    sent[r["jobId"]] = time.time()
            next_trig += a.trigger_every
        time.sleep(2)
        row = {"t": round(time.time() - t0, 1), "memUsedPct": mem_used_pct(), "sys": sampler.sample()}
        for k, r in st.recorders.items():
            ps = procstat(r.proc.pid) if r.proc and r.proc.poll() is None else None
            if ps and k in prev_cpu:
                dt = time.monotonic() - prev_cpu[k][0]
                row[f"cpu_{k}"] = round(100 * (ps["cpuS"] - prev_cpu[k][1]) / dt, 1) if dt > 0 else None
                row[f"rss_{k}"] = round(ps["rssMb"], 1)
                prev_cpu[k] = (time.monotonic(), ps["cpuS"])
        samples.append(row)
    st.engine.wait_idle(120)
    for jid, ts in sent.items():
        j = st.store.job(jid)
        if j and j["state"] == "done" and j["finished_at"]:
            from datetime import datetime
            lat.append(datetime.fromisoformat(j["finished_at"]).timestamp() - ts)   # UTC naive vs epoch (aproximado)
    usage = me.stop()
    diag = st.recorders["REPLAY_01"].diagnostics()
    stats = st.engine.court_stats()
    seg_bytes = sum(s["bytes"] for r in st.recorders.values() for s in [x.__dict__ for x in r.index.scan(True)])
    cpu_rec = [v for row in samples for k, v in row.items() if k.startswith("cpu_") and v is not None]
    rss_rec = [v for row in samples for k, v in row.items() if k.startswith("rss_")]
    sysc = [row["sys"]["cpuPct"] for row in samples if row["sys"].get("cpuPct") is not None]
    rep = {
        "host": {"cpus": os.cpu_count(), "memTotalMb": round(int([l for l in open("/proc/meminfo") if l.startswith("MemTotal")][0].split()[1]) / 1024)},
        "scenario": {"courts": a.courts, "size": a.size, "fps": a.fps, "bitrateKbps": a.bitrate_k, "seconds": a.seconds, "clipMode": a.mode,
                     "triggerEveryS": a.trigger_every, "source": "arquivo de vídeo em laço (não é RTSP)"},
        "capture": {"measuredFpsCourt1": diag["fps"], "declaredFps": diag["declaredFps"], "lossPct": diag["lossPct"], "bitrateKbpsMeasured": diag["bitrateKbps"],
                    "recorderCpuPctAvgPerCamera": round(sum(cpu_rec) / len(cpu_rec), 2) if cpu_rec else None, "recorderCpuPctMax": max(cpu_rec) if cpu_rec else None,
                    "recorderRssMbMax": max(rss_rec) if rss_rec else None, "reconnects": diag["reconnects"]},
        "system": {"cpuPctAvg": round(sum(sysc) / len(sysc), 1) if sysc else None, "cpuPctMax": max(sysc) if sysc else None,
                   "memUsedPctMax": max((r["memUsedPct"] or 0) for r in samples) if samples else None},
        "agentProcess": usage,
        "replays": {"done": sum(s["completed"] for s in stats), "failed": sum(s["failed"] for s in stats), "unrecoverable": sum(s["unrecoverable"] for s in stats),
                    "avgGenerationS": [s["avgRenderS"] for s in stats], "p95GenerationS": [s["p95RenderS"] for s in stats],
                    "triggerToReadyS": {"avg": round(sum(lat) / len(lat), 1) if lat else None, "max": round(max(lat), 1) if lat else None}},
        "storage": {"bufferBytesOnDisk": seg_bytes, "bufferBytesPerSecondPerCamera": round(seg_bytes / max(1, a.courts) / max(1, st.recorders["REPLAY_01"].cam.buffer_s)),
                    "replaysBytes": st.store.usage(), "avgReplayBytes": round(st.store.usage() / max(1, st.store.count()))},
    }
    st.stop()
    out = json.dumps(rep, indent=2, ensure_ascii=False)
    print(out)
    if a.json:
        Path(a.json).write_text(out)
    return 0 if rep["replays"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
