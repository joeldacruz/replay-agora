"""Portal DEV — replays por arena: lista, moderação excepcional e regularização segura de replays ainda privados."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import audit, replaypolicy
from ..db import get_db
from ..deps import Auth, platform_auth
from ..models import Arena, Court, MusicTrack, Video
from .arena import remove_video
from .serializers import iso

router = APIRouter(prefix="/api/platform/arenas/{arena_id}", tags=["portal-dev"])


def _arena(db: Session, arena_id: str) -> Arena:
    ar = db.get(Arena, arena_id)
    if not ar:
        raise HTTPException(404, "arena não encontrada")
    return ar


def _row(v: Video, court: Court | None, track: MusicTrack | None) -> dict:
    return {"id": v.id, "code": v.code, "title": v.title, "court": court.label if court else None, "capturedAt": iso(v.captured_at),
            "durationS": round(v.duration, 1), "visibility": v.visibility, "moderationState": v.moderation_state,
            "moderationReason": v.moderation_reason, "publishedAt": iso(v.published_at), "deleted": v.deleted_at is not None,
            "mediaState": v.media_state, "storageLocation": v.storage_location, "valid": replaypolicy.is_valid(v),
            "music": {"id": track.id, "title": track.title} if track else None, "musicReason": v.music_reason}


@router.get("/replays")
def list_replays(arena_id: str, state: str | None = Query(None, pattern=r"^(published|pending|moderated|removed)$"),
                 limit: int = Query(100, ge=1, le=300), a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    _arena(db, arena_id)
    q = db.query(Video, Court).join(Court, Court.id == Video.court_id).filter(Video.arena_id == arena_id)
    if state == "published":
        q = q.filter(Video.visibility == "arena", Video.deleted_at.is_(None))
    elif state == "pending":
        q = q.filter(Video.visibility == "private", Video.moderation_state == "none", Video.deleted_at.is_(None))
    elif state == "moderated":
        q = q.filter(Video.moderation_state != "none", Video.deleted_at.is_(None))
    elif state == "removed":
        q = q.filter(Video.deleted_at.is_not(None))
    out = []
    for v, ct in q.order_by(Video.captured_at.desc()).limit(limit):
        out.append(_row(v, ct, db.get(MusicTrack, v.music_id) if v.music_id else None))
    return out


class Moderate(BaseModel):
    action: str = Field(pattern=r"^(hide|block|restore|remove)$")
    reason: str = Field("", max_length=200)


@router.post("/replays/{video_id}/moderate")
def moderate(arena_id: str, video_id: str, body: Moderate, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    """Moderação excepcional. hide/block/remove exigem motivo e impedem qualquer republicação automática."""
    _arena(db, arena_id)
    v = db.get(Video, video_id)
    if not v or v.arena_id != arena_id:
        raise HTTPException(404, "replay não encontrado nesta arena")
    if body.action != "restore" and len(body.reason.strip()) < 3:
        raise HTTPException(422, "informe o motivo da moderação")
    if v.deleted_at is not None:
        raise HTTPException(409, "replay já removido")
    if body.action == "remove":
        remove_video(db, v, a.user.id, body.reason.strip())
    else:
        replaypolicy.moderate(db, v, body.action, a.user.id, body.reason.strip())
        audit.record(db, a.user.id, arena_id, f"video.moderate_{body.action}", "video", v.id, {"reason": body.reason.strip()})
    db.commit()
    return _row(v, db.get(Court, v.court_id), db.get(MusicTrack, v.music_id) if v.music_id else None)


class PublishPending(BaseModel):
    dryRun: bool = True


@router.post("/replays/publish-pending")
def publish_pending(arena_id: str, body: PublishPending, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    """Regulariza replays sincronizados como 'private' ANTES da regra automática: publica apenas os válidos DESTA arena que nunca
    foram publicados, moderados ou removidos, em quadras sem retenção administrativa. dryRun (padrão) só lista."""
    ar = _arena(db, arena_id)
    cand = (db.query(Video, Court).join(Court, Court.id == Video.court_id)
            .filter(Video.arena_id == arena_id, Video.visibility == "private", Video.moderation_state == "none",
                    Video.deleted_at.is_(None), Video.published_at.is_(None)).order_by(Video.captured_at).all())
    eligible, skipped = [], []
    for v, ct in cand:
        ok = ar.status == "active" and ct.visibility == "arena" and replaypolicy.is_valid(v)
        (eligible if ok else skipped).append((v, ct))
    if not body.dryRun:
        for v, ct in eligible:
            replaypolicy.assign_music(db, v, ar)
            replaypolicy.auto_publish(db, v, ar, ct)
        audit.record(db, a.user.id, arena_id, "video.regularize", "arena", arena_id, {"published": [v.id for v, _ in eligible]})
        db.commit()
    return {"dryRun": body.dryRun, "published": [] if body.dryRun else [v.id for v, _ in eligible],
            "eligible": [{"id": v.id, "code": v.code, "court": ct.label} for v, ct in eligible],
            "skipped": [{"id": v.id, "code": v.code, "reason": "não está pronto/válido ou quadra em retenção"} for v, _ in skipped]}
