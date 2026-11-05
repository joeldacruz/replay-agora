"""Ingestão do motor local (arena-agent). Credencial por arena; toda escrita é limitada à arena do token."""
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import audit, mediainfo, storage
from ..config import settings
from ..db import get_db, new_id, utcnow
from ..models import Arena, ArenaAgent, Court, Plan, ReplayEvent, Video
from ..agent_security import authenticate
from ..uploads import ext_of, to_temp, validate_video
from .arena import storage_used

router = APIRouter(prefix="/api/ingest", tags=["ingest"])
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def agent_auth(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> ArenaAgent:
    """Autenticação do contrato LEGADO (sem corpo/assinatura: uploads multipart grandes). Respeita rotação e expiração."""
    ag, _ = authenticate(db, authorization)
    ag.last_used_at = utcnow()
    return ag


def _court(db: Session, ag: ArenaAgent, key: str) -> Court:
    ct = db.query(Court).filter_by(arena_id=ag.arena_id, local_court_key=key).first()  # sempre escopado à arena do token
    if not ct:
        raise HTTPException(404, "quadra desconhecida para esta arena")
    if ct.status == "inactive":
        raise HTTPException(409, "quadra inativa")
    return ct


def _to_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


class EventIn(BaseModel):
    courtKey: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,60}$")  # ex.: REPLAY_01
    idempotencyKey: str = Field(min_length=8, max_length=120)
    capturedAt: datetime
    preSeconds: int = Field(15, ge=0, le=120)
    postSeconds: int = Field(5, ge=0, le=60)
    source: str = Field("arduino", max_length=30)


def _event_out(e: ReplayEvent, duplicate: bool) -> dict:
    return {"eventId": e.id, "status": e.status, "duplicate": duplicate, "duplicateOf": e.duplicate_of,
            "courtId": e.court_id, "preSeconds": e.pre_seconds, "postSeconds": e.post_seconds}


@router.post("/events")
def ingest_event(body: EventIn, ag: ArenaAgent = Depends(agent_auth), db: Session = Depends(get_db)):
    ct = _court(db, ag, body.courtKey)
    existing = db.query(ReplayEvent).filter_by(arena_id=ag.arena_id, idempotency_key=body.idempotencyKey).first()
    if existing:  # reenvio/retry com a mesma chave
        db.commit()
        return _event_out(existing, True)
    at = _to_utc(body.capturedAt)
    win = timedelta(seconds=settings.event_dedup_seconds)
    near = (db.query(ReplayEvent).filter(ReplayEvent.court_id == ct.id, ReplayEvent.status != "duplicate",
                                         ReplayEvent.captured_at >= at - win, ReplayEvent.captured_at <= at + win)
            .order_by(ReplayEvent.captured_at).first())
    ev = ReplayEvent(arena_id=ag.arena_id, court_id=ct.id, captured_at=at, source=body.source,
                     idempotency_key=body.idempotencyKey, pre_seconds=body.preSeconds, post_seconds=body.postSeconds,
                     status="duplicate" if near else "pending", duplicate_of=near.id if near else None)
    db.add(ev)
    ct.capture_status = "ok"
    db.commit()
    return _event_out(ev, bool(near))


def _code(db: Session) -> str:
    for _ in range(20):
        c = "".join(secrets.choice(_ALPHABET) for _ in range(8))
        if not db.query(Video.id).filter_by(code=c).first():
            return c
    raise HTTPException(500, "não foi possível gerar código")


@router.post("/videos", status_code=201)
async def ingest_video(file: UploadFile = File(...), idempotencyKey: str = Form(..., min_length=8, max_length=120),
                       courtKey: str = Form(..., pattern=r"^[A-Za-z0-9_\-]{1,60}$"), capturedAt: datetime | None = Form(None),
                       ag: ArenaAgent = Depends(agent_auth), db: Session = Depends(get_db)):
    if not settings.allow_agent_video_upload:
        raise HTTPException(410, "envio de vídeo à central desativado: o MP4 permanece na arena; use /api/agent/v1/sync/replays")
    ct = _court(db, ag, courtKey)
    ev = db.query(ReplayEvent).filter_by(arena_id=ag.arena_id, idempotency_key=idempotencyKey).first()
    if ev and ev.court_id != ct.id:
        raise HTTPException(409, "evento pertence a outra quadra")
    if ev and ev.status == "duplicate":
        raise HTTPException(409, "evento descartado como duplicado")
    if ev:
        done = db.query(Video).filter_by(event_id=ev.id).first()
        if done:  # reenvio idempotente: não cria outro replay
            db.commit()
            return {"videoId": done.id, "code": done.code, "duplicate": True}
    tmp = await to_temp(file, settings.max_video_bytes, ".mp4")
    try:
        info = validate_video(tmp, ext_of(file.filename))
        arena = db.get(Arena, ag.arena_id)
        plan = db.get(Plan, arena.plan_id) if arena.plan_id else None
        size = tmp.stat().st_size
        if plan and storage_used(db, arena.id) + size > plan.max_storage_mb * 1024 * 1024:
            raise HTTPException(413, "cota de armazenamento da arena excedida")
        if not ev:
            at = _to_utc(capturedAt) if capturedAt else utcnow()
            ev = ReplayEvent(arena_id=arena.id, court_id=ct.id, captured_at=at, source="agent-upload", idempotency_key=idempotencyKey)
            db.add(ev)
            db.flush()
        vid = new_id()
        key = storage.make_key(arena.id, "videos", f"{vid}.mp4")
        storage.move_into(tmp, key)
    finally:
        tmp.unlink(missing_ok=True)
    thumb_key = storage.make_key(arena.id, "thumbs", f"{vid}.jpg")
    ok = mediainfo.make_thumbnail(storage.path_for(key), storage.ensure_parent(thumb_key), min(1.0, info.duration / 2))
    v = Video(id=vid, arena_id=arena.id, court_id=ct.id, event_id=ev.id, code=_code(db), storage_key=key,
              thumb_key=thumb_key if ok else None, bytes=size, duration=info.duration, width=info.width, height=info.height,
              video_codec=info.video_codec, has_audio=info.has_audio, visibility="private", processing_status="ready",
              captured_at=ev.captured_at)
    ev.status = "completed"
    db.add(v)
    audit.record(db, None, arena.id, "ingest.video", "video", vid, {"court": ct.local_court_key, "agent": ag.id})
    db.commit()
    return {"videoId": v.id, "code": v.code, "duplicate": False}


class Heartbeat(BaseModel):
    version: str | None = Field(None, max_length=40)
    pendingSync: int = Field(0, ge=0, le=1_000_000)


@router.post("/heartbeat")
def heartbeat(body: Heartbeat, ag: ArenaAgent = Depends(agent_auth), db: Session = Depends(get_db)):
    ag.agent_version, ag.pending_sync = body.version, body.pendingSync
    ag.last_heartbeat_at = utcnow()
    db.commit()
    return {"ok": True}
