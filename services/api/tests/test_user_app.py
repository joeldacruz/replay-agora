import time

import jwt as pyjwt
from app.config import settings
from app.db import utcnow


def test_register_login_and_directory_join(client):
    r = client.post("/api/auth/user/register", json={"name": "Novo Atleta", "email": "novo@t.test", "password": "uma-senha-longa-1"})
    assert r.status_code == 201
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.post("/api/auth/user/register", json={"name": "Dup", "email": "NOVO@t.test", "password": "uma-senha-longa-1"}).status_code == 409
    assert client.post("/api/auth/user/register", json={"name": "Fraca", "email": "f@t.test", "password": "curta"}).status_code == 422
    assert client.get("/api/arenas", headers=h).json() == []


def test_join_then_see_only_published(client, world, H):
    h = H("nobody")
    d = client.get("/api/arenas/directory", headers=h).json()
    assert {x["id"] for x in d} == {world.A.id, world.B.id}
    assert client.get("/api/videos", headers=h).json()["items"] == []
    assert client.post(f"/api/arenas/{world.A.id}/join", headers=h).status_code == 201
    items = client.get("/api/videos", headers=h).json()["items"]
    assert {i["id"] for i in items} == {world.A.pub, world.A.mute}  # privado fica de fora
    assert client.get(f"/api/videos/{world.A.priv}", headers=h).status_code == 404


def test_claim_code_grants_private_video(client, world, H):
    h = H("a_user")
    assert client.get(f"/api/videos/{world.A.priv}", headers=h).status_code == 404
    assert client.post("/api/videos/claim", headers=h, json={"code": "NAOEXISTE"}).status_code == 404
    assert client.post("/api/videos/claim", headers=h, json={"code": world.A.priv_code.lower()}).status_code == 200
    assert client.get(f"/api/videos/{world.A.priv}", headers=h).status_code == 200
    # quem reivindica vira membro da arena, mas não vê outros privados
    h2 = H("nobody")
    client.post("/api/videos/claim", headers=h2, json={"code": world.A.priv_code})
    ids = {i["id"] for i in client.get("/api/videos", headers=h2).json()["items"]}
    assert world.A.priv in ids


def test_filters_pagination(client, world, H):
    h = H("a_user")
    r = client.get(f"/api/videos?courtId={world.A.c2}", headers=h).json()["items"]
    assert [i["id"] for i in r] == [world.A.mute]
    p1 = client.get("/api/videos?limit=1", headers=h).json()
    assert len(p1["items"]) == 1 and p1["nextCursor"]
    p2 = client.get(f"/api/videos?limit=1&cursor={p1['nextCursor']}", headers=h).json()
    assert p2["items"][0]["id"] != p1["items"][0]["id"] and p2["nextCursor"] is None
    assert client.get("/api/videos?cursor=lixo", headers=h).status_code == 422
    day = utcnow().date().isoformat()
    assert client.get(f"/api/videos?q={world.A.pub_code[:5]}", headers=h).json()["items"][0]["id"] == world.A.pub
    assert client.get("/api/videos?date=2000-01-01", headers=h).json()["items"] == []
    assert client.get("/api/videos?date=nao-e-data", headers=h).status_code == 422
    assert day  # sanidade


