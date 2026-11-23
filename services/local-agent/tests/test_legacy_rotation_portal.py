import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from replay_agent.legacy_rotation_portal import export_state


@pytest.mark.parametrize("status, active, rolled_back, partial, accepted", [
    ("aplicado", 7, False, False, True),
    ("pendente", 7, False, False, False),
    ("aplicado", 6, False, False, False),
    ("aplicado", 7, True, False, False),
    ("aplicado", 7, False, True, False),
])
def test_exports_only_the_active_confirmed_quadra02(status, active, rolled_back, partial, accepted):
    server = {"id": "server", "arenaId": "arena", "hostname": "replayagora-srv01",
              "status": "active", "appliedVersion": active}
    revision = {"id": "revision", "serverId": "server", "version": 7, "status": status,
                "finishedAt": "timestamp", "revertedFrom": 5,
                "result": {"activeVersion": 7, "rolledBack": rolled_back, "items": [{"ok": True}]},
                "snapshot": {"serverId": "server", "arenaId": "arena", "courts": [
                    {"key": "QUADRA_01", "active": True, "camera": {"rotationDeg": 0}},
                    {"key": "QUADRA_02", "active": True, "camera": {"rotationDeg": 270, "host": "private"}},
                ]}}
    if partial:
        revision["result"]["items"] = [{"kind": "replay", "key": "", "ok": True}]
    responses = {"/api/platform/servers/server": server, "/api/platform/servers/server/revisions/7": revision}
    if not accepted:
        with pytest.raises(ValueError, match="sem confirmação"):
            export_state(responses.__getitem__, "server", 7)
        return
    state = export_state(responses.__getitem__, "server", 7)
    assert state["activeVersion"] == 7
    assert state["snapshot"]["courts"] == [
        {"key": "QUADRA_02", "active": True, "camera": {"rotationDeg": 270}},
    ]
    assert state["origin"]["revisionId"] == "revision"
    assert state["origin"]["revertedFrom"] == 5
