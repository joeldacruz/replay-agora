"""Prévia patrocinada: sem vazamento do MP4 original, quota separada e renderização real."""
from app import mediainfo, storage
from app.db import SessionLocal
from app.models import Arena, ExportJob, Plan, Sponsor, SponsorCampaign
from app.routers.media import sign
from worker import runner


def test_branded_preview_renders_and_does_not_expose_raw_video(client, world, H):
    h = H("a_user")
    vid = world.A.pub
    assert client.get(f"/api/videos/{vid}/play", headers=h).status_code == 409
    assert client.get(sign("video", vid, world.uid["a_user"])).status_code == 409
    assert client.post(f"/api/videos/{vid}/preview", headers=H("b_user")).status_code == 404
    first = client.post(f"/api/videos/{vid}/preview", headers=h)
    assert first.status_code == 200, first.text
    data = first.json()
    assert data["sponsored"] is True and data["status"] == "queued" and data["url"] is None
    jid = data["id"]
    assert client.post(f"/api/videos/{vid}/preview", headers=h).json()["id"] == jid
    assert client.get(f"/api/exports/{jid}", headers=h).status_code == 404
    assert client.post(f"/api/exports/{jid}/download-link", headers=h).status_code == 404

    assert runner.process_one()
    ready = client.get(f"/api/previews/{jid}", headers=h)
    assert ready.status_code == 200 and ready.json()["status"] == "ready"
    url = ready.json()["url"]
    assert url.startswith("/media/")
    assert client.get(url).status_code == 200
    part = client.get(url, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100
    assert client.get(sign("export", jid, world.uid["a_user"], "attachment")).status_code == 403
    assert client.get(url.replace("/media/", "/media/x")).status_code == 403

    db = SessionLocal()
    j = db.get(ExportJob, jid)
    assert j.purpose == "preview" and j.apply_sponsors
    assert j.output_key and storage.path_for(j.output_key).exists()
    assert mediainfo.video_info(storage.path_for(j.output_key)).duration > 2
    db.close()
    assert client.post(f"/api/videos/{vid}/preview", headers=h).json()["id"] == jid
    assert client.get("/api/exports", headers=h).json() == []


def test_no_campaign_uses_raw_player_without_rendering(client, world, H):
    db = SessionLocal()
    db.query(SponsorCampaign).filter_by(arena_id=world.A.id).delete()
    db.commit()
    db.close()
    h = H("a_user")
    result = client.post(f"/api/videos/{world.A.pub}/preview", headers=h).json()
    assert result["status"] == "ready" and result["sponsored"] is False
    assert client.get(result["url"]).status_code == 200
    assert client.get(f"/api/videos/{world.A.pub}/play", headers=h).status_code == 200
    db = SessionLocal()
    assert db.query(ExportJob).count() == 0
    db.close()


def test_preview_is_not_charged_to_daily_custom_export_quota(client, world, H):
    db = SessionLocal()
    db.get(Plan, world.plan_id).max_exports_per_user_day = 1
    db.commit()
    db.close()
    h = H("a_user")
    p = client.post(f"/api/videos/{world.A.pub}/preview", headers=h).json()
    assert p["status"] == "queued"
    assert runner.process_one()
    assert client.post(f"/api/videos/{world.A.pub}/exports", headers=h, json={"audioMode": "mute"}).status_code == 200
    assert client.post(f"/api/videos/{world.A.pub}/exports", headers=h, json={"audioMode": "original"}).status_code == 429


def test_campaign_update_revokes_old_preview_and_download(client, world, H):
    h = H("a_user")
    preview = client.post(f"/api/videos/{world.A.pub}/preview", headers=h).json()
    assert runner.process_one()
    old_url = client.get(f"/api/previews/{preview['id']}", headers=h).json()["url"]
    assert client.get(old_url).status_code == 200

    exported = client.post(f"/api/videos/{world.A.pub}/exports", headers=h, json={"audioMode": "mute"}).json()
    assert runner.process_one()
    direct = client.post(f"/api/exports/{exported['id']}/download-link", headers=h).json()["url"]
    assert client.get(direct).status_code == 200

    db = SessionLocal()
    c = db.get(SponsorCampaign, world.A.campaign)
    c.opacity = 0.7
    db.commit()
    db.close()

    assert client.get(old_url).status_code == 409
    assert client.get(direct).status_code == 409
    assert client.get(f"/api/previews/{preview['id']}", headers=h).status_code == 409
    assert client.post(f"/api/exports/{exported['id']}/download-link", headers=h).status_code == 409
    again = client.post(f"/api/videos/{world.A.pub}/preview", headers=h).json()
    assert again["id"] != preview["id"]


def test_missing_sponsor_asset_fails_closed(client, world, H):
    h = H("a_user")
    p = client.post(f"/api/videos/{world.A.pub}/preview", headers=h).json()
    db = SessionLocal()
    logo = db.get(Sponsor, world.A.sponsor)
    storage.path_for(logo.asset_key).unlink()
    db.close()
    assert runner.process_one()
    result = client.get(f"/api/previews/{p['id']}", headers=h).json()
    assert result["status"] == "failed" and result["url"] is None
    assert "logotipo" in result["error"]


def test_disabled_original_audio_uses_mute_for_preview(client, world, H):
    db = SessionLocal()
    db.get(Arena, world.A.id).audio_allowed_modes = "mute,music"
    db.commit()
    db.close()
    h = H("a_user")
    p = client.post(f"/api/videos/{world.A.pub}/preview", headers=h).json()
    db = SessionLocal()
    assert db.get(ExportJob, p["id"]).audio_mode == "mute"
    db.close()
