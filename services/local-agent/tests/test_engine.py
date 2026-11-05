"""Motor local simulado: fila por quadra, concorrência, limites de armazenamento e falhas. Sem API, sem câmeras."""
import time
from datetime import datetime, timedelta
from pathlib import Path

from replay_agent.engine import Engine
from replay_agent.render import SimRenderer
from replay_agent.store import LocalStore

T0 = datetime(2026, 11, 5, 12, 0, 0)


def make(tmp_path, courts=("REPLAY_01", "REPLAY_02"), tag="e01", **kw):
    rend = kw.pop("renderer", None) or SimRenderer(size_bytes=100_000)
    store = LocalStore(str(tmp_path / "l.db"))
    return Engine(tag, tmp_path / "r", list(courts), store, rend, **kw), rend, store


def files(tmp_path):
    return sorted(p for p in (tmp_path / "r").rglob("*.mp4"))


def test_courts_do_not_block_each_other(tmp_path):
    e, rend, store = make(tmp_path)
    rend.delay_by_court["REPLAY_01"] = 0.8   # quadra 1 lenta
    rend.delay_by_court["REPLAY_02"] = 0.0
    e.trigger("REPLAY_01", T0)
    for i in range(5):                        # 5 replays rápidos na quadra 2 enquanto a 1 trabalha
        e.trigger("REPLAY_02", T0 + timedelta(seconds=10 * (i + 1)))
    t = time.perf_counter()
    while store.job_counts("REPLAY_02").get("done", 0) < 5 and time.perf_counter() - t < 5:
        time.sleep(0.01)
    assert e.workers["REPLAY_02"].completed == 5
    assert e.workers["REPLAY_01"].completed == 0 and e.workers["REPLAY_01"].processing  # ainda renderizando: não atrasou a 2
    assert e.wait_idle(10) and e.workers["REPLAY_01"].completed == 1
    assert rend.max_concurrent == 2
    e.stop()


def test_simultaneous_triggers_on_both_courts_produce_separate_replays(tmp_path):
    e, rend, store = make(tmp_path, renderer=SimRenderer(delay_s=0.2, size_bytes=50_000))
    r = [e.on_serial_line("REPLAY_01\n", T0), e.on_serial_line("REPLAY_02\r\n", T0)]  # mesmo instante, linhas seriais reais
    assert all(x["accepted"] for x in r) and r[0]["localId"] != r[1]["localId"]
    assert e.wait_idle(10)
    assert {x["court_key"] for x in store._x("SELECT court_key FROM replays").fetchall()} == {"REPLAY_01", "REPLAY_02"}
    assert len(files(tmp_path)) == 2 and rend.max_concurrent == 2
    e.stop()


def test_button_debounce_unknown_and_inactive_courts(tmp_path):
    e, _, _ = make(tmp_path, dedup_s=3)
    assert e.trigger("REPLAY_01", T0)["accepted"]
    assert e.trigger("REPLAY_01", T0 + timedelta(seconds=1))["reason"] == "duplicate"
    assert e.trigger("REPLAY_02", T0 + timedelta(seconds=1))["accepted"]  # debounce é por quadra
    assert e.trigger("REPLAY_01", T0 + timedelta(seconds=5))["accepted"]
    assert e.trigger("REPLAY_99")["reason"] == "unknown_court"
    e.apply_config({"storage": {}, "courts": [{"courtKey": "REPLAY_02", "status": "maintenance"}]})
    assert e.trigger("REPLAY_02", T0 + timedelta(seconds=60))["reason"] == "court_inactive"
    e.stop()


def test_queue_limit_rejects_instead_of_growing_forever(tmp_path):
    e, rend, _ = make(tmp_path, max_queue=2, renderer=SimRenderer(delay_s=0.5, size_bytes=10))
    res = [e.trigger("REPLAY_01", T0 + timedelta(seconds=10 * i))["accepted"] for i in range(6)]
    assert res.count(False) >= 2 and e.workers["REPLAY_01"].rejected_full >= 2
    e.stop()


def test_court_and_arena_limits_block_new_replays_without_deleting_anything(tmp_path):
    e, _, store = make(tmp_path, renderer=SimRenderer(size_bytes=100_000))
    e.apply_config({"storage": {"arenaLimitBytes": 450_000}, "courts": [{"courtKey": "REPLAY_01", "storageLimitBytes": 250_000},
                                                                         {"courtKey": "REPLAY_02", "storageLimitBytes": None}]})
    for i in range(4):
        e.trigger("REPLAY_01", T0 + timedelta(seconds=10 * i))
    e.wait_idle(10)
    w1 = e.workers["REPLAY_01"]
    assert w1.completed == 2 and w1.failed == 2 and w1.state == "storage_failure" and w1.last_error == "court_limit"
    before = files(tmp_path)
    for i in range(5):
        e.trigger("REPLAY_02", T0 + timedelta(seconds=10 * i))
    e.wait_idle(10)
    w2 = e.workers["REPLAY_02"]
    assert w2.completed == 2 and w2.last_error == "arena_limit"   # arena: 450k - 200k usados = 2 de 5 cabem
    assert set(before) <= set(files(tmp_path))                     # nenhum arquivo foi removido
    e.apply_config({"storage": {"arenaLimitBytes": None}, "courts": []})  # operador aumenta o limite => recupera sozinho
    e.trigger("REPLAY_01", T0 + timedelta(seconds=500))
    e.wait_idle(10)
    assert w1.state == "ok" and w1.completed == 3
    e.stop()


