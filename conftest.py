"""Fixtures compartilhadas (API + worker). Ambiente isolado em diretório temporário."""
import os
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="replay-tests-"))
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_TMP}/test.db", "STORAGE_DIR": str(_TMP / "storage"), "SECRET_KEY": "test-secret-not-for-production-0123456789abcdef",
    "LOGIN_MAX_ATTEMPTS": "1000", "APP_ENV": "test", "EXPORT_TIMEOUT_S": "60",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import samples, storage  # noqa: E402
from app.db import Base, SessionLocal, engine, new_id, utcnow  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (Arena, ArenaAgent, Court, Membership, MusicTrack, Plan, ReplayEvent, Sponsor, SponsorCampaign,  # noqa: E402
                        User, Video)
from app.routers import auth as auth_router  # noqa: E402
from app.security import hash_agent_token, hash_password  # noqa: E402

PASSWORD = "Senha-de-teste-123"
ORIG_HZ, MUSIC_HZ = 300, 3000


@pytest.fixture(scope="session")
def media():
    d = _TMP / "samples"
    return {
        "audio": samples.make_video(d / "audio.mp4", 3, True, tone=ORIG_HZ),
        "mute": samples.make_video(d / "mute.mp4", 3, False),
        "tone": samples.make_tone(d / "music.mp3", MUSIC_HZ, 2, 0.8),  # mais curta que o vídeo: testa loop
        "logo": samples.make_logo(d / "logo.png", (255, 0, 255), "L"),
    }


@pytest.fixture(autouse=True)
def fresh():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    auth_router.limiter.reset()
    shutil.rmtree(_TMP / "storage", ignore_errors=True)
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


class World:
    pass


def _add_video(db, arena, court, src, vis, minutes=5, code=None, title=""):
    from app import mediainfo
    info = mediainfo.video_info(src)
    vid = new_id()
    key = storage.make_key(arena.id, "videos", f"{vid}.mp4")
    shutil.copy(src, storage.ensure_parent(key))
    thumb = storage.make_key(arena.id, "thumbs", f"{vid}.jpg")
    mediainfo.make_thumbnail(storage.path_for(key), storage.ensure_parent(thumb), 1)
    at = utcnow() - timedelta(minutes=minutes)
    ev = ReplayEvent(arena_id=arena.id, court_id=court.id, captured_at=at, idempotency_key=f"t-{vid}", status="completed")
    db.add(ev)
    db.flush()
    v = Video(id=vid, arena_id=arena.id, court_id=court.id, event_id=ev.id, code=code or vid[:8].upper(), title=title, storage_key=key,
              thumb_key=thumb, bytes=src.stat().st_size, duration=info.duration, width=info.width, height=info.height,
              video_codec=info.video_codec, has_audio=info.has_audio, visibility=vis, captured_at=at)
    db.add(v)
    db.flush()
    return v


