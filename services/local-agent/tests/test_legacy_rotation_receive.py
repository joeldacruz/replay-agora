import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from replay_agent.legacy_rotation_receive import RotationReceipt


def revision(version, angle):
    return {"serverId": "server", "status": "pendente", "version": version, "id": f"r{version}",
            "snapshot": {"serverId": "server", "arenaId": "arena", "schema": 1,
                         "controllers": [{"id": "ignorado"}], "courts": [
                             {"key": "QUADRA_01", "active": True},
                             {"key": "QUADRA_02", "active": True,
                              "camera": {"rotationDeg": angle, "host": "privado"}},
                         ]}}


def test_receipt_restart_confirmation_and_rollback(tmp_path):
    calls = []

    def get(version):
        calls.append(version)
        if calls == [3]:
            raise TimeoutError()
        return revision(version, version * 90 % 360)

    def reopen():
        return RotationReceipt(tmp_path, "server", "arena", get)

    adapter = reopen()
    hint = {"linked": True, "pendingVersion": 3}
    with pytest.raises(TimeoutError):
        adapter.tick(hint)
    assert not adapter.path.exists()
    assert reopen().tick(hint)["rotationDeg"] == 270
    assert reopen().tick(hint) is None
    assert reopen().confirm("r3") == reopen().confirm("r3")
    hint["pendingVersion"] = 4
    assert reopen().tick(hint)["rotationDeg"] == 0
    assert reopen().confirm("r4")["version"] == 4
    assert reopen().rollback()
    assert not reopen().rollback()
    assert reopen().tick(hint) is None
    assert calls == [3, 3, 4]
    raw = adapter.path.read_text()
    state = json.loads(raw)
    assert state["confirmed"]["version"] == 3
    assert state["highestVersion"] == 4
    assert "activeVersion" not in raw and "privado" not in raw and "controllers" not in raw


@pytest.mark.parametrize("change", [
    lambda rev: rev.update(status="substituido"),
    lambda rev: rev.update(version=5),
    lambda rev: rev["snapshot"].update(arenaId="outra"),
    lambda rev: rev["snapshot"].update(schema=2),
    lambda rev: rev["snapshot"]["courts"][1]["camera"].pop("rotationDeg"),
    lambda rev: rev["snapshot"]["courts"][1]["camera"].update(rotationDeg=17),
])
def test_invalid_revision_leaves_no_receipt(tmp_path, change):
    candidate = revision(4, 90)
    change(candidate)
    adapter = RotationReceipt(tmp_path, "server", "arena", lambda version: candidate)
    with pytest.raises(ValueError):
        adapter.tick({"linked": True, "pendingVersion": 4})
    assert not adapter.path.exists()


def test_portal_callback_only_reads_server_and_revision(tmp_path, monkeypatch):
    from replay_agent import legacy_rotation_receive

    calls = []
    server_path = "/api/platform/servers/server"

    def get(path):
        calls.append(path)
        if path == server_path:
            return {"id": "server", "arenaId": "arena", "hostname": "replayagora-srv01",
                    "latestVersion": 4, "latestStatus": "pendente"}
        assert path == server_path + "/revisions/4"
        return revision(4, 90)

    monkeypatch.setattr(legacy_rotation_receive, "portal_getter", lambda token: get)
    adapter = legacy_rotation_receive.portal_receipt(tmp_path, "server", "arena", "fixture")
    assert adapter.tick({"linked": True, "pendingVersion": 4})["rotationDeg"] == 90
    adapter.confirm("r4")
    adapter.rollback()
    assert calls == [server_path + "/revisions/4", server_path]
