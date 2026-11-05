"""Retenção conservadora: cache antigo somente, nunca origem nem job ativo."""
from datetime import timedelta

from app import storage
from app.db import SessionLocal, utcnow
from app.models import ExportJob, Video
from app.retention import prune_previews
from worker import runner


def test_cache_cleanup_dry_run_and_apply_preserves_original(client, world, H):
    h = H("a_user")
    p = client.post(f"/api/videos/{world.A.pub}/preview", headers=h).json()
    assert runner.process_one()
    db = SessionLocal()
    job = db.get(ExportJob, p["id"])
    source = storage.path_for(db.get(Video, world.A.pub).storage_key)
    preview = storage.path_for(job.output_key)
    assert source.exists() and preview.exists()
    job.created_at = utcnow() - timedelta(days=10)
    db.commit()

    dry = prune_previews(db, days=7)
    assert dry["dry_run"] and dry["eligible"] == 1 and dry["removed"] == 0
    assert preview.exists() and db.get(ExportJob, p["id"])
    deleted = prune_previews(db, days=7, apply=True)
    assert deleted["removed"] == 1 and deleted["bytes_freed"] > 0
    assert not preview.exists() and source.exists()
    assert db.get(ExportJob, p["id"]) is None
    db.close()

    fresh = client.post(f"/api/videos/{world.A.pub}/preview", headers=h).json()
    assert fresh["id"] != p["id"] and fresh["status"] == "queued"


def test_retention_does_not_delete_pending_or_foreign_path(client, world, H):
    h = H("a_user")
    preview = client.post(f"/api/videos/{world.A.pub}/preview", headers=h).json()
    db = SessionLocal()
    j = db.get(ExportJob, preview["id"])
    j.created_at = utcnow() - timedelta(days=10)
    db.commit()
    assert prune_previews(db, days=7, apply=True)["eligible"] == 0
    assert db.get(ExportJob, j.id) is not None  # queued não é eliminado

    j.status = "failed"
    j.output_key = storage.make_key(world.A.id, "videos", "origem.mp4")
    db.commit()
    result = prune_previews(db, days=7, apply=True)
    assert result["skipped_unsafe"] == 1 and result["removed"] == 0
    db.close()
