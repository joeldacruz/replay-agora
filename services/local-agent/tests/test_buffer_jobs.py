"""Acionamentos persistidos ligados a segmentos do buffer: recuperação após reinício, irrecuperáveis e integridade antes de 'done'.
Câmeras = arquivos de vídeo reais em laço (sem câmera física); sem portas físicas."""
import json
import os
import signal
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest

from replay_agent.adapters import utc_epoch
from replay_agent.assemble import build_stack
from replay_agent.config import parse
from replay_agent.render import Rendered, SimRenderer
from replay_agent.samples import make_gop_video

HERE = Path(__file__).parent
LA = HERE.parent


def readline_timeout(proc, seconds):
    """Lê uma linha do subprocesso auxiliar com limite de tempo (nunca bloqueia para sempre); mata o auxiliar se estourar."""
    import select
    r, _, _ = select.select([proc.stdout], [], [], seconds)
    if not r:
        proc.kill()
        proc.wait()
        raise AssertionError(f"subprocesso auxiliar não respondeu em {seconds}s")
    return proc.stdout.readline()


def wait(cond, timeout=40, step=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return False


@pytest.fixture(scope="module")
def vids(tmp_path_factory):
    d = tmp_path_factory.mktemp("v")
    return make_gop_video(d / "q1.mp4", 6, gop=25), make_gop_video(d / "q2.mp4", 6, gop=25)


def cfg_dict(root, v1, v2, **cam2):
    clip = {"pre_s": 3, "post_s": 2, "min_pre_s": 2, "min_post_s": 0.5, "gap_tolerance_s": 3.5}
    cam = {"segment_s": 1, "buffer_s": 30, "stall_s": 5, "reconnect_min_s": 0.2, "reconnect_max_s": 1}
    return {"arena_tag": "arena01", "data_dir": str(root), "courts": [
        {"key": "REPLAY_01", "camera": {"url": f"file:{v1}", **cam}, "trigger": {"buttons": ["REPLAY_01"], "cooldown_s": 0.5}, "clip": clip},
        {"key": "REPLAY_02", "camera": {"url": f"file:{v2}", **cam, **cam2}, "trigger": {"buttons": ["REPLAY_02"], "cooldown_s": 0.5}, "clip": clip}]}


def stack(root, v1, v2, **cam2):
    (root).mkdir(parents=True, exist_ok=True)
    return build_stack(parse(cfg_dict(root, v1, v2, **cam2)))


def buffered(st, secs=6):
    return all(r.index.stats(3.5)["availableSeconds"] >= secs for r in st.recorders.values())


def test_two_courts_triggered_together_produce_two_correct_replays(tmp_path, vids):
    st = stack(tmp_path, *vids)
    st.start()
    try:
        assert wait(lambda: buffered(st))
        a, b = st.router.manual("REPLAY_01", "t"), st.router.manual("REPLAY_02", "t")
        assert a["outcome"] == b["outcome"] == "accepted"
        # o job espera o pós-roll (sem gastar tentativa) e só então gera
        assert wait(lambda: st.store.job_counts() == {"done": 2}, 45)
        rows = st.store._x("SELECT * FROM replays ORDER BY court_key").fetchall()
        assert [r["court_key"] for r in rows] == ["REPLAY_01", "REPLAY_02"]
        for r in rows:
            assert Path(r["path"]).is_file() and r["duration"] >= 3.0 and r["has_audio"] == 1 and r["bytes"] == Path(r["path"]).stat().st_size
        ev = [e["event"] for e in st.store.job_events(a["jobId"])]
        assert ev == ["queued", "started", "done"] and st.store.job(a["jobId"])["attempts"] == 1        # o job só é iniciado com o pós-roll gravado
        assert st.store.job(a["jobId"])["window_start"] < st.store.job(a["jobId"])["window_end"]
    finally:
        st.stop()


def test_camera_down_yields_unrecoverable_event_without_fake_replay_and_other_court_is_unaffected(tmp_path, vids):
    broken = tmp_path / "gone.mp4"                                           # câmera 2 indisponível desde o início
    st = stack(tmp_path / "d", vids[0], broken)
    st.start()
    try:
        assert wait(lambda: st.recorders["REPLAY_01"].index.stats(3.5)["availableSeconds"] >= 6 and st.recorders["REPLAY_02"].state == "down")
        a, b = st.router.manual("REPLAY_01", "t"), st.router.manual("REPLAY_02", "t")
        assert wait(lambda: st.store.job(a["jobId"])["state"] == "done" and st.store.job(b["jobId"])["state"] == "unrecoverable", 45)
        j = st.store.job(b["jobId"])
        assert j["last_error"].startswith("no_coverage_at_t0") and st.store.count() == 1             # nenhum replay inventado
        assert st.store._x("SELECT court_key FROM replays").fetchall()[0]["court_key"] == "REPLAY_01"
        stats = {x["courtKey"]: x for x in st.engine.court_stats()}
        assert stats["REPLAY_02"]["unrecoverable"] == 1 and stats["REPLAY_01"]["completed"] == 1
        assert not list((tmp_path / "d" / "replays" / "REPLAY_02").glob("*.mp4"))
    finally:
        st.stop()


def test_sigkill_during_postroll_recovers_the_pending_trigger_and_leaves_no_orphan_ffmpeg(tmp_path, vids):
    root = tmp_path / "k"
    root.mkdir()
    (root / "agent.json").write_text(json.dumps(cfg_dict(root, *vids)))
    p = subprocess.Popen([sys.executable, str(HERE / "agent_helper.py"), str(root), str(vids[0]), str(vids[1])],
                         env={**os.environ, "PYTHONPATH": str(LA)}, stdout=subprocess.PIPE, text=True)
    assert readline_timeout(p, 90).startswith("TRIGGERED accepted accepted")
    time.sleep(0.4)                                                             # ainda aguardando o pós-roll
    p.send_signal(signal.SIGKILL)
    p.wait()
    time.sleep(0.5)
    out = subprocess.run(["pgrep", "-f", str(root / "buffer")], capture_output=True, text=True).stdout.split()
    assert out == []                                                            # PDEATHSIG: FFmpeg de captura morreu junto
    from replay_agent.store import LocalStore
    pre = LocalStore(str(root / "local.db"))
    assert pre.job_counts().get("done", 0) == 0 and pre.active_jobs() == 2     # nada concluído; dois acionamentos persistidos
    pre.close()
    # reinício do agente
    st = build_stack(parse(json.loads((root / "agent.json").read_text())))
    st.start()
    try:
        assert wait(lambda: st.store.active_jobs() == 0, 60)
        c = st.store.job_counts()
        assert sum(c.values()) == 2 and set(c) <= {"done", "unrecoverable"}      # sempre desfecho explícito
        rows = st.store._x("SELECT * FROM replays").fetchall()
        assert len(rows) == c.get("done", 0) and len({r["local_id"] for r in rows}) == len(rows)           # sem duplicatas
        for r in rows:
            assert Path(r["path"]).stat().st_size == r["bytes"] > 0
        assert c.get("done", 0) >= 1                                            # reinício rápido: recuperável dentro da tolerância
        assert all(r["source"].startswith("manual:") for r in st.store._x("SELECT source FROM jobs").fetchall())        # origem preservada
    finally:
        st.stop()


def test_long_downtime_makes_the_event_unrecoverable_instead_of_a_wrong_replay(tmp_path, vids):
    st = stack(tmp_path, *vids)
    st.start()
    try:
        assert wait(lambda: buffered(st, 4))
        t0 = time.time()
        st.engine.stop()                                                        # ainda não gerou
    finally:
        st.stop()
    for p in (tmp_path / "buffer").rglob("seg_*.ts"):                           # buffer perdido (disco trocado/limpo durante a queda)
        p.unlink()
    from datetime import datetime, timezone
    store = st.store
    # acionamento persistido de antes da queda, cujo buffer não existe mais
    store.enqueue_job("arena01-REPLAY_01-1", "REPLAY_01", datetime.fromtimestamp(t0, timezone.utc).replace(tzinfo=None), 3, ready_at=t0 + 1, window=(t0 - 3, t0 + 2))
    st2 = build_stack(parse(cfg_dict(tmp_path, *vids)), store=store)
    st2.start()
    try:
        assert wait(lambda: store.job("arena01-REPLAY_01-1")["state"] in ("unrecoverable", "done"), 40)
        j = store.job("arena01-REPLAY_01-1")
        assert j["state"] == "unrecoverable" and store.count() == 0
    finally:
        st2.stop()


def test_pending_job_windows_protect_buffer_segments_from_rotation(tmp_path, vids):
    d = cfg_dict(tmp_path, *vids)
    for c in d["courts"]:
        c["camera"]["buffer_s"] = 7                                             # buffer curto: tudo mais velho que ~7 s sairia
    st = build_stack(parse(d))
    st.start()
    try:
        assert wait(lambda: buffered(st, 4))
        t0 = time.time()
        st.store.enqueue_job("arena01-REPLAY_01-pin", "REPLAY_01", __import__("datetime").datetime.utcfromtimestamp(t0), 3,
                             ready_at=t0 + 3600, window=(t0 - 3, t0 + 2))        # job "pendente" por muito tempo
        time.sleep(12)
        starts = [s.start for s in st.indexes["REPLAY_01"].scan(open_last=True)]
        assert any(t0 - 3 <= s <= t0 + 2 for s in starts)                       # segmentos da janela fixada sobreviveram à rotação
        other = [s.start for s in st.indexes["REPLAY_02"].scan(open_last=True)]
        assert min(other) > t0 - 10                                             # sem pino, a outra quadra rotacionou
    finally:
        st.store._x("DELETE FROM jobs WHERE job_id='arena01-REPLAY_01-pin'")
        st.stop()


def test_job_is_never_done_before_a_complete_file_exists(tmp_path):
    from replay_agent.engine import Engine
    from replay_agent.store import LocalStore

    class Flaky(SimRenderer):
        n = 0

        def render(self, court, dest, pre, post, at=None):
            Flaky.n += 1
            if Flaky.n == 1:
                dest.write_bytes(b"\0" * 10)                                    # escreve parcial e "cai"
                raise OSError(5, "I/O error")
            if Flaky.n == 2:
                dest.write_bytes(b"\0" * 50)
                return Rendered(2.0, 1, 1, True, "h264", 9999)                  # tamanho informado não confere com o arquivo
            return super().render(court, dest, pre, post, at)
    store = LocalStore(str(tmp_path / "l.db"))
    e = Engine("c", tmp_path / "r", ["REPLAY_01"], store, Flaky(size_bytes=500), dedup_s=0, retry_backoff_s=0.0, max_attempts=4)
    r = e.trigger("REPLAY_01", __import__("datetime").datetime(2026, 11, 5, 12))
    assert e.wait_idle(15)
    j = store.job(r["localId"])
    assert j["state"] == "done" and j["attempts"] == 3 and store.count() == 1
    row = store.get(r["localId"])
    assert Path(row["path"]).stat().st_size == row["bytes"] == 500 + 12             # só o terceiro (íntegro) virou replay
    assert [x["event"] for x in store.job_events(r["localId"])].count("retry_scheduled") == 2
    e.stop()
