"""Gravador de segmentos com arquivo de vídeo real em laço (sem câmera): buffer circular, reconexão, diagnóstico e corte do replay."""
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from replay_agent.adapters import NotReady, Unrecoverable
from replay_agent.capture import BufferIndex, SegmentRecorder, build_capture_cmd, rotate
from replay_agent.clipper import BufferClipRenderer
from replay_agent.config import CameraCfg, ClipCfg
from replay_agent.samples import make_gop_video


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    return make_gop_video(tmp_path_factory.mktemp("vid") / "quadra.mp4", seconds=6, gop=25)


def cam(video, **kw):
    return CameraCfg(url=f"file:{video}", segment_s=1.0, buffer_s=40, stall_s=4, reconnect_min_s=0.2, reconnect_max_s=1.0, **kw)


def wait(cond, timeout=20, step=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return False


def utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


def test_capture_command_is_copy_only_and_never_logs_credentials():
    c = CameraCfg(url="rtsp://admin:S3nh4@10.0.0.11:554/stream1", segment_s=2, buffer_s=60)
    cmd = build_capture_cmd(c, Path("/buf"))
    j = " ".join(cmd)
    assert "-rtsp_transport tcp" in j and "-c copy" in j and "-f segment" in j and "-segment_time 2" in j and "seg_%s.ts" in j
    assert "-c:v libx264" not in j                                          # captura NUNCA recodifica (CPU baixa)
    udp = build_capture_cmd(CameraCfg(url="rtsp://h/x", transport="udp"), Path("/b"))
    assert "udp" in udp
    with pytest.raises(ValueError):
        build_capture_cmd(CameraCfg(url="http://x"), Path("/b"))


def test_recorder_fills_circular_buffer_reports_fps_and_rotates(tmp_path, video):
    rec = SegmentRecorder("REPLAY_01", cam(video), tmp_path / "buf")
    rec.cam.buffer_s = 6
    rec.start()
    try:
        assert wait(lambda: rec.state == "streaming" and rec.index.stats(2.5)["segments"] >= 4, 25)
        d = rec.diagnostics()
        assert d["state"] == "streaming" and 15 <= d["fps"] <= 35 and d["lastFrameAgeS"] < 5 and d["pid"]
        assert d["buffer"]["availableSeconds"] >= 3 and d["buffer"]["gaps"] == 0 and d["buffer"]["medianSegmentS"] == pytest.approx(1, abs=1.1)
        assert "u:p@" not in str(d)
        # rotação: buffer_s=6 => segmentos antigos saem, a quantidade fica limitada
        assert wait(lambda: rec.index.stats(2.5)["segments"] >= 5 and len(list((tmp_path / "buf").glob("seg_*.ts"))) <= 14, 30)
        assert wait(lambda: rec.index.stats(2.5)["oldestAt"] > time.time() - 14, 30)
    finally:
        rec.stop()
    assert rec.state == "stopped"


def test_rotation_keeps_pinned_windows_and_ignores_foreign_files(tmp_path):
    d = tmp_path / "buf"
    d.mkdir()
    now = time.time()
    for k in range(10):
        p = d / f"seg_{int(now) - 100 + k}.ts"
        p.write_bytes(b"x" * 10)
        os.utime(p, (now - 50, now - 50))
    foreign = d / "notes.txt"
    foreign.write_text("não toque")
    idx = BufferIndex(d, 1.0)
    idx._dur = {str(s.path): 1.0 for s in idx.scan(open_last=True)}
    removed = rotate(idx, keep_s=20, pinned=[(now - 100, now - 99)], now=now, margin=1.5)
    left = {int(p.name[4:-3]) for p in d.glob("seg_*.ts")}
    base = int(now) - 100
    assert removed >= 5 and foreign.exists()                                              # arquivo estranho nunca é apagado
    assert {base, base + 1} <= left                                                      # janela fixada (acionamento pendente) preservada
    assert base + 5 not in left and base + 6 not in left                                 # fora da janela: rotacionado
    assert base + 9 in left                                                              # o mais novo (em gravação) fica


def test_recorder_reconnects_after_process_death_and_other_court_is_unaffected(tmp_path, video):
    r1 = SegmentRecorder("REPLAY_01", cam(video), tmp_path / "b1")
    r2 = SegmentRecorder("REPLAY_02", cam(video), tmp_path / "b2")
    r1.start(); r2.start()
    try:
        assert wait(lambda: r1.state == "streaming" and r2.state == "streaming", 25)
        pid2 = r2.proc.pid
        r1.proc.kill()                                                                    # câmera 1 "cai" (processo morre)
        assert wait(lambda: r1.reconnects >= 1, 10)
        assert wait(lambda: r1.state == "streaming" and r1.runs >= 2, 25)                 # reconectou sozinha
        assert r2.proc.pid == pid2 and r2.reconnects == 0 and r2.runs == 1               # a outra câmera nem percebeu
        assert r1.diagnostics()["reconnects"] >= 1
    finally:
        r1.stop(); r2.stop()


def test_recorder_with_unreachable_source_goes_down_with_backoff_and_redacted_error(tmp_path):
    c = CameraCfg(url="file:/nao/existe.mp4", segment_s=1.0, buffer_s=30, reconnect_min_s=0.05, reconnect_max_s=0.2)
    rec = SegmentRecorder("REPLAY_01", c, tmp_path / "b")
    rec.start()
    try:
        assert wait(lambda: rec.state == "down", 15)
        d = rec.diagnostics()
        assert d["reconnects"] >= 3 and d["lastError"] and d["fps"] == 0 and d["buffer"]["segments"] == 0
    finally:
        rec.stop()


def test_stalled_stream_is_killed_and_restarted(tmp_path, video):
    rec = SegmentRecorder("REPLAY_01", cam(video), tmp_path / "b")
    rec.start()
    try:
        assert wait(lambda: rec.state == "streaming", 20)
        os.kill(rec.proc.pid, 19)                                                         # SIGSTOP: processo "congela" (RTSP pendurado)
        assert wait(lambda: rec.stalls >= 1, 15)
        assert wait(lambda: rec.runs >= 2 and rec.state == "streaming", 25)
    finally:
        rec.stop()


def mk_renderer(rec, clip, **kw):
    return BufferClipRenderer({"REPLAY_01": rec.index}, {"REPLAY_01": rec}, {"REPLAY_01": clip}, {"REPLAY_01": rec.cam}, **kw)


def test_clip_is_cut_from_the_trigger_instant_and_waits_for_post_roll(tmp_path, video):
    rec = SegmentRecorder("REPLAY_01", cam(video), tmp_path / "b")
    rec.start()
    try:
        assert wait(lambda: rec.index.stats(2.5)["availableSeconds"] >= 5, 30)
        clip = ClipCfg(pre_s=3, post_s=2, min_pre_s=2, min_post_s=1)
        r = mk_renderer(rec, clip)
        t0 = time.time()
        with pytest.raises(NotReady) as nr:                                               # pós-roll ainda não existe
            r.render("REPLAY_01", tmp_path / "o.mp4", 3, 2, utc(t0))
        assert nr.value.retry_at > t0 + 2
        assert wait(lambda: time.time() >= nr.value.retry_at, 15)
        out = r.render("REPLAY_01", tmp_path / "o.mp4", 3, 2, utc(t0))
        assert 4.0 <= out.duration <= 9.0 and out.width == 320 and out.has_audio and out.bytes > 1000
        # o corte tem o passado pedido (>= pre) e não é gerado de trecho aleatório
        assert out.duration >= 3 + 2 - 1.5
    finally:
        rec.stop()


def test_unrecoverable_cases_never_produce_a_wrong_replay(tmp_path, video):
    rec = SegmentRecorder("REPLAY_01", cam(video), tmp_path / "b")
    rec.start()
    try:
        assert wait(lambda: rec.index.stats(2.5)["availableSeconds"] >= 10, 40)
    finally:
        rec.stop()
    clip = ClipCfg(pre_s=3, post_s=2, min_pre_s=2, min_post_s=1)
    r = mk_renderer(rec, clip)
    now = time.time()
    past = now - 3600                                                                      # evento de 1 h atrás: buffer não tem mais
    with pytest.raises(Unrecoverable) as e:
        r.render("REPLAY_01", tmp_path / "x.mp4", 3, 2, utc(past))
    assert e.value.reason == "no_coverage_at_t0" and not (tmp_path / "x.mp4").exists()
    # lacuna de 3 s (câmera caiu) no pré-roll: o acionamento logo depois dela NÃO pode virar um replay "emendado"
    segs = rec.index.scan(open_last=True)
    for hole in segs[2:5]:
        hole.path.unlink()
        rec.index._dur.pop(str(hole.path), None)
    t0 = segs[5].start + 1.0
    with pytest.raises(Unrecoverable) as e:
        mk_renderer(rec, ClipCfg(pre_s=3, post_s=2, min_pre_s=3, min_post_s=1)).render("REPLAY_01", tmp_path / "y.mp4", 3, 2, utc(t0))
    assert e.value.reason in ("buffer_gap", "segments_expired") and not (tmp_path / "y.mp4").exists()
    with pytest.raises(Unrecoverable) as e:                                                # acionamento DENTRO da lacuna
        r.render("REPLAY_01", tmp_path / "w.mp4", 3, 2, utc(segs[3].start + 0.5))
    assert e.value.reason == "no_coverage_at_t0"
    # trigger sem horário
    with pytest.raises(Unrecoverable):
        r.render("REPLAY_01", tmp_path / "z.mp4", 3, 2, None)


def test_external_dir_mode_reads_foreign_segments_without_touching_them(tmp_path, video):
    """Adoção do buffer de OUTRO processo (legado): somente leitura; nada é gravado nem rotacionado."""
    src = SegmentRecorder("REPLAY_01", cam(video), tmp_path / "legacy")
    src.start()
    try:
        assert wait(lambda: src.index.stats(2.5)["availableSeconds"] >= 5, 30)
    finally:
        src.stop()
    before = {p.name: p.stat().st_mtime_ns for p in (tmp_path / "legacy").glob("*")}
    idx = BufferIndex(tmp_path / "legacy", 1.0, glob=str(tmp_path / "legacy" / "seg_*.ts"), time_source="name_epoch")
    c = cam(video)
    r = BufferClipRenderer({"REPLAY_01": idx}, {}, {"REPLAY_01": ClipCfg(pre_s=2, post_s=1, min_pre_s=1, min_post_s=0.5)}, {"REPLAY_01": c})
    t0 = idx.scan(True)[2].start + 1.0
    out = r.render("REPLAY_01", tmp_path / "ext.mp4", 2, 1, utc(t0))
    assert out.duration >= 2.0
    assert before == {p.name: p.stat().st_mtime_ns for p in (tmp_path / "legacy").glob("*")}   # legado intacto
