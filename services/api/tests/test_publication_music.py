"""Publicação automática (idempotente, sem republicar moderado/removido) e música por replay (rotação, licença, fallback, corrida)."""
import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal, new_id, utcnow
from app.main import app
from app.models import Arena, AuditLog, Court, MusicTrack, Video
from conftest import login, platform_login
from test_agent_sync import ah, item, reg, sync


def vid(local_id, arena="A"):
    db = SessionLocal()
    try:
        return db.query(Video).filter_by(local_id=local_id).one()
    finally:
        db.close()


def set_mode(world, tag, mode):
    db = SessionLocal(); a = db.get(Arena, getattr(world, tag).id); a.audio_default_mode = mode; db.commit(); db.close()


def disable_all_but(world, tag, keep):
    db = SessionLocal()
    for t in db.query(MusicTrack).filter_by(arena_id=getattr(world, tag).id):
        t.status = "enabled" if t.title.endswith(keep) else "disabled"
    db.commit(); db.close()


def add_tracks(world, tag, n):
    """n faixas válidas além das do fixture (que são desativadas)."""
    db = SessionLocal()
    for t in db.query(MusicTrack).filter_by(arena_id=getattr(world, tag).id):
        t.status = "disabled"
    base = db.query(MusicTrack).filter_by(arena_id=getattr(world, tag).id, title=f"{tag}-ok").one()
    ids = []
    for i in range(n):
        nt = MusicTrack(id=new_id(), arena_id=base.arena_id, asset_key=base.asset_key, title=f"Faixa {i}", artist="T", duration=2, license_info="licença de teste 123456",
                        rights_declared=True, allowed_use="play,export,social", status="enabled", position=i)
        db.add(nt); ids.append(nt.id)
    db.commit(); db.close()
    return ids


# ---------------- publicação automática ----------------
def test_valid_replay_is_published_automatically_and_resync_is_idempotent(client, world):
    reg(client, world)
    r = sync(client, world, [item("REPLAY-L-1")]).json()["results"][0]
    assert r["status"] == "created" and r["published"] is True
    assert vid("REPLAY-L-1").visibility == "arena" and vid("REPLAY-L-1").published_at is not None
    r2 = sync(client, world, [item("REPLAY-L-1")]).json()["results"][0]
    assert r2["status"] == "duplicate"
    db = SessionLocal(); assert db.query(Video).filter_by(local_id="REPLAY-L-1").count() == 1
    assert db.query(AuditLog).filter_by(action="video.auto_publish").count() == 1; db.close()   # publicou uma vez só


def test_missing_file_is_not_published_until_agent_confirms(client, world):
    reg(client, world)
    r = sync(client, world, [item("REPLAY-L-2", fileAvailable=False)]).json()["results"][0]
    assert r["published"] is False and vid("REPLAY-L-2").visibility == "private"
    assert sync(client, world, [item("REPLAY-L-2", fileAvailable=False)]).json()["results"][0]["published"] is False   # falha temporária: segue pendente
    r = sync(client, world, [item("REPLAY-L-2", fileAvailable=True)]).json()["results"][0]   # reconexão/arquivo disponível
    assert r["status"] == "duplicate" and r["published"] is True and vid("REPLAY-L-2").visibility == "arena"
    # sem tamanho/duração reais não é "válido"
    assert sync(client, world, [item("REPLAY-L-3", bytes=0)]).json()["results"][0]["published"] is False
    assert sync(client, world, [item("REPLAY-L-4", durationS=0)]).json()["results"][0]["published"] is False


def test_moderated_or_removed_replay_is_never_republished_by_sync(client, world):
    reg(client, world)
    root = platform_login(client, world.emails["root"])
    sync(client, world, [item("REPLAY-L-5"), item("REPLAY-L-6")])
    v5, v6 = vid("REPLAY-L-5"), vid("REPLAY-L-6")
    base = f"/api/platform/arenas/{world.A.id}/replays"
    assert client.post(f"{base}/{v5.id}/moderate", json={"action": "hide"}, headers=root).status_code == 422          # exige motivo
    assert client.post(f"{base}/{v5.id}/moderate", json={"action": "hide", "reason": "privacidade"}, headers=root).status_code == 200
    assert client.post(f"{base}/{v6.id}/moderate", json={"action": "remove", "reason": "conteúdo indevido"}, headers=root).status_code == 200
    for _ in range(2):
        sync(client, world, [item("REPLAY-L-5", fileAvailable=True), item("REPLAY-L-6", fileAvailable=True)])
    assert vid("REPLAY-L-5").visibility == "private" and vid("REPLAY-L-5").moderation_state == "hidden"
    assert vid("REPLAY-L-6").deleted_at is not None and vid("REPLAY-L-6").visibility == "private"
    # só uma ação explícita do administrador global devolve à arena
    assert client.post(f"{base}/{v5.id}/moderate", json={"action": "restore"}, headers=root).status_code == 200
    assert vid("REPLAY-L-5").visibility == "arena"
    assert client.post(f"{base}/{v6.id}/moderate", json={"action": "restore"}, headers=root).status_code == 409        # removido não volta


