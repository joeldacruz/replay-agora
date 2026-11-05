"""Governança de recursos: limite de FFmpeg, prioridade da captura, aceleração por hardware (montagem do comando) e recuo para CPU."""
import os
import stat
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from replay_agent import ffplan
from replay_agent.resources import HwProfile, ResourceGovernor, choose_hw, detect_hw, looks_like_hw_failure
from replay_agent.link import CentralLink


def gov(**kw):
    kw.setdefault("detected", {"vaapi": False, "qsv": False, "deviceExists": False})
    return ResourceGovernor(**kw)


def run_slots(g, n, hold=0.15):
    peak, cur, lock = [0], [0], threading.Lock()

    def job():
        with g.slot():
            with lock:
                cur[0] += 1
                peak[0] = max(peak[0], cur[0])
            time.sleep(hold)
            with lock:
                cur[0] -= 1
    ts = [threading.Thread(target=job) for _ in range(n)]
    t0 = time.monotonic()
    [t.start() for t in ts]
    [t.join() for t in ts]
    return peak[0], time.monotonic() - t0


def test_ffmpeg_concurrency_never_exceeds_the_configured_limit():
    for limit in (1, 2):
        g = gov(max_ffmpeg=limit)
        peak, wall = run_slots(g, 6)
        assert peak == limit and g.stats()["runs"] == 6
        assert wall >= 6 * 0.15 / limit * 0.9                                   # realmente serializou
    g = gov(max_ffmpeg=1)
    g.apply(max_ffmpeg=2)                                                       # ajuste dinâmico (config por arena) sem reiniciar
    assert run_slots(g, 4)[0] == 2
    g.apply(max_ffmpeg=999)
    assert g.max_ffmpeg == (os.cpu_count() or 1)                                # nunca acima do hardware local


def test_render_pauses_while_capture_lags_and_resumes_when_it_recovers():
    rec = SimpleNamespace(state="streaming", speed=0.5)
    g = gov(max_ffmpeg=2, recorders={"REPLAY_01": rec})
    assert g.capture_lagging()
    started = []

    def job():
        with g.slot():
            started.append(time.monotonic())
    t0 = time.monotonic()
    t = threading.Thread(target=job)
    t.start()
    time.sleep(0.6)
    assert not started and g.stats()["pausedNow"]                               # captura atrasada => render NÃO começa
    rec.speed = 1.0
    t.join(3)
    assert started and started[0] - t0 >= 0.55 and g.stats()["pausedForCapture"] == 1


def test_pause_is_bounded_so_renders_are_not_starved_forever():
    rec = SimpleNamespace(state="streaming", speed=0.1)
    g = gov(max_ffmpeg=1, recorders={"c": rec}, max_pause_s=0.5)
    with g.slot():
        pass
    assert g.stats()["forcedRuns"] == 1                                         # depois do limite segue, registrando o fato
    rec.state = "down"
    assert not g.capture_lagging()                                              # câmera caída não segura o render
    g2 = gov(max_ffmpeg=1, recorders={"c": SimpleNamespace(state="streaming", speed=0.1)}, pause_when_lagging=False)
    t0 = time.monotonic()
    with g2.slot():
        pass
    assert time.monotonic() - t0 < 0.3                                          # política desligada: não espera


def test_hw_selection_detection_and_cpu_alternative():
    det = {"vaapi": True, "qsv": False, "deviceExists": True}
    assert choose_hw("none", det, "/dev/dri/renderD128") is None
    assert choose_hw("vaapi", det, "/dev/dri/renderD128").name == "vaapi"
    assert choose_hw("auto", det, "/dev/dri/renderD128").name == "vaapi"
    assert choose_hw("qsv", det, "/dev/dri/renderD128") is None                 # pedido mas indisponível => CPU
    assert choose_hw("auto", {"vaapi": False, "qsv": True}, "/dev/dri/renderD128").name == "qsv"
    assert choose_hw("auto", {}, "/dev/x") is None
    d = detect_hw("ffmpeg", "/dev/dri/renderD128")
    assert set(d) >= {"vaapi", "qsv", "deviceExists", "renderNodes"} and d["vaapi"] is False        # sem /dev/dri neste ambiente
    assert detect_hw("/nao/existe/ffmpeg")["vaapi"] is False


def meta(codec="h264"):
    return ffplan.SourceMeta(10.0, 1280, 720, codec, True, "aac")


