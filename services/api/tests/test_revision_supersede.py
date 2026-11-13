
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import remote_config as rc


def test_nova_revisao_salva_substitui_pendente():
    db = MagicMock()
    server = SimpleNamespace(id="servidor-teste", arena_id="arena-teste")
    current = SimpleNamespace(version=1)

    snapshot = {
        "schema": 1,
        "serverId": server.id,
        "arenaId": server.arena_id,
        "courts": [],
        "controllers": [],
    }

    with (
        patch.object(rc, "latest", return_value=current),
        patch.object(rc, "validate_snapshot", return_value=[]),
        patch.object(rc, "supersede_pending") as supersede,
    ):
        rev = rc.create_revision(
            db=db,
            server=server,
            user_id="usuario-teste",
            expected=1,
            note="Teste de substituicao",
            apply=False,
            snapshot=snapshot,
        )

    assert rev.version == 2
    assert rev.status == "salvo"
    assert rev.allow_restart is False

    supersede.assert_called_once_with(
        db, server.id, keep_version=2
    )

    db.add.assert_called_once()
    db.flush.assert_called_once()