def test_disk_full_fails_cleanly_keeps_old_files_and_recovers(tmp_path):
    e, rend, store = make(tmp_path)
    e.trigger("REPLAY_01", T0)
    e.wait_idle(5)
    old = files(tmp_path)
    rend.disk_full.set()
    for i in range(3):
        e.trigger("REPLAY_01", T0 + timedelta(seconds=10 * (i + 1)))
    e.trigger("REPLAY_02", T0)
    e.wait_idle(10)
    assert e.workers["REPLAY_01"].state == "storage_failure" and e.workers["REPLAY_01"].failed == 3
    assert files(tmp_path) == old and not list((tmp_path / "r" / "REPLAY_01" / ".tmp").glob("*"))
    assert store.count() == 1                                      # nenhum registro fantasma
    rend.disk_full.clear()
    e.trigger("REPLAY_01", T0 + timedelta(seconds=100))
    e.wait_idle(5)
    assert e.workers["REPLAY_01"].state == "ok" and store.count() == 2
    e.stop()


def test_low_free_disk_reserve_blocks_generation(tmp_path):
    e, _, _ = make(tmp_path, disk_reserve_bytes=1_000_000)
    class DU:  # noqa: E301
        free = 1_050_000
    e.disk_usage = lambda p: DU()
    e.trigger("REPLAY_01", T0)
    e.wait_idle(5)
    assert e.workers["REPLAY_01"].last_error == "disk_free_low" and not files(tmp_path)
    e.stop()


def test_camera_down_is_isolated_per_court_and_recovers(tmp_path):
    e, rend, _ = make(tmp_path)
    rend.camera_up["REPLAY_01"] = False
    e.trigger("REPLAY_01", T0)
    e.trigger("REPLAY_02", T0)
    e.wait_idle(5)
    assert e.workers["REPLAY_01"].state == "camera_down" and e.workers["REPLAY_02"].state == "ok"
    rend.camera_up["REPLAY_01"] = True
    e.trigger("REPLAY_01", T0 + timedelta(seconds=30))
    e.wait_idle(5)
    assert e.workers["REPLAY_01"].state == "ok"
    e.stop()


def test_unexpected_renderer_error_does_not_kill_court_worker(tmp_path):
    class Boom(SimRenderer):
        n = 0

        def render(self, *a, **k):
            Boom.n += 1
            if Boom.n == 1:
                raise RuntimeError("falha inesperada")
            return super().render(*a, **k)
    e, _, store = make(tmp_path, renderer=Boom(size_bytes=1000), retry_backoff_s=0.0)
    r1 = e.trigger("REPLAY_01", T0)
    e.trigger("REPLAY_01", T0 + timedelta(seconds=10))
    e.wait_idle(5)
    w = e.workers["REPLAY_01"]
    # falha transitória => nova tentativa automática: os dois replays saem, nada se perde
    assert (w.failed, w.completed, w.state, w.retries) == (0, 2, "ok", 1) and w.is_alive()
    assert [x["event"] for x in store.job_events(r1["localId"])] == ["queued", "started", "retry_scheduled", "started", "done"]
    assert store.job(r1["localId"])["attempts"] == 2
    e.stop()


def test_retention_candidates_exclude_protected_and_are_dry_run(tmp_path):
    e, _, store = make(tmp_path)
    old = datetime.utcnow() - timedelta(days=90)
    for i in range(4):
        e.trigger("REPLAY_01", old + timedelta(seconds=10 * i))
    e.wait_idle(5)
    ids = [r[0] for r in store._x("SELECT local_id FROM replays ORDER BY captured_at").fetchall()]
    store.set_protected(ids[0], True)
    store.set_central_protected([ids[1]])
    assert store.retention_candidates("REPLAY_01", 30) == ids[2:]
    assert store.retention_candidates("REPLAY_01", 365) == []
    assert len(files(tmp_path)) == 4  # consultar candidatos nunca remove arquivo
    e.stop()


def test_engine_restarts_with_cached_config_when_central_is_down(tmp_path):
    e, _, store = make(tmp_path)
    e.apply_config({"storage": {"arenaLimitBytes": 123}, "courts": [{"courtKey": "REPLAY_01", "storageLimitBytes": 77}]})
    e.stop()
    e2 = Engine("e01", tmp_path / "r", ["REPLAY_01"], store, SimRenderer())
    assert e2.arena_limit == 123 and e2.court_cfg["REPLAY_01"]["storageLimitBytes"] == 77
    e2.stop()


def test_shared_store_is_safe_under_concurrent_court_workers(tmp_path):
    """Regressão: a conexão SQLite é compartilhada; leitura fora do lock já travou workers de quadra."""
    import threading
    store = LocalStore(str(tmp_path / "c.db"))
    errs = []

    def hammer(k):
        try:
            for i in range(300):
                store.add_replay(f"{k}-{i:06d}", k, T0, "p", 10, 1.0, 1, 1, True, "h264")
                store.usage(k)
                store.pending(5)
                store.count("pending")
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=hammer, args=(f"C{n}",)) for n in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and store.count() == 2400 and store.usage() == 24000


def test_court_worker_survives_unexpected_error_outside_render(tmp_path):
    e, _, store = make(tmp_path, retry_backoff_s=0.0)
    real = store.usage
    calls = {"n": 0}

    def flaky(court=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("falha no store")
        return real(court)
    store.usage = flaky
    e.apply_config({"storage": {"arenaLimitBytes": 10**9}, "courts": []})  # força a consulta de uso no store
    e.trigger("REPLAY_01", T0)
    e.trigger("REPLAY_01", T0 + timedelta(seconds=10))
    assert e.wait_idle(5)
    w = e.workers["REPLAY_01"]
    assert w.is_alive() and w.completed == 2 and w.failed == 0 and w.retries == 1
    e.stop()
