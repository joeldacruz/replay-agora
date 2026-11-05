"""Campanhas da arena nao podem ser desligadas pelo atleta no frontend nem por JSON manipulado."""
import json

from app.db import SessionLocal
from app.models import ExportJob


def test_atleta_cannot_disable_sponsor_with_manipulated_payload(client, world, H):
    endpoint = f"/api/videos/{world.A.pub}/exports"
    h = H("a_user")
    for bad in ({"audioMode": "mute", "applySponsors": False},
                {"audioMode": "mute", "applySponsors": None},
                {"audioMode": "mute", "unexpected": 1}):
        res = client.post(endpoint, headers=h, json=bad)
        assert res.status_code == 422, res.text
    r = client.post(endpoint, headers=h, json={"audioMode": "mute"})
    assert r.status_code == 200
    db = SessionLocal()
    j = db.get(ExportJob, r.json()["id"])
    assert j.apply_sponsors is True
    assert j.sponsor_template_version and len(json.loads(j.overlay_spec)) > 0
    db.close()


def test_user_export_without_campaigns_still_possible(client, world, H):
    from app.models import SponsorCampaign
    db = SessionLocal()
    db.query(SponsorCampaign).filter_by(arena_id=world.A.id).delete()
    db.commit()
    db.close()
    r = client.post(f"/api/videos/{world.A.pub}/exports", headers=H("a_user"), json={"audioMode": "mute"})
    assert r.status_code == 200
    db = SessionLocal()
    j = db.get(ExportJob, r.json()["id"])
    assert j.apply_sponsors is False and json.loads(j.overlay_spec) == []
    db.close()
