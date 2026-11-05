"""Prévia compartilhada: deduplicação concorrente e autorização por visualizador."""
from concurrent.futures import ThreadPoolExecutor

from app.db import SessionLocal
from app.models import ExportJob, Membership
from app.routers.media import sign
from worker import runner


def test_shared_cache_between_two_authorized_users(client, world, H):
    a, b = H("a_user"), H("a_user2")
    p1 = client.post(f"/api/videos/{world.A.pub}/preview", headers=a)
    p2 = client.post(f"/api/videos/{world.A.pub}/preview", headers=b)
    assert (p1.status_code, p2.status_code) == (200, 200)
    assert p1.json()["id"] == p2.json()["id"]
    jid = p1.json()["id"]
    db = SessionLocal()
    jobs = db.query(ExportJob).filter_by(purpose="preview", video_id=world.A.pub).all()
    assert len(jobs) == 1 and jobs[0].preview_cache_key
    db.close()
    assert runner.process_one()
    url_a = client.get(f"/api/previews/{jid}", headers=a).json()["url"]
    url_b = client.get(f"/api/previews/{jid}", headers=b).json()["url"]
    assert url_a != url_b  # URL assinada vinculada ao visualizador
    assert client.get(url_a).status_code == 200
    assert client.get(url_b, headers={"Range": "bytes=0-99"}).status_code == 206
    assert client.get(sign("export", jid, world.uid["a_user2"], "attachment")).status_code == 403

    db = SessionLocal()
    db.query(Membership).filter_by(arena_id=world.A.id, user_id=world.uid["a_user2"]).update({"status": "inactive"})
    db.commit()
    db.close()
    assert client.get(f"/api/previews/{jid}", headers=b).status_code == 404
    assert client.get(url_b).status_code == 404
    assert client.get(url_a).status_code == 200


def test_cache_never_crosses_arenas(client, world, H):
    a = client.post(f"/api/videos/{world.A.pub}/preview", headers=H("a_user"))
    b = client.post(f"/api/videos/{world.B.pub}/preview", headers=H("b_user"))
    assert a.status_code == b.status_code == 200
    assert a.json()["id"] != b.json()["id"]
    assert client.get(f"/api/previews/{a.json()['id']}", headers=H("b_user")).status_code == 404
    assert client.get(f"/api/previews/{b.json()['id']}", headers=H("a_user")).status_code == 404


def test_fifty_concurrent_preview_requests_share_one_render(client, world, H):
    headers = [H("a_user"), H("a_user2")]
    url = f"/api/videos/{world.A.pub}/preview"

    def request(i):
        response = client.post(url, headers=headers[i % 2])
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=12) as pool:
        responses = list(pool.map(request, range(50)))
    assert all(code == 200 for code, _ in responses), responses
    assert len({payload["id"] for _, payload in responses}) == 1
    db = SessionLocal()
    assert db.query(ExportJob).filter_by(purpose="preview", video_id=world.A.pub).count() == 1
    db.close()
    assert runner.process_one()
    assert not runner.process_one()


def test_cache_requeues_missing_ready_file_once(client, world, H):
    headers = H("a_user")
    a = client.post(f"/api/videos/{world.A.pub}/preview", headers=headers).json()
    assert runner.process_one()
    db = SessionLocal()
    job = db.get(ExportJob, a["id"])
    from app import storage
    storage.path_for(job.output_key).unlink()
    db.close()

    b = client.post(f"/api/videos/{world.A.pub}/preview", headers=H("a_user2")).json()
    assert b["id"] == a["id"] and b["status"] == "queued"
    assert runner.process_one()
    final = client.get(f"/api/previews/{b['id']}", headers=headers).json()
    assert final["status"] == "ready" and final["url"]
