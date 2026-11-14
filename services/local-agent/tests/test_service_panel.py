"""Agente completo (captura+botoeira serial virtual+fila+link) conversando com a API real: o painel mostra dados reais e o teste manual funciona."""
import json
import os
import pty
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Video
from replay_agent.config import parse
from replay_agent.samples import make_gop_video
from replay_agent.service import AgentService


def wait(cond, timeout=60, step=0.2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return False


@pytest.fixture
def svc(client, world, tmp_path):
    v = make_gop_video(tmp_path / "q.mp4", 6)
    master, slave = pty.openpty()
    cfg = parse({"arena_tag": "arenaA", "data_dir": str(tmp_path / "data"), "serial": [{"name": "botoeira1", "port": os.ttyname(slave)}],
                 "courts": [{"key": "A_01", "label": "Quadra 1", "camera": {"url": f"file:{v}", "segment_s": 1, "buffer_s": 30, "reconnect_min_s": 0.2},
                             "trigger": {"buttons": ["A_01"], "cooldown_s": 1.0}, "clip": {"pre_s": 3, "post_s": 2, "min_pre_s": 2, "min_post_s": 0.5, "gap_tolerance_s": 3.5}},
                            {"key": "A_02", "label": "Quadra 2", "camera": {"url": "file:/nao/existe.mp4", "segment_s": 1, "buffer_s": 30, "reconnect_min_s": 0.1, "reconnect_max_s": 0.3},
                             "trigger": {"buttons": ["A_02"]}, "clip": {"pre_s": 3, "post_s": 2, "min_pre_s": 2, "min_post_s": 0.5}}],
                 "central": {"base_url": "http://127.0.0.1:8000", "node_id": "node-A-0001", "credentials_file": str(tmp_path / "creds.json")}})
    s = AgentService(cfg, http=TestClient(app), credentials={"token": world.A.token})
    s.stack.start()
    s.master = master
    yield s
    s.stop_flag.set()
    s.stack.stop()
    os.close(master); os.close(slave)


def test_real_agent_data_reaches_the_panel_and_manual_test_makes_a_labeled_replay(svc, client, world, H):
    st = svc.stack
    assert wait(lambda: st.recorders["A_01"].index.stats(3.5)["availableSeconds"] >= 6 and st.recorders["A_02"].state == "down")
    assert svc.link.tick()["online"] and svc.link.tick()["online"]          # o estado do link vai no heartbeat seguinte
    ha = H("root")
    d = client.get(f"/api/arena/{world.A.id}/diagnostics", headers=ha).json()
    c1 = next(x for x in d["courts"] if x["courtId"] == world.A.c1)["diag"]
    c2 = next(x for x in d["courts"] if x["courtId"] == world.A.c2)["diag"]
    assert c1["camera"]["state"] == "streaming" and 15 < c1["camera"]["fps"] < 40 and c1["camera"]["buffer"]["availableSeconds"] >= 5
    assert c1["trigger"]["state"] == "connected" and c1["trigger"]["buttons"] == ["A_01"] and c1["queue"]["queued"] == 0
    assert c2["camera"]["state"] == "down" and c2["camera"]["reconnects"] >= 1                     # câmera 2 caída aparece como tal
    codes = {(a["code"], a.get("court")) for a in d["alerts"]}
    assert ("camera_down", "A_02") in codes and not any(c == "camera_down" and k == "A_01" for c, k in codes)
    assert d["agent"]["resources"]["maxFfmpeg"] == 1 and d["agent"]["triggerSources"][0]["kind"] == "serial"
    assert d["agent"]["link"]["state"] == "online"
    # botoeira física (pty): aparece como "último acionamento" com horário e origem
    os.write(svc.master, b"A_01\n")
    assert wait(lambda: (st.store.recent_triggers(1) or [{"outcome": None}])[0]["outcome"] == "accepted", 10)
    time.sleep(1.1)                                                                                 # passa o cooldown da quadra
    # teste manual pelo painel -> comando -> heartbeat -> acionamento -> replay -> sync -> listagem com título de teste
    r = client.post(f"/api/arena/{world.A.id}/courts/{world.A.c1}/test-trigger", headers=ha)
    assert r.status_code == 202
    cid = r.json()["commandId"]
    assert svc.link.tick()["online"]
    cmd = client.get(f"/api/arena/{world.A.id}/commands/{cid}", headers=ha).json()
    assert cmd["status"] == "done" and cmd["outcome"] == "accepted"
    assert wait(lambda: st.store.job_counts().get("done", 0) >= 2, 60)
    assert svc.link.tick()["online"] and st.store.count("pending") == 0
    vids = client.get(f"/api/arena/{world.A.id}/videos", headers=ha).json()
    mine = [v for v in vids if v["title"].startswith("Teste de acionamento")]
    assert len(mine) == 1 and mine[0]["courtId"] == world.A.c1                                      # só o teste é rotulado; o da botoeira não
    d2 = client.get(f"/api/arena/{world.A.id}/diagnostics", headers=ha).json()
    c1b = next(x for x in d2["courts"] if x["courtId"] == world.A.c1)["diag"]
    assert c1b["queue"]["done"] >= 2 and c1b["trigger"]["last"]["source"].startswith("manual:painel-")
    assert c1b["trigger"]["outcomes"].get("accepted", 0) >= 2
    db = SessionLocal()
    assert db.query(Video).filter(Video.arena_id == world.A.id, Video.storage_location == "agent").count() == 2
    db.close()


def test_agent_loses_central_and_keeps_capturing_and_generating(svc, client, world, H):
    st = svc.stack
    assert wait(lambda: st.recorders["A_01"].index.stats(3.5)["availableSeconds"] >= 6)
    assert svc.link.tick()["online"]
    from replay_agent.simulator import FaultyHttp
    fh = FaultyHttp(svc.client.http)
    svc.client.http = fh
    fh.down = True                                                                                   # API central indisponível
    assert svc.link.tick()["online"] is False
    r = st.router.manual("A_01", "ops")
    assert r["outcome"] == "accepted"
    assert wait(lambda: st.store.job(r["jobId"])["state"] == "done", 60)                            # replay gerado localmente, sem central
    assert st.store.count("pending") == 1 and svc.diagnostics()["link"]["state"] == "offline"
    assert any(a["code"] == "central_offline" for a in svc.diagnostics()["alerts"])
    fh.down = False
    assert svc.link.tick()["online"] and st.store.count("pending") == 0                              # reconectou: sincronizou sozinho


def test_cli_check_config_and_hwinfo_do_not_leak_secrets(tmp_path, capsys):
    from replay_agent.service import main
    p = tmp_path / "agent.json"
    p.write_text(json.dumps({"arena_tag": "a1", "data_dir": str(tmp_path / "d"),
                             "courts": [{"key": "REPLAY_01", "camera": {"url": "rtsp://admin:S3nh4@10.0.0.11/s"}, "trigger": {"buttons": ["REPLAY_01"]}}]}))
    p.chmod(0o600)
    assert main(["check-config", "--config", str(p)]) == 0
    out = capsys.readouterr().out
    assert "S3nh4" not in out and "***@10.0.0.11" in out
    assert main(["hwinfo"]) == 0 and "vaapi" in capsys.readouterr().out
    p.chmod(0o644)
    assert main(["check-config", "--config", str(p)]) == 2
