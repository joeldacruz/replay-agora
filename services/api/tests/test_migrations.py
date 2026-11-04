import subprocess
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, inspect

from app.db import Base

API_DIR = Path(__file__).resolve().parents[1]


def alembic(db_url, *args):
    import os
    env = {**os.environ, "DATABASE_URL": db_url}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=API_DIR, env=env, capture_output=True, text=True)


def test_migrations_upgrade_match_models_and_downgrade():
    url = f"sqlite:///{tempfile.mkdtemp()}/m.db"
    r = alembic(url, "upgrade", "head")
    assert r.returncode == 0, r.stderr
    insp = inspect(create_engine(url))
    assert set(Base.metadata.tables) <= set(insp.get_table_names())
    for t in Base.metadata.tables.values():
        assert {c.name for c in t.columns} == {c["name"] for c in insp.get_columns(t.name)}, t.name
    r = alembic(url, "check")  # nenhum modelo sem migração
    assert r.returncode == 0, r.stdout + r.stderr
    assert alembic(url, "downgrade", "base").returncode == 0
