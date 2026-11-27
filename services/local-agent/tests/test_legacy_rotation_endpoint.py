"""Contrato de orientação somente-leitura (sem aplicar a revisão v4).

Os testes verificam o isolamento por agente, ausência de segredos, validade do
ângulo e que a consulta não altera status de revisão nem faz commit.
"""
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers.agent_remote import rotation_configuration
from app.models import ConfigRevision, LocalServer


class Query:
    def __init__(self, db, cls):
        self.db, self.cls = db, cls
        self.filters = {}
    def filter_by(self, **kwargs):
        self.filters.update(kwargs)
        return self
    def order_by(self, *args):
        return self
    def first(self):
        if self.cls is LocalServer:
            return next((s for s in self.db.servers if all(
                getattr(s, k) == v for k, v in self.filters.items())), None)
        if self.cls is ConfigRevision:
            rows = [r for r in self.db.revisions if all(
                getattr(r, k) == v for k, v in self.filters.items())]
            return max(rows, key=lambda r: r.version, default=None)
        raise AssertionError("consulta inesperada")


class DB:
    def __init__(self):
        self.servers = [
            SimpleNamespace(id="srv-A", arena_id="arena-A", agent_id="agent-A",
                            status="connected", applied_version=0),
            SimpleNamespace(id="srv-B", arena_id="arena-B", agent_id="agent-B",
                            status="connected", applied_version=0),
        ]
        self.revisions = []
        self.commits = 0
    def query(self, model):
        return Query(self, model)
    def commit(self):
        self.commits += 1


def revision(server_id, arena_id, rotation, *, version=4, status="pendente"):
    snap = {"schema": 1, "serverId": server_id, "arenaId": arena_id,
            "courts": [{"key": "QUADRA_02", "active": True,
                        "camera": {"rotationDeg": rotation, "username": "private",
                                   "password": "DO-NOT-EXPOSE", "host": "private"}}]}
    return SimpleNamespace(id=f"rev-{server_id}-{version}", server_id=server_id,
                           version=version, status=status, snapshot_json=json.dumps(snap))


def test_authenticated_agent_only_sees_own_orientation_without_mutation():
    db = DB()
    db.revisions = [revision("srv-A", "arena-A", 90),
                    revision("srv-B", "arena-B", 270)]
    ag = SimpleNamespace(id="agent-A")
    out = rotation_configuration(db, ag, "QUADRA_02")
    assert out == {
        "serverId": "srv-A", "arenaId": "arena-A", "courtKey": "QUADRA_02",
        "revisionId": "rev-srv-A-4", "version": 4,
        "revisionStatus": "pendente", "configurationState": "desired_unapplied",
        "rotationDeg": 90,
    }
    assert "DO-NOT-EXPOSE" not in json.dumps(out)
    assert db.commits == 0
    assert db.revisions[0].status == "pendente"
    assert rotation_configuration(db, SimpleNamespace(id="agent-B"), "QUADRA_02")["rotationDeg"] == 270


@pytest.mark.parametrize("angle", [-90, 45, "90", None, True])
def test_reject_invalid_rotation(angle):
    db = DB()
    db.revisions = [revision("srv-A", "arena-A", angle)]
    with pytest.raises(HTTPException) as error:
        rotation_configuration(db, SimpleNamespace(id="agent-A"), "QUADRA_02")
    assert error.value.status_code == 409


def test_reject_other_court_unknown_agent_and_mismatched_snapshot():
    db = DB()
    db.revisions = [revision("srv-A", "arena-A", 90)]
    agent = SimpleNamespace(id="agent-A")
    with pytest.raises(HTTPException) as error:
        rotation_configuration(db, agent, "QUADRA_01")
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        rotation_configuration(db, SimpleNamespace(id="unknown"), "QUADRA_02")
    assert error.value.status_code == 409
    db.revisions = [revision("srv-A", "arena-B", 90)]
    with pytest.raises(HTTPException) as error:
        rotation_configuration(db, agent, "QUADRA_02")
    assert error.value.status_code == 409


def test_applied_status_only_if_server_ack_matches():
    db = DB()
    db.revisions = [revision("srv-A", "arena-A", 180, status="aplicado")]
    agent = SimpleNamespace(id="agent-A")
    assert rotation_configuration(db, agent, "QUADRA_02")["configurationState"] == "desired_unapplied"
    db.servers[0].applied_version = 4
    assert rotation_configuration(db, agent, "QUADRA_02")["configurationState"] == "applied"