def test_play_range_and_secure_download(client, world, H):
    h = H("a_user")
    # Um replay patrocinado nao pode expor sua origem ao atleta.
    assert client.post(f"/api/videos/{world.A.pub}/download-link", headers=h).status_code == 409
    assert client.get(f"/api/videos/{world.A.pub}/download", headers=h).status_code == 409
    assert client.get(f"/api/videos/{world.A.pub}/play", headers=h).status_code == 409
    # Sem campanha o player conserva o fluxo original com Range.
    from app.db import SessionLocal
    from app.models import SponsorCampaign
    db = SessionLocal()
    assert client.get(f"/media/{__import__('app.security', fromlist=['create_media_token']).create_media_token('video', world.A.pub, world.uid['a_user'], 'inline')}").status_code == 409
    db.query(SponsorCampaign).filter_by(arena_id=world.A.id).delete()
    db.commit()
    db.close()
    url = client.get(f"/api/videos/{world.A.pub}/play", headers=h).json()["url"]
    assert url.startswith("/media/")
    full = client.get(url)
    assert full.status_code == 200 and full.headers["content-type"] == "video/mp4" and "attachment" not in full.headers.get("content-disposition", "")
    assert full.headers["cache-control"] == "private, no-store"
    part = client.get(url, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100 and part.headers["content-range"].startswith("bytes 0-99/")

    dl = client.post(f"/api/videos/{world.A.pub}/download-link", headers=h).json()["url"]
    r = client.get(dl)
    assert r.status_code == 200 and f'filename="replay-{world.A.pub_code}.mp4"' in r.headers["content-disposition"]
    assert r.headers["content-disposition"].startswith("attachment") and int(r.headers["content-length"]) == len(r.content)
    r = client.get(f"/api/videos/{world.A.pub}/download", headers=h)
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment")
    assert client.get(f"/api/videos/{world.A.pub}/download").status_code == 401
    # o diretório de storage não é servido estaticamente
    assert client.get("/arenas/x/videos/y.mp4").status_code == 404


def test_signed_url_expires_and_tampering_fails(client, world, H):
    from app.routers.media import sign
    url = sign("video", world.A.pub, world.uid["a_user"])
    token = url.split("/media/")[1]
    assert client.get(f"/media/{token[:-3]}abc").status_code == 403
    old = pyjwt.encode({"typ": "media", "k": "video", "rid": world.A.pub, "uid": world.uid["a_user"], "d": "inline", "exp": int(time.time()) - 5},
                       settings.secret_key, "HS256")
    assert client.get(f"/media/{old}").status_code == 403
    # token de acesso não funciona como link de mídia
    acc = H("a_user")["Authorization"].split()[1]
    assert client.get(f"/media/{acc}").status_code == 403


def test_deleted_video_link_dies(client, world, H):
    from app.routers.media import sign
    url = sign("video", world.A.pub, world.uid["a_user"])
    assert client.get(url).status_code == 409  # origem bloqueada por patrocínio
    r = client.delete(f"/api/arena/{world.A.id}/videos/{world.A.pub}?confirm=true", headers=H("root"))
    assert r.status_code == 204
    assert client.get(url).status_code == 404
    assert client.get(f"/api/videos/{world.A.pub}", headers=H("a_user")).status_code == 404


def test_unpublish_revokes_access_for_members(client, world, H):
    assert client.patch(f"/api/arena/{world.A.id}/videos/{world.A.pub}", headers=H("root"), json={"visibility": "private", "reason": "teste"}).status_code == 200
    ids = {i["id"] for i in client.get("/api/videos", headers=H("a_user")).json()["items"]}
    assert world.A.pub not in ids


def test_share_link_redeem_expire_revoke(client, world, H):
    owner, other = H("a_user"), H("nobody")
    client.post("/api/videos/claim", headers=owner, json={"code": world.A.priv_code})
    s = client.post(f"/api/videos/{world.A.priv}/shares", headers=owner, json={"hours": 1}).json()
    assert client.get(f"/api/videos/{world.A.priv}", headers=other).status_code == 404
    assert client.post("/api/shares/redeem", headers=other, json={"token": s["token"]}).status_code == 200
    assert client.get(f"/api/videos/{world.A.priv}", headers=other).status_code == 200
    # revogar derruba o acesso concedido pelo link
    assert client.delete(f"/api/videos/{world.A.priv}/shares/{s['id']}", headers=owner).status_code == 204
    assert client.get(f"/api/videos/{world.A.priv}", headers=other).status_code == 404
    assert client.post("/api/shares/redeem", headers=other, json={"token": s["token"]}).status_code == 404
    assert client.post("/api/shares/redeem", headers=other, json={"token": "x" * 30}).status_code == 404
    # quem não tem acesso não cria link
    assert client.post(f"/api/videos/{world.A.priv}/shares", headers=H("a_user2"), json={}).status_code == 404


def test_export_options_follow_arena_policy_and_license(client, world, H):
    h = H("a_user")
    o = client.get(f"/api/videos/{world.A.pub}/export-options", headers=h).json()
    assert o["modes"] == ["original", "music", "mix", "mute"] and [t["title"] for t in o["tracks"]] == ["A-ok"]  # só faixa licenciada/habilitada
    # gestor restringe modos
    r = client.patch(f"/api/arena/{world.A.id}", headers=H("a_admin_arena"), json={"allowedModes": ["original", "mute"], "defaultMode": "mute"})
    assert r.status_code == 200
    o = client.get(f"/api/videos/{world.A.pub}/export-options", headers=h).json()
    assert o["modes"] == ["original", "mute"] and o["defaultMode"] == "mute"
    assert client.post(f"/api/videos/{world.A.pub}/exports", headers=h, json={"audioMode": "mix", "musicId": world.A.tracks["ok"]}).status_code == 422
    assert client.post(f"/api/videos/{world.A.pub}/exports", headers=h, json={}).json()["audioMode"] == "mute"  # padrão da arena


def test_export_validation(client, world, H):
    h = H("a_user")
    for tid in ("expired", "norights", "disabled", "noexport"):
        r = client.post(f"/api/videos/{world.A.pub}/exports", headers=h, json={"audioMode": "music", "musicId": world.A.tracks[tid]})
        assert r.status_code == 422, tid
    assert client.post(f"/api/videos/{world.A.pub}/exports", headers=h, json={"audioMode": "music"}).status_code == 200  # usa a faixa válida padrão
    assert client.post(f"/api/videos/{world.A.pub}/exports", headers=h, json={"audioMode": "karaoke"}).status_code == 422
    assert client.post(f"/api/videos/{world.A.pub}/exports", headers=h, json={"audioMode": "mix", "musicId": world.A.tracks["ok"], "musicVolume": 9}).status_code == 422
    assert client.post(f"/api/videos/{world.A.pub}/exports", headers=h, json={"audioMode": "music", "musicId": "x"}).status_code == 422


def test_export_quota_per_user(client, world, H):
    h = H("a_user")
    codes = [client.post(f"/api/videos/{world.A.pub}/exports", headers=h, json={"audioMode": "mix", "musicId": world.A.tracks["ok"], "musicVolume": v / 10}).status_code
             for v in range(1, 6)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429


def test_export_download_only_when_ready_and_owner(client, world, H):
    from worker import runner
    h = H("a_user")
    e = client.post(f"/api/videos/{world.A.pub}/exports", headers=h, json={"audioMode": "mute"}).json()
    assert e["status"] == "queued" and e["downloadUrl"] is None
    assert client.post(f"/api/exports/{e['id']}/download-link", headers=h).status_code == 409
    assert client.get(f"/api/exports/{e['id']}/download", headers=h).status_code == 409
    runner.process_one()
    e = client.get(f"/api/exports/{e['id']}", headers=h).json()
    assert e["status"] == "ready"
    r = client.get(e["downloadUrl"])
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment") and f"-mute.mp4" in r.headers["content-disposition"]
    assert client.get(f"/api/exports/{e['id']}/download", headers=h).status_code == 200
