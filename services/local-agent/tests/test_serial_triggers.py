"""Protocolo REPLAY em portas seriais VIRTUAIS (pty): botoeiras por quadra, debounce/cooldown, ruído, desconexão e recuperação.
Nenhuma porta física é aberta."""
import os
import pty
import time
from datetime import datetime

import pytest

from replay_agent.engine import Engine
from replay_agent.render import SimRenderer
from replay_agent.store import LocalStore
from replay_agent.triggers import SerialTrigger, TriggerRouter


def wait(cond, timeout=8, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return False


class Rig:
    pass


@pytest.fixture
def rig(tmp_path):
    r = Rig()
    r.master, slave = pty.openpty()
    r.path = os.ttyname(slave)
    r.slave = slave                                              # mantém o lado escravo aberto (como o driver USB faria)
    r.store = LocalStore(str(tmp_path / "l.db"))
    r.engine = Engine("t", tmp_path / "r", ["REPLAY_01", "REPLAY_02"], r.store, SimRenderer(size_bytes=100), dedup_s=3.0)
    r.engine.configure_court("REPLAY_02", cooldown_s=1.0)        # política independente por quadra
    r.router = TriggerRouter(r.engine, r.store, {"REPLAY_01": "REPLAY_01", "REPLAY_02": "REPLAY_02", "REPLAY_02_NT": "REPLAY_02"},
                             {"REPLAY_01": 0.3, "REPLAY_02": 0.3})
    r.ser = SerialTrigger("botoeira1", r.path, 9600, reopen_s=0.05)
    r.ser.start(r.router.handle)
    assert wait(lambda: r.ser.state == "connected")
    yield r
    r.ser.stop()
    r.engine.stop()
    for fd in (r.master, r.slave):
        try:
            os.close(fd)
        except OSError:
            pass


def send(rig, data: bytes):
    os.write(rig.master, data)


def jobs(rig):
    return rig.store._x("SELECT job_id, court_key, source, button FROM jobs ORDER BY created_at").fetchall()


def test_each_button_line_creates_a_job_for_its_court_with_time_and_origin(rig):
    t = time.time()
    send(rig, b"REPLAY_01\r\n")
    assert wait(lambda: len(jobs(rig)) == 1)
    j = jobs(rig)[0]
    assert j["court_key"] == "REPLAY_01" and j["source"] == "serial:botoeira1" and j["button"] == "REPLAY_01"
    row = rig.store.recent_triggers(1)[0]
    assert abs(row["ts"] - t) < 2 and row["outcome"] == "accepted" and row["job_id"] == j["job_id"]       # horário e origem registrados
    assert rig.engine.wait_idle(10) and rig.store.count() == 1


def test_two_courts_triggered_in_the_same_write_are_independent(rig):
    send(rig, b"REPLAY_01\nREPLAY_02\n")                         # chegam juntos no mesmo read()
    assert wait(lambda: len(jobs(rig)) == 2)
    assert {j["court_key"] for j in jobs(rig)} == {"REPLAY_01", "REPLAY_02"}
    assert rig.engine.wait_idle(10) and rig.store.count() == 2


def test_multiple_buttons_on_one_court_share_the_court_cooldown(rig):
    send(rig, b"REPLAY_02\n")
    assert wait(lambda: len(jobs(rig)) == 1)
    send(rig, b"REPLAY_02_NT\n")                                 # botoeira no-touch da MESMA quadra, dentro do cooldown (1 s)
    assert wait(lambda: rig.router.counts.get("duplicate") == 1)
    assert len(jobs(rig)) == 1
    time.sleep(1.1)
    send(rig, b"REPLAY_02_NT\n")                                 # depois do cooldown: novo replay legítimo, origem identificada
    assert wait(lambda: len(jobs(rig)) == 2)
    assert jobs(rig)[1]["button"] == "REPLAY_02_NT"


def test_debounce_ignores_contact_bounce_of_the_same_button(rig):
    for _ in range(4):
        send(rig, b"REPLAY_01\n")
        time.sleep(0.03)                                         # repique mecânico (<< 0,3 s)
    assert wait(lambda: sum(rig.router.counts.values()) == 4)
    assert rig.router.counts["accepted"] == 1 and rig.router.counts["debounce"] == 3
    assert len(jobs(rig)) == 1
    assert rig.store.trigger_outcomes("REPLAY_01") == {"accepted": 1, "debounce": 3}                    # tudo auditável


def test_unknown_noise_partial_and_binary_lines_never_break_the_reader(rig):
    send(rig, b"\xff\xfe\x00garbage\n")                           # binário
    send(rig, b"REPLAY_9" + b"9" * 80 + b"\n")                    # linha longa
    send(rig, b"hello world\n")                                   # texto fora do protocolo
    send(rig, b"REPLAY_99\n")                                     # bem formada, mas botoeira desconhecida
    send(rig, b"REPLAY_0")                                        # linha partida em dois pacotes
    time.sleep(0.15)
    send(rig, b"1\n")
    assert wait(lambda: len(jobs(rig)) == 1)
    assert jobs(rig)[0]["court_key"] == "REPLAY_01"
    d = rig.ser.diagnostics()
    assert d["garbled"] >= 2 and d["state"] == "connected" and d["lines"] >= 2
    assert rig.router.counts.get("unknown_button") == 1
    assert rig.store.recent_triggers(5)[0]["button"] == "REPLAY_01"


def test_lines_in_the_same_read_get_distinct_arrival_times():
    m, sl = pty.openpty()                                                  # pty próprio: não mexe no leitor da fixture
    stamps = []
    ser = SerialTrigger("ts", os.ttyname(sl), reopen_s=0.05)
    ser.start(lambda msg, src, ts: stamps.append((msg, ts)))
    try:
        assert wait(lambda: ser.state == "connected")
        time.sleep(0.2)
        os.write(m, b"REPLAY_01\nREPLAY_02\nREPLAY_01\n")               # chegam juntas
        assert wait(lambda: len(stamps) == 3)
        t = [x[1] for x in stamps]
        assert t[0] < t[1] < t[2] and abs((t[1] - t[0]) - 10 * 10 / 9600) < 0.004      # 10 bytes/linha a 9600 baud ≈ 10,4 ms
    finally:
        ser.stop()
        os.close(m); os.close(sl)


def test_reader_releases_exclusive_lock_on_close_so_the_port_can_be_reopened():
    """Sem TIOCNXCL, um reinício do leitor (ou o do legado, em rollback) tomaria EBUSY enquanto o driver mantém a porta aberta."""
    m, sl = pty.openpty()
    path = os.ttyname(sl)
    got = []
    a = SerialTrigger("a", path, reopen_s=0.05)
    a.start(lambda *x: None)
    try:
        assert wait(lambda: a.state == "connected")
    finally:
        a.stop()
    b = SerialTrigger("b", path, reopen_s=0.05)
    b.start(lambda msg, src, ts: got.append(msg))
    try:
        assert wait(lambda: b.state == "connected", 5)                         # reabre (não-root: falharia com EBUSY sem o TIOCNXCL)
        os.write(m, b"REPLAY_01\n")
        assert wait(lambda: got == ["REPLAY_01"])
    finally:
        b.stop()
        os.close(m); os.close(sl)


def test_no_event_is_lost_under_a_burst_on_both_courts(rig):
    rig.engine.configure_court("REPLAY_01", cooldown_s=0.0)
    rig.engine.configure_court("REPLAY_02", cooldown_s=0.0)
    rig.router.debounce = {"REPLAY_01": 0.0, "REPLAY_02": 0.0}
    n = 20
    for i in range(n):
        send(rig, b"REPLAY_01\nREPLAY_02\n")
        time.sleep(0.004)                                        # ms distintos => chaves distintas
    assert wait(lambda: sum(rig.router.counts.values()) == 2 * n, 15)
    accepted = rig.router.counts.get("accepted", 0)
    dup = rig.router.counts.get("duplicate", 0)
    assert accepted == 2 * n and dup == 0                          # nada some e nada colide: horário estritamente crescente por linha
    assert rig.engine.wait_idle(30)
    ids = [r["local_id"] for r in rig.store._x("SELECT local_id FROM replays").fetchall()]
    assert len(ids) == len(set(ids)) == accepted


def test_unplug_and_replug_reconnects_automatically(tmp_path):
    link = tmp_path / "botoeira"                                  # equivalente a uma regra udev /dev/replayagora/botoeira
    m1, s1 = pty.openpty()
    os.symlink(os.ttyname(s1), link)
    got = []
    ser = SerialTrigger("b", str(link), reopen_s=0.05)
    ser.start(lambda m, src, ts: got.append(m))
    try:
        assert wait(lambda: ser.state == "connected")
        os.write(m1, b"REPLAY_01\n")
        assert wait(lambda: got == ["REPLAY_01"])
        os.close(m1); os.close(s1)                                # USB removido
        assert wait(lambda: ser.state == "disconnected" and ser.reconnects >= 1)
        os.unlink(link)
        time.sleep(0.2)
        assert ser.diagnostics()["state"] == "disconnected" and ser.last_error                       # porta sumiu: sem crash
        m2, s2 = pty.openpty()                                    # USB reconectado (novo dispositivo, mesmo nome estável)
        os.symlink(os.ttyname(s2), link)
        assert wait(lambda: ser.state == "connected", 10)
        os.write(m2, b"REPLAY_02\n")
        assert wait(lambda: got == ["REPLAY_01", "REPLAY_02"])
        os.close(m2); os.close(s2)
    finally:
        ser.stop()


def test_exclusive_open_is_enabled_so_a_second_reader_cannot_share_the_port(rig):
    """TIOCEXCL: sem CAP_SYS_ADMIN (ex.: o leitor.py como usuário comum) o segundo open recebe EBUSY — comportamento real verificado no CI
    (não-root). Como root o kernel ignora o bloqueio, então ali o teste confere o estado do tty (TIOCGEXCL)."""
    import errno
    import fcntl
    if os.geteuid() != 0:
        with pytest.raises(OSError) as e:
            os.open(rig.path, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
        assert e.value.errno == errno.EBUSY
        other = SerialTrigger("intruso", rig.path, reopen_s=0.05)
        other.start(lambda *a: None)
        try:
            assert wait(lambda: other.state == "busy", 3) and other.diagnostics()["lastError"]        # estado "porta ocupada" no painel
        finally:
            other.stop()
        return
    fd = os.open(rig.path, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        assert int.from_bytes(fcntl.ioctl(fd, 0x80045440, b"\0\0\0\0"), "little") == 1       # TIOCGEXCL
    finally:
        os.close(fd)


def test_manual_test_trigger_goes_through_the_same_path_and_is_labeled(rig):
    r = rig.router.manual("REPLAY_01", "gestor@arena")
    assert r["outcome"] == "accepted"
    row = rig.store.recent_triggers(1)[0]
    assert row["source"] == "manual:gestor@arena" and row["button"] is None and row["court_key"] == "REPLAY_01"
    assert rig.router.manual("REPLAY_99", "x")["outcome"] == "unknown_court"
    assert rig.router.manual("REPLAY_01", "x")["outcome"] == "duplicate"                                 # cooldown da quadra vale também no teste


def test_crash_between_log_and_job_is_recovered_once(tmp_path):
    store = LocalStore(str(tmp_path / "l.db"))
    e = Engine("t", tmp_path / "r", ["REPLAY_01"], store, SimRenderer(size_bytes=10), dedup_s=0)
    ts = time.time()
    store.log_trigger(ts, "serial:b", "REPLAY_01", "REPLAY_01")            # processo caiu logo depois do registro, antes do job
    assert store.count() == 0 and store.job_counts() == {}
    router = TriggerRouter(e, store, {"REPLAY_01": "REPLAY_01"})
    assert router.recover_pending() == 1 and e.wait_idle(5)
    assert store.count() == 1 and store.recent_triggers(1)[0]["outcome"] == "accepted"
    assert router.recover_pending() == 0                                    # já resolvido
    # simula uma segunda recuperação do mesmo evento (log reaberto): job idempotente, não duplica
    store._x("UPDATE trigger_log SET outcome=NULL")
    router.recover_pending()
    e.wait_idle(5)
    assert store.count() == 1 and store.recent_triggers(1)[0]["outcome"] == "duplicate"
    e.stop()