@pytest.fixture
def world(media):
    db = SessionLocal()
    w = World()
    plan = Plan(name="Teste", max_courts=3, max_storage_mb=1024, max_exports_per_user_day=50)
    db.add(plan)
    ph = hash_password(PASSWORD)
    mk = lambda n, e, **k: User(name=n, email=e, password_hash=ph, **k)  # noqa: E731
    w.users = {
        "root": mk("Root", "root@t.test", global_role="global_admin"),
        "a_admin": mk("A Admin", "a_admin@t.test"), "a_op": mk("A Op", "a_op@t.test"), "a_user": mk("A User", "a_user@t.test"),
        "a_user2": mk("A User2", "a_user2@t.test"),
        "b_admin": mk("B Admin", "b_admin@t.test"), "b_user": mk("B User", "b_user@t.test"), "nobody": mk("Nobody", "nobody@t.test"),
    }
    db.add_all(w.users.values())
    db.flush()
    w.emails = {k: u.email for k, u in w.users.items()}
    w.uid = {k: u.id for k, u in w.users.items()}
    w.plan_id = plan.id
    for tag in ("A", "B"):
        ar = Arena(slug=f"arena-{tag.lower()}", name=f"Arena {tag}", plan_id=plan.id)
        db.add(ar)
        db.flush()
        c1 = Court(arena_id=ar.id, label=f"{tag} Quadra 1", number=1, local_court_key=f"{tag}_01")
        c2 = Court(arena_id=ar.id, label=f"{tag} Quadra 2", number=2, local_court_key=f"{tag}_02")
        db.add_all([c1, c2])
        db.flush()
        t = tag.lower()
        db.add(Membership(user_id=w.users[f"{t}_admin"].id, arena_id=ar.id, role="arena_admin"))
        db.add(Membership(user_id=w.users[f"{t}_user"].id, arena_id=ar.id, role="user"))
        if tag == "A":
            db.add(Membership(user_id=w.users["a_op"].id, arena_id=ar.id, role="arena_operator"))
            db.add(Membership(user_id=w.users["a_user2"].id, arena_id=ar.id, role="user"))
        # música
        tracks = {}
        for name, kw in {"ok": {}, "expired": {"license_valid_until": utcnow() - timedelta(days=1)}, "norights": {"rights_declared": False},
                         "disabled": {"status": "disabled"}, "noexport": {"allowed_use": "play"}}.items():
            tid = new_id()
            key = storage.make_key(ar.id, "music", f"{tid}.mp3")
            shutil.copy(media["tone"], storage.ensure_parent(key))
            base = dict(id=tid, arena_id=ar.id, asset_key=key, title=f"{tag}-{name}", artist="Teste", duration=2, license_info="licença de teste 123456",
                        rights_declared=True, allowed_use="play,export,social", status="enabled")
            db.add(MusicTrack(**{**base, **kw}))
            tracks[name] = tid
        # patrocinador
        sid = new_id()
        skey = storage.make_key(ar.id, "logos", f"{sid}.png")
        shutil.copy(media["logo"], storage.ensure_parent(skey))
        db.add(Sponsor(id=sid, arena_id=ar.id, name=f"Spons {tag}", asset_key=skey, logo_width=400, logo_height=160))
        db.flush()
        camp = SponsorCampaign(arena_id=ar.id, sponsor_id=sid, position="top-left", size_pct=20, opacity=1.0)
        db.add(camp)
        tok = f"rag_test_token_{tag}"
        db.add(ArenaAgent(arena_id=ar.id, label="ag", token_hash=hash_agent_token(tok)))
        v_pub = _add_video(db, ar, c1, media["audio"], "arena", 5, code=f"PUB{tag}0001", title="pub")
        v_priv = _add_video(db, ar, c1, media["audio"], "private", 6, code=f"PRIV{tag}001")
        v_mute = _add_video(db, ar, c2, media["mute"], "arena", 7, code=f"MUTE{tag}001")
        setattr(w, tag, type("Ar", (), dict(id=ar.id, c1=c1.id, c2=c2.id, tracks=tracks, sponsor=sid, campaign=camp.id, token=tok,
                                            pub=v_pub.id, priv=v_priv.id, mute=v_mute.id, priv_code=v_priv.code, pub_code=v_pub.code)))
    db.commit()
    db.close()
    return w


def login(client, email, app="user", password=PASSWORD):
    r = client.post(f"/api/auth/{app}/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def platform_login(client, email):
    from app.db import SessionLocal as S
    from app.models import User as U
    from app.security import _totp_code, totp_now_step
    r = client.post("/api/auth/platform/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    h = {"Authorization": f"Bearer {r.json()['mfaToken']}"}
    if r.json()["mfaStep"] == "enroll":
        client.post("/api/auth/platform/mfa/setup", headers=h)
    db = S()
    u = db.query(U).filter_by(email=email).first()
    secret, last = u.mfa_secret, u.mfa_last_step
    db.close()
    step = max(totp_now_step(), last + 1)
    r = client.post("/api/auth/platform/mfa/verify", headers=h, json={"code": _totp_code(secret, step)})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def H(client, world):
    """Cabeçalhos autenticados por persona (login separado por aplicação)."""
    cache = {}

    def get(who: str):
        if who not in cache:
            if who == "root":
                cache[who] = platform_login(client, world.emails["root"])
            elif who.endswith("_staff") or who in ("a_admin_arena", "a_op_arena", "b_admin_arena"):
                cache[who] = login(client, world.emails[who.replace("_arena", "")], "arena")
            else:
                cache[who] = login(client, world.emails[who], "user")
        return cache[who]
    return get