def test_vaapi_and_qsv_command_construction_and_cpu_default_unchanged():
    ov = [ffplan.Overlay("/l.png", 400, 160, "top-left", 20, 1.0, None)]
    plan = ffplan.ExportPlan("/s.mp4", "/o.mp4", meta(), "mute", None, 1.0, 0.6, ov)
    cpu = ffplan.build_command(plan)
    assert "libx264" in cpu and "-vaapi_device" not in cpu and cpu == ffplan.build_command(plan, "ffmpeg", None)
    va = ffplan.build_command(plan, "ffmpeg", HwProfile("vaapi", "/dev/dri/renderD128"))
    j = " ".join(va)
    assert va[va.index("-vaapi_device") + 1] == "/dev/dri/renderD128" and "h264_vaapi" in va and "format=nv12,hwupload[vhw]" in j and "[vhw]" in va
    assert "libx264" not in j and va.index("-vaapi_device") < va.index("-i")        # dispositivo declarado ANTES da entrada
    qs = ffplan.build_command(plan, "ffmpeg", HwProfile("qsv"))
    assert "h264_qsv" in qs and "-init_hw_device" in qs and "hwupload=extra_hw_frames=64" in " ".join(qs)
    plain = ffplan.ExportPlan("/s.mp4", "/o.mp4", meta("mpeg4"), "mute", None, 1.0, 0.6, [])
    assert "h264_vaapi" in ffplan.build_command(plain, "ffmpeg", HwProfile("vaapi"))
    h264 = ffplan.ExportPlan("/s.mp4", "/o.mp4", meta(), "mute", None, 1.0, 0.6, [])
    assert "copy" in ffplan.build_command(h264, "ffmpeg", HwProfile("vaapi"))     # sem overlay e já h264: continua cópia (sem GPU)


def test_failure_detector_recognises_gpu_errors_only():
    assert looks_like_hw_failure("Failed to initialise VAAPI connection: -1 (unknown libva error).")
    assert looks_like_hw_failure("Cannot load libmfx / qsv device creation failed")
    assert not looks_like_hw_failure("Invalid data found when processing input")


def fake_ffmpeg(tmp_path) -> Path:
    """Wrapper: simula uma máquina cuja GPU falha; só codifica via CPU."""
    f = tmp_path / "ffmpeg-fake"
    f.write_text('#!/bin/sh\nfor a in "$@"; do case "$a" in h264_vaapi|h264_qsv) echo "Failed to initialise VAAPI connection: -1 (unknown libva error)" >&2; exit 1;; esac; done\nexec ffmpeg "$@"\n')
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return f


def test_buffer_clip_encode_mode_falls_back_to_cpu_when_gpu_fails(tmp_path):
    from datetime import datetime, timezone
    from replay_agent.capture import SegmentRecorder
    from replay_agent.clipper import BufferClipRenderer
    from replay_agent.config import CameraCfg, ClipCfg
    from replay_agent.samples import make_gop_video
    v = make_gop_video(tmp_path / "v.mp4", 6)
    cam = CameraCfg(url=f"file:{v}", segment_s=1.0, buffer_s=30, reconnect_min_s=0.2)
    rec = SegmentRecorder("REPLAY_01", cam, tmp_path / "b")
    g = gov(max_ffmpeg=1, hwaccel="vaapi", detected={"vaapi": True, "qsv": False, "deviceExists": True}, recorders={})
    clip = ClipCfg(pre_s=3, post_s=1, min_pre_s=2, min_post_s=0.5, mode="encode")
    r = BufferClipRenderer({"REPLAY_01": rec.index}, {"REPLAY_01": rec}, {"REPLAY_01": clip}, {"REPLAY_01": cam}, ffmpeg=str(fake_ffmpeg(tmp_path)), governor=g)
    rec.start()
    try:
        end = time.monotonic() + 40
        while time.monotonic() < end and rec.index.stats(2.5)["availableSeconds"] < 9:
            time.sleep(0.2)
        t0 = time.time() - 3
        out = r.render("REPLAY_01", tmp_path / "o.mp4", 3, 1, datetime.fromtimestamp(t0, timezone.utc).replace(tzinfo=None))
    finally:
        rec.stop()
    assert out.duration >= 2.0 and g.hw_failed and g.stats()["hw"]["active"] == "cpu" and "libva" in g.stats()["hw"]["reason"]


def test_link_applies_arena_resources_from_central_config(tmp_path):
    g = gov(max_ffmpeg=1)
    eng = SimpleNamespace(apply_config=lambda cfg: None)
    link = CentralLink.__new__(CentralLink)
    link.engine, link.governor, link.config_version = eng, g, ""
    link._apply({"configVersion": "v2", "resources": {"maxFfmpeg": 2, "hwaccel": "auto", "renderNice": 4}})
    s = g.stats()
    assert s["maxFfmpeg"] == 2 and s["renderNice"] == 4 and s["hw"]["setting"] == "auto" and link.config_version == "v2"
