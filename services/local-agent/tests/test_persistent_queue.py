"""Fila SQLite persistente: recuperação após reinício inesperado (SIGKILL), sem duplicar replays, com histórico de tentativas."""
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from replay_agent.engine import Engine
from replay_agent.render import SimRenderer
from replay_agent.store import LocalStore

HERE = Path(__file__).parent
T0 = datetime(2026, 11, 5, 12, 0, 0)
LA = HERE.parent  # services/local-agent


def spawn(root, delay):
    env = {**os.environ, "PYTHONPATH": str(LA)}
    p = subprocess.Popen([sys.executable, str(HERE / "crash_helper.py"), str(root), str(delay)], env=env,
                         stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "READY"
    return p


def test_sigkill_during_queue_recovers_all_triggers_without_duplicates(tmp_path):
    p = spawn(tmp_path, delay=0.5)           # 6 acionamentos, 2 quadras, 0,5 s cada => dá tempo de matar no meio
    time.sleep(0.8)
    p.send_signal(signal.SIGKILL)            # queda de energia / kill -9: sem finalização limpa
    p.wait()
    store = LocalStore(str(tmp_path / "local.db"))
    before = store.job_counts()
    assert before.get("processing", 0) >= 1 and before.get("queued", 0) + before.get("processing", 0) + before.get("done", 0) == 6
    (tmp_path / "r" / "REPLAY_01" / ".tmp").mkdir(parents=True, exist_ok=True)
    (tmp_path / "r" / "REPLAY_01" / ".tmp" / "lixo.mp4").write_bytes(b"parcial")  # parcial de execução interrompida
    done_before = {r["local_id"] for r in store._x("SELECT local_id FROM replays").fetchall()}

    e = Engine("crash", tmp_path / "r", ["REPLAY_01", "REPLAY_02"], store, SimRenderer(size_bytes=1000), dedup_s=0)
    assert e.recovered >= 1 and not (tmp_path / "r" / "REPLAY_01" / ".tmp" / "lixo.mp4").exists()
    assert e.wait_idle(20)
    c = store.job_counts()
    assert c == {"done": 6}                                   # nenhum acionamento perdido
    ids = [r["local_id"] for r in store._x("SELECT local_id FROM replays").fetchall()]
    assert len(ids) == 6 == len(set(ids)) and done_before <= set(ids)
    assert len(list((tmp_path / "r").rglob("*.mp4"))) == 6    # um arquivo por replay
    ev = [x["event"] for r in store._x("SELECT job_id FROM jobs").fetchall() for x in store.job_events(r["job_id"])]
    assert "recovered" in ev
    e.stop()


def test_retriggering_same_instant_after_restart_is_not_duplicated(tmp_path):
    store = LocalStore(str(tmp_path / "l.db"))
    e = Engine("rr", tmp_path / "r", ["REPLAY_01"], store, SimRenderer(size_bytes=10), dedup_s=0)
    assert e.trigger("REPLAY_01", T0)["accepted"]
    e.wait_idle(5)
    e.stop()
    e2 = Engine("rr", tmp_path / "r", ["REPLAY_01"], store, SimRenderer(size_bytes=10), dedup_s=0)
    assert e2.trigger("REPLAY_01", T0)["reason"] == "duplicate"        # mesmo instante: mesma chave
    e3 = e2
    e3.wait_idle(5)
    assert store.count() == 1 and len(list((tmp_path / "r").rglob("*.mp4"))) == 1
    e2.stop()


def test_debounce_survives_restart(tmp_path):
    store = LocalStore(str(tmp_path / "l.db"))
    e = Engine("db", tmp_path / "r", ["REPLAY_01"], store, SimRenderer(size_bytes=10), dedup_s=3)
    assert e.trigger("REPLAY_01", T0)["accepted"]
    e.stop()
    e2 = Engine("db", tmp_path / "r", ["REPLAY_01"], store, SimRenderer(size_bytes=10), dedup_s=3)
    assert e2.trigger("REPLAY_01", T0 + timedelta(seconds=1))["reason"] == "duplicate"  # botão apertado de novo logo após reiniciar
    assert e2.trigger("REPLAY_01", T0 + timedelta(seconds=10))["accepted"]
    e2.stop()


def test_crash_after_render_before_done_does_not_regenerate(tmp_path):
    """Simula queda entre gravar o replay e marcar o job: ao reiniciar, não renderiza de novo."""
    class Counting(SimRenderer):
        n = 0

        def render(self, *a, **k):
            Counting.n += 1
            return super().render(*a, **k)
    store = LocalStore(str(tmp_path / "l.db"))
    e = Engine("cr", tmp_path / "r", ["REPLAY_01"], store, Counting(size_bytes=10), dedup_s=0)
    r = e.trigger("REPLAY_01", T0)
    e.wait_idle(5)
    e.stop()
    assert Counting.n == 1
    store._x("UPDATE jobs SET state='processing' WHERE job_id=?", (r["localId"],))  # como se tivesse caído antes do 'done'
    e2 = Engine("cr", tmp_path / "r", ["REPLAY_01"], store, Counting(size_bytes=10), dedup_s=0)
    e2.wait_idle(5)
    assert Counting.n == 1 and store.count() == 1 and store.job(r["localId"])["state"] == "done"
    e2.stop()


def test_attempts_errors_and_final_state_are_recorded(tmp_path):
    class Always(SimRenderer):
        def render(self, *a, **k):
            raise OSError(28, "No space left on device")
    store = LocalStore(str(tmp_path / "l.db"))
    e = Engine("at", tmp_path / "r", ["REPLAY_01"], store, Always(), dedup_s=0, max_attempts=3, retry_backoff_s=0.0)
    r = e.trigger("REPLAY_01", T0)
    assert e.wait_idle(10)
    j = store.job(r["localId"])
    assert (j["state"], j["attempts"], j["last_error"]) == ("failed", 3, "io:28")
    names = [x["event"] for x in store.job_events(r["localId"])]
    assert names == ["queued", "started", "retry_scheduled", "started", "retry_scheduled", "started", "failed"]
    assert e.workers["REPLAY_01"].state == "storage_failure" and not store.count()
    e.stop()


def test_camera_down_is_terminal_no_pointless_retries(tmp_path):
    rend = SimRenderer(size_bytes=10)
    rend.camera_up["REPLAY_01"] = False
    store = LocalStore(str(tmp_path / "l.db"))
    e = Engine("cd", tmp_path / "r", ["REPLAY_01"], store, rend, dedup_s=0)
    r = e.trigger("REPLAY_01", T0)
    e.wait_idle(5)
    assert store.job(r["localId"])["attempts"] == 1 and store.job(r["localId"])["state"] == "failed"
    e.stop()


def test_queues_stay_independent_per_court_in_the_persistent_queue(tmp_path):
    rend = SimRenderer(size_bytes=10)
    rend.delay_by_court["REPLAY_01"] = 0.6
    store = LocalStore(str(tmp_path / "l.db"))
    e = Engine("ind", tmp_path / "r", ["REPLAY_01", "REPLAY_02"], store, rend, dedup_s=0)
    e.trigger("REPLAY_01", T0)
    for i in range(4):
        e.trigger("REPLAY_02", T0 + timedelta(seconds=10 * (i + 1)))
    t = time.perf_counter()
    while store.job_counts("REPLAY_02").get("done", 0) < 4 and time.perf_counter() - t < 5:
        time.sleep(0.01)
    assert store.job_counts("REPLAY_02") == {"done": 4} and store.job_counts("REPLAY_01").get("done", 0) == 0
    e.wait_idle(10)
    e.stop()