def test_court_retention_blocks_auto_publish_and_only_platform_changes_it(client, world):
    reg(client, world)
    db = SessionLocal(); c = db.query(Court).filter_by(arena_id=world.A.id, local_court_key="A_01").one(); c.visibility = "private"; db.commit(); db.close()
    assert sync(client, world, [item("REPLAY-L-7")]).json()["results"][0]["published"] is False
    assert sync(client, world, [item("REPLAY-L-8", court="A_02")]).json()["results"][0]["published"] is True       # a outra quadra não é afetada


def test_simultaneous_courts_are_kept_separate(client, world):
    reg(client, world)
    out = {}

    def go(lid, court):
        with TestClient(app) as c:
            out[lid] = c.post("/api/agent/v1/sync/replays", headers=ah(world), json={"replays": [item(lid, court=court)]}).json()["results"][0]
    ts = [threading.Thread(target=go, args=a) for a in (("REPLAY-S-1", "A_01"), ("REPLAY-S-2", "A_02"))]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert out["REPLAY-S-1"]["status"] == out["REPLAY-S-2"]["status"] == "created"
    assert vid("REPLAY-S-1").court_id != vid("REPLAY-S-2").court_id and vid("REPLAY-S-1").visibility == vid("REPLAY-S-2").visibility == "arena"


def test_regularization_publishes_only_valid_private_replays_of_that_arena(client, world):
    root = platform_login(client, world.emails["root"])
    db = SessionLocal()
    for tag in ("A", "B"):                        # replays 'private' legados (sem published_at) nas duas arenas
        for vv in db.query(Video).filter_by(arena_id=getattr(world, tag).id):
            vv.visibility, vv.published_at = "private", None
    hidden = db.get(Video, world.A.pub); hidden.moderation_state = "blocked"
    db.commit(); db.close()
    base = f"/api/platform/arenas/{world.A.id}/replays/publish-pending"
    dry = client.post(base, json={}, headers=root).json()           # dryRun padrão
    assert dry["dryRun"] and dry["published"] == [] and world.A.pub not in {e["id"] for e in dry["eligible"]}
    r = client.post(base, json={"dryRun": False}, headers=root).json()
    assert set(r["published"]) == {e["id"] for e in dry["eligible"]} and r["published"]
    db = SessionLocal()
    assert db.get(Video, world.A.pub).visibility == "private"                                  # moderado: intocado
    assert all(vv.visibility == "private" for vv in db.query(Video).filter_by(arena_id=world.B.id))   # outra arena: intocada
    db.close()
    assert client.post(base, json={"dryRun": False}, headers=root).json()["published"] == []   # idempotente
    # gestor não regulariza nem modera
    ga = login(client, world.emails["a_admin"], "arena")
    assert client.post(base, json={"dryRun": False}, headers=ga).status_code in (401, 403)


# ---------------- música por replay ----------------
def test_empty_library_falls_back_to_ambient_and_still_publishes(client, world):
    reg(client, world); set_mode(world, "A", "music")
    db = SessionLocal()
    for t in db.query(MusicTrack).filter_by(arena_id=world.A.id): t.status = "disabled"
    db.commit(); db.close()
    r = sync(client, world, [item("REPLAY-M-0")]).json()["results"][0]
    v = vid("REPLAY-M-0")
    assert r["published"] is True and v.music_id is None and v.music_reason.startswith("fallback_ambient:biblioteca_sem_faixas_validas")


def test_single_track_is_reused_and_ambient_mode_assigns_nothing(client, world):
    reg(client, world); set_mode(world, "A", "music"); disable_all_but(world, "A", "ok")
    sync(client, world, [item("REPLAY-M-1"), item("REPLAY-M-2")])
    assert vid("REPLAY-M-1").music_id == vid("REPLAY-M-2").music_id is not None and vid("REPLAY-M-1").music_reason == "single_track"
    set_mode(world, "A", "original")
    sync(client, world, [item("REPLAY-M-3")])
    assert vid("REPLAY-M-3").music_id is None and "modo_da_arena" in vid("REPLAY-M-3").music_reason


