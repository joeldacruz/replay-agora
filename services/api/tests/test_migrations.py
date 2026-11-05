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


def test_0004_preserves_existing_rows_and_rolls_back_cleanly():
    from sqlalchemy import text
    url = f"sqlite:///{tempfile.mkdtemp()}/m4.db"
    assert alembic(url, "upgrade", "0003").returncode == 0
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("INSERT INTO arenas(id,slug,name,status,timezone,brand_color,allow_self_join,audio_default_mode,audio_original_volume,"
                       "audio_music_volume,audio_allowed_modes,created_at) VALUES('a1','x1','X','active','America/Sao_Paulo','#000000',1,'original',1,0.6,'original',CURRENT_TIMESTAMP)"))
        c.execute(text("INSERT INTO courts(id,arena_id,label,number,status,capture_status,local_court_key,retention_days,visibility,created_at) "
                       "VALUES('c1','a1','Q1',1,'active','unknown','K1',30,'arena',CURRENT_TIMESTAMP)"))
        c.execute(text("INSERT INTO videos(id,arena_id,court_id,code,title,storage_key,mime,bytes,duration,width,height,has_audio,visibility,"
                       "processing_status,captured_at,created_at) VALUES('v1','a1','c1','CODE0001','','arenas/a1/videos/v1.mp4','video/mp4',10,1,1,1,1,'private','ready',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
    assert alembic(url, "upgrade", "0004").returncode == 0, "upgrade 0004"
    with create_engine(url).begin() as c:
        row = c.execute(text("SELECT storage_location, local_id, protected FROM videos WHERE id='v1'")).one()
        assert tuple(row) == ("central", None, 0)  # legado preservado como 'central', desprotegido
        assert c.execute(text("SELECT storage_warn_pct, storage_critical_pct, storage_limit_mb FROM arenas")).one() == (80, 95, None)
    assert alembic(url, "downgrade", "0003").returncode == 0
    with create_engine(url).begin() as c:
        assert c.execute(text("SELECT COUNT(*) FROM videos")).scalar() == 1  # downgrade não perde vídeos
