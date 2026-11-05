"""Seeds fictícios: 2 arenas × 2 quadras, usuários de exemplo, patrocinadores, músicas sintéticas e replays.
Senhas aleatórias (impressas uma vez) ou SEED_PASSWORD. Nenhum dado de pessoa real."""
import os
import secrets
import shutil
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from . import mediainfo, samples, storage
from .db import SessionLocal, new_id, utcnow
from .models import (Arena, ArenaAgent, Court, Membership, MusicTrack, Plan, ReplayEvent, Sponsor, SponsorCampaign, User, Video)
from .security import hash_agent_token, hash_password, new_agent_token

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _video(db, arena, court, src: Path, minutes_ago: int, visibility: str, title: str = ""):
    info = mediainfo.video_info(src)
    vid = new_id()
    key = storage.make_key(arena.id, "videos", f"{vid}.mp4")
    shutil.copy(src, storage.ensure_parent(key))
    thumb = storage.make_key(arena.id, "thumbs", f"{vid}.jpg")
    ok = mediainfo.make_thumbnail(storage.path_for(key), storage.ensure_parent(thumb), 1.0)
    at = utcnow() - timedelta(minutes=minutes_ago)
    ev = ReplayEvent(arena_id=arena.id, court_id=court.id, captured_at=at, source="seed", idempotency_key=f"seed-{vid}", status="completed")
    db.add(ev)
    db.flush()
    db.add(Video(id=vid, arena_id=arena.id, court_id=court.id, event_id=ev.id, code="".join(secrets.choice(ALPHABET) for _ in range(8)),
                 title=title, storage_key=key, thumb_key=thumb if ok else None, bytes=src.stat().st_size, duration=info.duration,
                 width=info.width, height=info.height, video_codec=info.video_codec, has_audio=info.has_audio,
                 visibility=visibility, captured_at=at))


def run(password: str | None = None) -> dict:
    password = password or os.environ.get("SEED_PASSWORD") or secrets.token_urlsafe(12)
    out = {"password": password, "agentTokens": {}, "users": {}}
    db = SessionLocal()
    try:
        if db.query(Arena).filter_by(slug="arena-alfa").first():
            raise SystemExit("seed já aplicado (arena-alfa existe). Apague o banco de desenvolvimento para recriar.")
        tmp = Path(tempfile.mkdtemp())
        v_audio = samples.make_video(tmp / "com_audio.mp4", 4, True)
        v_mute = samples.make_video(tmp / "sem_audio.mp4", 4, False)
        plan = Plan(name="Básico (dev)", max_courts=2, max_storage_mb=2048)
        db.add(plan)
        ph = hash_password(password)
        users = {
            "global": User(name="Dev Plataforma (fictício)", email="dev@replayagora.example", password_hash=ph, global_role="global_admin"),
            "alfa_admin": User(name="Gestora Alfa (fictícia)", email="gestor.alfa@example.test", password_hash=ph),
            "alfa_op": User(name="Operador Alfa (fictício)", email="operador.alfa@example.test", password_hash=ph),
            "alfa_user": User(name="Atleta Alfa (fictício)", email="atleta.alfa@example.test", password_hash=ph),
            "beta_admin": User(name="Gestor Beta (fictício)", email="gestor.beta@example.test", password_hash=ph),
            "beta_user": User(name="Atleta Beta (fictício)", email="atleta.beta@example.test", password_hash=ph),
        }
        db.add_all(users.values())
        db.flush()
        out["users"] = {k: u.email for k, u in users.items()}
        spec = {
            "alfa": ("arena-alfa", "Arena Alfa (fictícia)", "#0b6bcb", [("Quadra 01", 1, "REPLAY_01", "D2"), ("Quadra 02", 2, "REPLAY_02", "A2")]),
            "beta": ("arena-beta", "Arena Beta (fictícia)", "#c2410c", [("Quadra A", 1, "BETA_01", None), ("Quadra B", 2, "BETA_02", None)]),
        }
        colors = {"alfa": [(220, 38, 38), (22, 163, 74)], "beta": [(124, 58, 237), (234, 179, 8)]}
        for k, (slug, name, color, courts) in spec.items():
            ar = Arena(slug=slug, name=name, brand_color=color, plan_id=plan.id)
            db.add(ar)
            db.flush()
            db.add(Membership(user_id=users[f"{k}_admin"].id, arena_id=ar.id, role="arena_admin"))
            db.add(Membership(user_id=users[f"{k}_user"].id, arena_id=ar.id, role="user"))
            if k == "alfa":
                db.add(Membership(user_id=users["alfa_op"].id, arena_id=ar.id, role="arena_operator"))
            cs = []
            for label, n, key, btn in courts:
                c = Court(arena_id=ar.id, label=label, number=n, local_court_key=key, button_ref=btn, camera_ref=f"camera-{n:02d} (simulada)")
                db.add(c)
                cs.append(c)
            db.flush()
            for i, c in enumerate(cs):
                _video(db, ar, c, v_audio, 10 + i * 5, "arena", f"Jogada {i + 1}")
                _video(db, ar, c, v_mute, 40 + i * 5, "private", "Sem áudio original (pendente de revisão)")
            for i, (nm, col) in enumerate(zip(("Patrocinador Um", "Patrocinador Dois"), colors[k])):
                lp = samples.make_logo(tmp / f"{k}{i}.png", col, nm.split()[-1])
                sid = new_id()
                key = storage.make_key(ar.id, "logos", f"{sid}.png")
                shutil.copy(lp, storage.ensure_parent(key))
                db.add(Sponsor(id=sid, arena_id=ar.id, name=f"{nm} ({k.title()})", website="https://example.test", asset_key=key, logo_width=400, logo_height=160))
                db.flush()
                db.add(SponsorCampaign(arena_id=ar.id, sponsor_id=sid, court_id=None if i == 0 else cs[0].id,
                                       position=("top-left", "bottom-right")[i], priority=10 + i, size_pct=18))
            first = None
            for i, (freq, title) in enumerate(((660, "Tom sintético A"), (880, "Tom sintético B"))):
                mid = new_id()
                key = storage.make_key(ar.id, "music", f"{mid}.mp3")
                samples.make_tone(storage.ensure_parent(key), freq, 8)
                db.add(MusicTrack(id=mid, arena_id=ar.id, asset_key=key, title=title, artist="Replay Agora (gerado por FFmpeg)",
                                  duration=8, license_info="Áudio sintético gerado pelo projeto (senoide); sem direitos de terceiros; uso livre.",
                                  rights_declared=True, allowed_use="play,export,social", position=i))
                first = first or mid
            ar.audio_default_music_id = first
            tok = new_agent_token()
            db.add(ArenaAgent(arena_id=ar.id, label=f"agente-simulado-{k}", token_hash=hash_agent_token(tok)))
            out["agentTokens"][slug] = tok
        db.commit()
        shutil.rmtree(tmp, ignore_errors=True)
        return out
    finally:
        db.close()


if __name__ == "__main__":
    r = run()
    print("Seed aplicado. Senha de desenvolvimento (todas as contas):", r["password"])
    for k, e in r["users"].items():
        print(f"  {k:11s} {e}")
    print("Tokens de agente (somente dev; exibidos uma vez):")
    for s, t in r["agentTokens"].items():
        print(f"  {s}: {t}")
    print("O portal exige MFA: no primeiro login do 'global' o app mostra o segredo TOTP para cadastro.", file=sys.stderr)
