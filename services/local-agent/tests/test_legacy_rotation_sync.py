"""Recebimento da orientação sem aplicação da revisão integral."""
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from replay_agent.legacy_rotation_sync import (
    as_rotation_state, fetch_rotation, receive_rotation,
)


def payload(version=4, rotation=90):
    return {"serverId": "srv-A", "arenaId": "arena-A", "courtKey": "QUADRA_02",
            "revisionId": f"rev-{version}", "version": version,
            "rotationDeg": rotation, "configurationState": "desired_unapplied",
            "revisionStatus": "pendente"}


class FakeAgent:
    def __init__(self, value):
        self.value = value
        self.calls = []
    def call(self, method, endpoint):
        self.calls.append((method, endpoint))
        return self.value


def test_readonly_orientation_fetch_and_private_receipt(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    agent = FakeAgent(payload())
    target = root / "angle.json"
    a = receive_rotation(agent, target, expected_server_id="srv-A", expected_arena_id="arena-A")
    assert a["rotationDeg"] == 90
    assert a["configurationState"] == "desired_unapplied"
    assert target.stat().st_mode & 0o077 == 0
    assert agent.calls == [("GET", "/remote/rotation/QUADRA_02")]
    assert json.loads(target.read_text()) == a
    state = as_rotation_state(a)
    assert state["snapshot"]["courts"][0]["camera"]["rotationDeg"] == 90
    assert receive_rotation(agent, target) == a
    agent.value = payload(5, 270)
    b = receive_rotation(agent, target)
    assert b["version"] == 5
    assert b["rotationDeg"] == 270
    agent.value = payload(4, 90)
    with pytest.raises(ValueError, match="regressão"):
        receive_rotation(agent, target)
    assert json.loads(target.read_text()) == b


@pytest.mark.parametrize("change", [
    {"courtKey": "QUADRA_01"}, {"rotationDeg": 45}, {"rotationDeg": "90"},
    {"configurationState": "unknown"}, {"revisionStatus": "falha"},
    {"version": 0}, {"serverId": ""}, {"arenaId": ""},
])
def test_invalid_configuration_never_persisted(tmp_path, change):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    agent = FakeAgent({**payload(), **change})
    with pytest.raises(ValueError):
        receive_rotation(agent, root / "angle.json")
    assert not (root / "angle.json").exists()


def test_reject_agent_identity_mismatch(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    agent = FakeAgent(payload())
    with pytest.raises(ValueError, match="servidor divergente"):
        receive_rotation(agent, root / "angle.json", expected_server_id="srv-B")
    with pytest.raises(ValueError, match="arena divergente"):
        receive_rotation(agent, root / "angle.json", expected_arena_id="arena-B")
    assert not (root / "angle.json").exists()


def test_same_version_never_changes_rotation(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    agent = FakeAgent(payload())
    receive_rotation(agent, root / "angle.json")
    agent.value = payload(rotation=180)
    with pytest.raises(ValueError, match="divergente"):
        receive_rotation(agent, root / "angle.json")
    assert json.loads((root / "angle.json").read_text())["rotationDeg"] == 90
