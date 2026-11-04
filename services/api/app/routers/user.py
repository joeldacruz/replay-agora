"""API do App do Usuário (atleta). Nenhum endpoint administrativo aqui."""
import json
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from .. import audit, policy, storage
from ..config import settings
from ..db import get_db, utcnow
from ..deps import Auth, user_auth, video_visible_to
from ..models import (AUDIO_MODES, Arena, Court, ExportJob, Membership, MusicTrack, Plan, ShareLink, Video,
                      VideoAccess)
from ..security import hash_agent_token
from .media import sign
from .serializers import export_out, iso, video_out

router = APIRouter(prefix="/api", tags=["usuario"])


def _member_arenas(db: Session, user_id: str) -> list[str]:
    return [r[0] for r in db.query(Membership.arena_id).join(Arena, Arena.id == Membership.arena_id).filter(
        Membership.user_id == user_id, Membership.status == "active", Arena.status == "active")]


def _get_visible(db: Session, a: Auth, video_id: str) -> Video:
    v = db.get(Video, video_id)
    if not v or not video_visible_to(db, a.user, v):
        raise HTTPException(404, "replay não encontrado")
    return v


# ---------- arenas / quadras ----------
@router.get("/arenas")
def my_arenas(a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    rows = db.query(Arena).filter(Arena.id.in_(_member_arenas(db, a.user.id))).order_by(Arena.name).all()
    return [{"id": r.id, "slug": r.slug, "name": r.name, "brandColor": r.brand_color} for r in rows]


@router.get("/arenas/directory")
def directory(a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    rows = db.query(Arena).filter_by(status="active", allow_self_join=True).order_by(Arena.name).all()
    mine = set(_member_arenas(db, a.user.id))
    return [{"id": r.id, "slug": r.slug, "name": r.name, "joined": r.id in mine} for r in rows]


@router.post("/arenas/{arena_id}/join", status_code=201)
def join(arena_id: str, a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    ar = db.get(Arena, arena_id)
    if not ar or ar.status != "active" or not ar.allow_self_join:
        raise HTTPException(404, "arena não encontrada")
    if not db.query(Membership).filter_by(user_id=a.user.id, arena_id=arena_id).first():
        db.add(Membership(user_id=a.user.id, arena_id=arena_id, role="user"))
        audit.record(db, a.user.id, arena_id, "member.self_join", "membership", None)
        db.commit()
    return {"ok": True}


@router.get("/arenas/{arena_id}/courts")
def courts(arena_id: str, a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    if arena_id not in _member_arenas(db, a.user.id):
        raise HTTPException(404, "arena não encontrada")
    rows = db.query(Court).filter_by(arena_id=arena_id).filter(Court.status != "inactive").order_by(Court.number).all()
    return [{"id": c.id, "label": c.label, "number": c.number} for c in rows]


# ---------- catálogo ----------
def _visible_filter(db: Session, user_id: str):
    now = utcnow()
    granted = db.query(VideoAccess.video_id).filter(
        VideoAccess.user_id == user_id, or_(VideoAccess.expires_at.is_(None), VideoAccess.expires_at > now))
    active = db.query(Arena.id).filter(Arena.status == "active")
    return and_(Video.deleted_at.is_(None), Video.arena_id.in_(active),
                or_(Video.id.in_(granted),
                    and_(Video.visibility == "arena", Video.arena_id.in_(_member_arenas(db, user_id)))))


@router.get("/videos")
def list_videos(arenaId: str | None = None, courtId: str | None = None, date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
                fromHour: int | None = Query(None, ge=0, le=23), toHour: int | None = Query(None, ge=0, le=23),
                q: str | None = Query(None, max_length=40), cursor: str | None = None,
                limit: int = Query(20, ge=1, le=50), a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    qs = db.query(Video, Court).join(Court, Court.id == Video.court_id).filter(_visible_filter(db, a.user.id))
    if arenaId:
        qs = qs.filter(Video.arena_id == arenaId)
    if courtId:
        qs = qs.filter(Video.court_id == courtId)
    if q:
        qs = qs.filter(or_(Video.code.ilike(f"%{q.strip()}%"), Video.title.ilike(f"%{q.strip()}%")))
    if date:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        # dia local da arena -> intervalo UTC (usa o fuso da primeira arena filtrada; padrão São Paulo)
        tzname = "America/Sao_Paulo"
        if arenaId and (ar := db.get(Arena, arenaId)):
            tzname = ar.timezone
        tz = ZoneInfo(tzname)
        start = datetime.fromisoformat(date).replace(tzinfo=tz)
        h0, h1 = (fromHour or 0), (toHour if toHour is not None else 23)
        lo = (start + timedelta(hours=h0)).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        hi = (start + timedelta(hours=h1 + 1)).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        qs = qs.filter(Video.captured_at >= lo, Video.captured_at < hi)
    if cursor:
        try:
            ts, vid = cursor.split("|")
            from datetime import datetime
            c = datetime.fromisoformat(ts)
        except ValueError:
            raise HTTPException(422, "cursor inválido")
        qs = qs.filter(or_(Video.captured_at < c, and_(Video.captured_at == c, Video.id < vid)))
    rows = qs.order_by(Video.captured_at.desc(), Video.id.desc()).limit(limit + 1).all()
    nxt = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1][0]
        nxt = f"{last.captured_at.isoformat()}|{last.id}"
    return {"items": [video_out(v, c, a.user.id) for v, c in rows], "nextCursor": nxt}


class Claim(BaseModel):
    code: str = Field(min_length=4, max_length=12)


@router.post("/videos/claim")
def claim(body: Claim, a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    """Código/QR impresso na quadra concede acesso ao replay (e vínculo com a arena)."""
    v = db.query(Video).filter_by(code=body.code.strip().upper()).first()
    if not v or v.deleted_at or v.processing_status != "ready" or db.get(Arena, v.arena_id).status != "active":
        raise HTTPException(404, "código não encontrado")
    if not db.query(VideoAccess).filter_by(user_id=a.user.id, video_id=v.id).first():
        db.add(VideoAccess(user_id=a.user.id, video_id=v.id))
    if not db.query(Membership).filter_by(user_id=a.user.id, arena_id=v.arena_id).first():
        db.add(Membership(user_id=a.user.id, arena_id=v.arena_id, role="user"))
    audit.record(db, a.user.id, v.arena_id, "video.claim", "video", v.id)
    db.commit()
    return {"videoId": v.id}


@router.get("/videos/{video_id}")
def get_video(video_id: str, a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    v = _get_visible(db, a, video_id)
    out = video_out(v, db.get(Court, v.court_id), a.user.id)
    out["sponsors"] = [o["name"] for o in policy.select_overlays(db, v)]
    return out


@router.get("/videos/{video_id}/play")
def play(video_id: str, a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    v = _get_visible(db, a, video_id)
    if v.processing_status != "ready":
        raise HTTPException(409, "replay ainda em processamento")
    return {"url": sign("video", v.id, a.user.id), "expiresIn": settings.signed_url_ttl_s}


@router.post("/videos/{video_id}/download-link")
def download_link(video_id: str, a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    v = _get_visible(db, a, video_id)
    return {"url": sign("video", v.id, a.user.id, "attachment"), "expiresIn": settings.signed_url_ttl_s}


@router.get("/videos/{video_id}/download")
def download(video_id: str, a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    v = _get_visible(db, a, video_id)
    p = storage.path_for(v.storage_key)
    if not p.exists():
        raise HTTPException(404, "arquivo indisponível")
    audit.record(db, a.user.id, v.arena_id, "video.download", "video", v.id)
    db.commit()
    r = FileResponse(p, media_type="video/mp4", filename=f"replay-{v.code}.mp4", content_disposition_type="attachment")
    r.headers["Cache-Control"] = "private, no-store"
    return r


# ---------- compartilhamento ----------
class ShareReq(BaseModel):
    hours: int = Field(24, ge=1, le=24 * 7)


@router.post("/videos/{video_id}/shares", status_code=201)
def create_share(video_id: str, body: ShareReq, a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    v = _get_visible(db, a, video_id)
    token = secrets.token_urlsafe(24)
    s = ShareLink(video_id=v.id, created_by=a.user.id, token_hash=hash_agent_token(token),
                  expires_at=utcnow() + timedelta(hours=body.hours))
    db.add(s)
    db.flush()
    audit.record(db, a.user.id, v.arena_id, "video.share_create", "share", s.id, {"hours": body.hours})
    db.commit()
    return {"id": s.id, "token": token, "path": f"#/s/{token}", "expiresAt": iso(s.expires_at)}


@router.get("/videos/{video_id}/shares")
def list_shares(video_id: str, a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    v = _get_visible(db, a, video_id)
    rows = db.query(ShareLink).filter_by(video_id=v.id, created_by=a.user.id).order_by(ShareLink.created_at.desc()).all()
    return [{"id": s.id, "expiresAt": iso(s.expires_at), "revoked": s.revoked_at is not None} for s in rows]


@router.delete("/videos/{video_id}/shares/{share_id}", status_code=204)
def revoke_share(video_id: str, share_id: str, a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    v = _get_visible(db, a, video_id)
    s = db.query(ShareLink).filter_by(id=share_id, video_id=v.id, created_by=a.user.id).first()
    if not s:
        raise HTTPException(404, "link não encontrado")
    s.revoked_at = utcnow()
    db.query(VideoAccess).filter_by(share_id=s.id).delete()
    audit.record(db, a.user.id, v.arena_id, "video.share_revoke", "share", s.id)
    db.commit()


class Redeem(BaseModel):
    token: str = Field(min_length=10, max_length=100)


@router.post("/shares/redeem")
def redeem(body: Redeem, a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    s = db.query(ShareLink).filter_by(token_hash=hash_agent_token(body.token)).first()
    if not s or s.revoked_at or s.expires_at < utcnow():
        raise HTTPException(404, "link inválido, expirado ou revogado")
    v = db.get(Video, s.video_id)
    if not v or v.deleted_at or db.get(Arena, v.arena_id).status != "active":
        raise HTTPException(404, "link inválido, expirado ou revogado")
    if not db.query(VideoAccess).filter_by(user_id=a.user.id, video_id=v.id).first():
        db.add(VideoAccess(user_id=a.user.id, video_id=v.id, share_id=s.id, expires_at=s.expires_at))
        audit.record(db, a.user.id, v.arena_id, "video.share_redeem", "share", s.id)
        db.commit()
    return {"videoId": v.id}


# ---------- exportação ----------
def export_options(db: Session, v: Video) -> dict:
    arena = db.get(Arena, v.arena_id)
    tracks = policy.usable_tracks(db, v.arena_id)
    modes = policy.allowed_modes(arena, v.has_audio, bool(tracks))
    return {"modes": modes, "hasOriginalAudio": v.has_audio, "defaultMode": arena.audio_default_mode if arena.audio_default_mode in modes else (modes[0] if modes else None),
            "defaultMusicId": arena.audio_default_music_id if any(t.id == arena.audio_default_music_id for t in tracks) else (tracks[0].id if tracks else None),
            "originalVolume": arena.audio_original_volume, "musicVolume": arena.audio_music_volume,
            "tracks": [{"id": t.id, "title": t.title, "artist": t.artist, "duration": round(t.duration, 1)} for t in tracks]}


@router.get("/videos/{video_id}/export-options")
def get_export_options(video_id: str, a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    return export_options(db, _get_visible(db, a, video_id))


class ExportReq(BaseModel):
    audioMode: str | None = None
    musicId: str | None = Field(None, max_length=32)
    originalVolume: float | None = Field(None, ge=0, le=1.5)
    musicVolume: float | None = Field(None, ge=0, le=1.5)
    applySponsors: bool = True


@router.post("/videos/{video_id}/exports")
def create_export(video_id: str, body: ExportReq, a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    v = _get_visible(db, a, video_id)
    if v.processing_status != "ready":
        raise HTTPException(409, "replay ainda em processamento")
    arena = db.get(Arena, v.arena_id)
    opts = export_options(db, v)
    mode = body.audioMode or opts["defaultMode"]
    if mode not in AUDIO_MODES:
        raise HTTPException(422, "modo de áudio inválido")
    if mode in ("original", "mix") and not v.has_audio:
        raise HTTPException(422, "este replay não possui áudio original")
    if mode not in opts["modes"]:
        raise HTTPException(422, "modo de áudio não disponível nesta arena")
    music_id = None
    if mode in ("music", "mix"):
        music_id = body.musicId or opts["defaultMusicId"]
        t = db.get(MusicTrack, music_id) if music_id else None
        # a faixa precisa ser da MESMA arena e estar licenciada/habilitada
        if not t or t.arena_id != v.arena_id or not policy.track_usable(t):
            raise HTTPException(422, "faixa musical indisponível ou sem licença válida")
    ov = body.originalVolume if body.originalVolume is not None else arena.audio_original_volume
    mv = body.musicVolume if body.musicVolume is not None else arena.audio_music_volume
    spec = policy.select_overlays(db, v) if body.applySponsors else []
    ph = policy.params_hash(v.id, mode, music_id, ov, mv, spec)

    same = db.query(ExportJob).filter(ExportJob.user_id == a.user.id, ExportJob.params_hash == ph,
                                      ExportJob.status.in_(("queued", "processing", "ready"))).first()
    if same and (same.status != "ready" or (same.output_key and storage.path_for(same.output_key).exists())):
        return export_out(same, a.user.id, v.code)

    plan = db.get(Plan, arena.plan_id) if arena.plan_id else None
    active = db.query(func.count(ExportJob.id)).filter(ExportJob.user_id == a.user.id,
                                                       ExportJob.status.in_(("queued", "processing"))).scalar()
    if active >= settings.max_active_exports_per_user:
        raise HTTPException(429, "muitas exportações em andamento; aguarde concluir")
    if plan:
        today = db.query(func.count(ExportJob.id)).filter(
            ExportJob.user_id == a.user.id, ExportJob.arena_id == arena.id,
            ExportJob.created_at >= utcnow() - timedelta(days=1)).scalar()
        if today >= plan.max_exports_per_user_day:
            raise HTTPException(429, "limite diário de exportações da arena atingido")

    job = ExportJob(arena_id=v.arena_id, user_id=a.user.id, video_id=v.id, audio_mode=mode, music_id=music_id,
                    original_volume=ov, music_volume=mv, apply_sponsors=body.applySponsors,
                    overlay_spec=json.dumps(spec), sponsor_template_version=policy.spec_version(spec), params_hash=ph)
    db.add(job)
    db.flush()
    audit.record(db, a.user.id, v.arena_id, "export.create", "export", job.id, {"mode": mode, "music_id": music_id})
    db.commit()
    return export_out(job, a.user.id, v.code)


@router.get("/exports")
def my_exports(a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    rows = (db.query(ExportJob, Video.code).join(Video, Video.id == ExportJob.video_id)
            .filter(ExportJob.user_id == a.user.id).order_by(ExportJob.created_at.desc()).limit(50).all())
    return [export_out(j, a.user.id, code) for j, code in rows]


def _own_export(db: Session, a: Auth, export_id: str) -> ExportJob:
    j = db.get(ExportJob, export_id)
    if not j or j.user_id != a.user.id:
        raise HTTPException(404, "exportação não encontrada")
    return j


@router.get("/exports/{export_id}")
def get_export(export_id: str, a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    j = _own_export(db, a, export_id)
    return export_out(j, a.user.id, db.get(Video, j.video_id).code)


@router.post("/exports/{export_id}/download-link")
def export_link(export_id: str, a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    j = _own_export(db, a, export_id)
    if j.status != "ready":
        raise HTTPException(409, "exportação ainda não está pronta")
    return {"url": sign("export", j.id, a.user.id, "attachment"), "expiresIn": settings.signed_url_ttl_s}


@router.get("/exports/{export_id}/download")
def export_download(export_id: str, a: Auth = Depends(user_auth), db: Session = Depends(get_db)):
    j = _own_export(db, a, export_id)
    v = db.get(Video, j.video_id)
    if j.status != "ready" or not j.output_key or not video_visible_to(db, a.user, v):
        raise HTTPException(409, "exportação indisponível")
    p = storage.path_for(j.output_key)
    if not p.exists():
        raise HTTPException(404, "arquivo indisponível")
    audit.record(db, a.user.id, j.arena_id, "export.download", "export", j.id)
    db.commit()
    r = FileResponse(p, media_type="video/mp4", filename=f"replay-{v.code}-{j.audio_mode}.mp4",
                     content_disposition_type="attachment")
    r.headers["Cache-Control"] = "private, no-store"
    return r
