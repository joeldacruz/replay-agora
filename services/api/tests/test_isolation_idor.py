"""Isolamento multiarenas: trocar IDs/URLs nunca expõe dados de outra arena."""
from app.security import create_media_token
from conftest import login


def test_arena_admin_cannot_use_other_arena_in_path(client, world, H):
    hb = H("b_admin_arena")
    A = world.A
    for p in ("", "/dashboard", "/courts", "/videos", "/sponsors", "/campaigns", "/music", "/members", "/agents", "/audit", "/exports"):
        assert client.get(f"/api/arena/{A.id}{p}", headers=hb).status_code == 404, p
    assert client.patch(f"/api/arena/{A.id}", headers=hb, json={"name": "hack"}).status_code == 404


def test_cross_arena_resource_ids_via_own_arena_path(client, world, H):
    hb, A, B = H("b_admin_arena"), world.A, world.B
    base = f"/api/arena/{B.id}"
    assert client.patch(f"{base}/videos/{A.pub}", headers=hb, json={"visibility": "private"}).status_code == 404
    assert client.delete(f"{base}/videos/{A.pub}?confirm=true", headers=hb).status_code == 404
    assert client.get(f"{base}/videos/{A.pub}/play", headers=hb).status_code == 404
    assert client.patch(f"{base}/courts/{A.c1}", headers=hb, json={"label": "x"}).status_code == 404
    assert client.patch(f"{base}/music/{A.tracks['ok']}", headers=hb, json={"status": "disabled"}).status_code == 404
    assert client.delete(f"{base}/music/{A.tracks['ok']}", headers=hb).status_code == 404
    assert client.patch(f"{base}/sponsors/{A.sponsor}", headers=hb, data={"name": "xx"}).status_code == 404
    assert client.delete(f"{base}/sponsors/{A.sponsor}", headers=hb).status_code == 404
    assert client.patch(f"{base}/campaigns/{A.campaign}", headers=hb, json={"priority": 1}).status_code == 404
    assert client.delete(f"{base}/campaigns/{A.campaign}", headers=hb).status_code == 404
    # criar campanha em B apontando patrocinador/quadra de A
    r = client.post(f"{base}/campaigns", headers=hb, json={"sponsorId": A.sponsor})
    assert r.status_code == 404
    r = client.post(f"{base}/campaigns", headers=hb, json={"sponsorId": B.sponsor, "courtId": A.c1})
    assert r.status_code == 404
    assert client.post(f"{base}/campaigns/preview", headers=hb, json={"sponsorId": A.sponsor}).status_code == 404
    # padrão de música de B apontando para faixa de A
    assert client.patch(base, headers=hb, json={"defaultMusicId": A.tracks["ok"]}).status_code == 422
    # nada mudou em A
    ha = H("a_admin_arena")
    assert any(v["id"] == A.pub for v in client.get(f"/api/arena/{A.id}/videos", headers=ha).json())
    assert len(client.get(f"/api/arena/{A.id}/music", headers=ha).json()) == 5


def test_athlete_cannot_reach_other_arena_media(client, world, H):
    hb = H("b_user")
    for vid in (world.A.pub, world.A.priv, world.A.mute):
        assert client.get(f"/api/videos/{vid}", headers=hb).status_code == 404
        assert client.get(f"/api/videos/{vid}/play", headers=hb).status_code == 404
        assert client.get(f"/api/videos/{vid}/download", headers=hb).status_code == 404
        assert client.post(f"/api/videos/{vid}/download-link", headers=hb).status_code == 404
        assert client.get(f"/api/videos/{vid}/export-options", headers=hb).status_code == 404
        assert client.post(f"/api/videos/{vid}/exports", headers=hb, json={"audioMode": "mute"}).status_code == 404
        assert client.post(f"/api/videos/{vid}/shares", headers=hb, json={}).status_code == 404
    ids = {v["id"] for v in client.get("/api/videos", headers=hb).json()["items"]}
    assert world.A.pub not in ids and ids  # vê só a própria arena
    assert client.get(f"/api/arenas/{world.A.id}/courts", headers=hb).status_code == 404
    assert client.get(f"/api/videos?arenaId={world.A.id}", headers=hb).json()["items"] == []


def test_export_with_other_arenas_music_rejected(client, world, H):
    r = client.post(f"/api/videos/{world.A.pub}/exports", headers=H("a_user"), json={"audioMode": "music", "musicId": world.B.tracks["ok"]})
    assert r.status_code == 422


def test_other_users_exports_not_accessible(client, world, H):
    r = client.post(f"/api/videos/{world.A.pub}/exports", headers=H("a_user"), json={"audioMode": "mute"})
    assert r.status_code == 200
    eid = r.json()["id"]
    h2 = H("a_user2")
    assert client.get(f"/api/exports/{eid}", headers=h2).status_code == 404
    assert client.post(f"/api/exports/{eid}/download-link", headers=h2).status_code == 404
    assert client.get(f"/api/exports/{eid}/download", headers=h2).status_code == 404
    assert client.get("/api/exports", headers=h2).json() == []


def test_signed_url_scoped_to_user_and_revalidated(client, world, H):
    # token de mídia emitido para outro usuário (b_user) sobre vídeo de A é inútil
    bad = create_media_token("video", world.A.pub, world.uid["b_user"])
    assert client.get(f"/media/{bad}").status_code == 404
    # logo/música só para equipe da própria arena
    tok = create_media_token("logo", world.A.sponsor, world.uid["a_user"])
    assert client.get(f"/media/{tok}").status_code == 404
    tok = create_media_token("logo", world.A.sponsor, world.uid["b_admin"])
    assert client.get(f"/media/{tok}").status_code == 404
    tok = create_media_token("logo", world.A.sponsor, world.uid["a_admin"])
    assert client.get(f"/media/{tok}").status_code == 200
    tok = create_media_token("music", world.A.tracks["ok"], world.uid["b_admin"])
    assert client.get(f"/media/{tok}").status_code == 404
    assert client.get("/media/token-invalido").status_code == 403


def test_agent_token_cannot_write_to_other_arena(client, world):
    hA = {"Authorization": f"Bearer {world.A.token}"}
    body = {"courtKey": "B_01", "idempotencyKey": "evt-cross-0001", "capturedAt": "2026-11-04T12:00:00Z"}
    assert client.post("/api/ingest/events", headers=hA, json=body).status_code == 404  # B_01 não existe em A
    assert client.post("/api/ingest/events", json=body).status_code == 401
    assert client.post("/api/ingest/events", headers={"Authorization": "Bearer rag_errado"}, json=body).status_code == 401
    # token de usuário não serve como agente
    assert client.post("/api/ingest/events", headers=login(client, world.emails["a_admin"], "arena"), json=body).status_code == 401


def test_staff_of_a_cannot_download_private_via_user_app_rules(client, world, H):
    # no App do Usuário a equipe vale como atleta comum: replay privado exige concessão
    assert client.get(f"/api/videos/{world.A.priv}", headers=H("a_admin")).status_code == 404
    # mas o app da arena (staff) reproduz via URL assinada própria
    r = client.get(f"/api/arena/{world.A.id}/videos/{world.A.priv}/play", headers=H("a_admin_arena"))
    assert r.status_code == 200 and client.get(r.json()["url"]).status_code == 200