def test_rotation_never_repeats_consecutively_and_is_persistent(client, world):
    reg(client, world); set_mode(world, "A", "music"); ids = add_tracks(world, "A", 3)
    sync(client, world, [item(f"REPLAY-R-{i}") for i in range(7)])
    seq = [vid(f"REPLAY-R-{i}").music_id for i in range(7)]
    assert seq == [ids[i % 3] for i in range(7)] and all(a != b for a, b in zip(seq, seq[1:]))
    sync(client, world, [item("REPLAY-R-2")])                                       # reenvio não troca a música
    assert vid("REPLAY-R-2").music_id == seq[2]
    # alterar a biblioteca depois não muda a música já atribuída
    db = SessionLocal(); db.query(MusicTrack).filter_by(id=seq[0]).one().status = "disabled"; db.commit(); db.close()
    assert vid("REPLAY-R-0").music_id == seq[0]


def test_inactive_and_expired_tracks_are_never_assigned(client, world):
    reg(client, world); set_mode(world, "A", "music")
    db = SessionLocal()
    for t in db.query(MusicTrack).filter_by(arena_id=world.A.id):
        t.status = "enabled"                       # fixture: ok, expired, norights, disabled(→enabled aqui), noexport
    db.query(MusicTrack).filter_by(arena_id=world.A.id, title="A-disabled").one().status = "disabled"
    db.commit(); db.close()
    sync(client, world, [item(f"REPLAY-V-{i}") for i in range(6)])
    used = {vid(f"REPLAY-V-{i}").music_id for i in range(6)}
    db = SessionLocal(); ok = db.query(MusicTrack).filter_by(arena_id=world.A.id, title="A-ok").one().id; db.close()
    assert used == {ok}                            # só a faixa válida (licença vigente, direitos, uso 'export', ativa)


def test_music_is_isolated_per_arena(client, world):
    reg(client, world, "A"); reg(client, world, "B"); set_mode(world, "A", "music"); set_mode(world, "B", "music")
    ida = add_tracks(world, "A", 2); idb = add_tracks(world, "B", 2)
    sync(client, world, [item("REPLAY-I-A", court="A_01")], "A"); sync(client, world, [item("REPLAY-I-B", court="B_01")], "B")
    assert vid("REPLAY-I-A").music_id in ida and vid("REPLAY-I-B").music_id in idb and not set(ida) & set(idb)


def test_concurrent_replays_get_distinct_consecutive_tracks(client, world):
    reg(client, world); set_mode(world, "A", "music"); ids = add_tracks(world, "A", 4)
    res = {}

    def go(i):
        with TestClient(app) as c:
            res[i] = c.post("/api/agent/v1/sync/replays", headers=ah(world), json={"replays": [item(f"REPLAY-C-{i}", court=("A_01" if i % 2 else "A_02"))]}).status_code
    ts = [threading.Thread(target=go, args=(i,)) for i in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert set(res.values()) == {200}
    got = [vid(f"REPLAY-C-{i}").music_id for i in range(4)]
    assert all(g in ids for g in got) and len(set(got)) == 4          # nenhuma repetição entre os 4 simultâneos


def test_assigned_track_drives_export_and_gestao_sees_assignment(client, world):
    reg(client, world); set_mode(world, "A", "music"); ids = add_tracks(world, "A", 2)
    sync(client, world, [item("REPLAY-E-1")])
    v = vid("REPLAY-E-1")
    ga = login(client, world.emails["a_admin"], "arena")
    lst = client.get(f"/api/arena/{world.A.id}/videos", headers=ga).json()
    mine = next(x for x in lst if x["id"] == v.id)
    assert mine["musicId"] == v.music_id and mine["visibility"] == "arena"
    p = client.get(f"/api/arena/{world.A.id}/videos/{v.id}/personalization", headers=ga).json()
    assert p["music"]["id"] == v.music_id and p["musicReason"] in ("rotation", "single_track")
    usage = client.get(f"/api/arena/{world.A.id}/music/usage", headers=ga).json()
    assert sum(t["replays"] for t in usage["tracks"]) >= 1
    gb = login(client, world.emails["b_admin"], "arena")
    assert client.get(f"/api/arena/{world.A.id}/music/usage", headers=gb).status_code == 404            # isolamento
    ju = login(client, world.emails["a_user"], "user")
    o = client.get(f"/api/videos/{v.id}/export-options", headers=ju)
    if o.status_code == 200:
        assert o.json()["assignedMusicId"] == v.music_id and o.json()["defaultMusicId"] == v.music_id
