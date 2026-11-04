"""API do App de Gestão da Arena. Todas as rotas exigem token do app 'arena' e contexto /arena/{arena_id}
validado no servidor (membership + papel). Nada aqui administra outras arenas ou a plataforma."""
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import audit, policy, storage
from ..config import settings
from ..db import get_db, new_id, utcnow
from ..deps import ArenaCtx, ArenaGate
from ..models import (AUDIO_MODES, POSITIONS, ROLES, Arena, ArenaAgent, AuditLog, Court, ExportJob, Membership,
                      MusicTrack, Plan, ReplayEvent, Sponsor, SponsorCampaign, User, Video)
from ..security import hash_agent_token, new_agent_token
from ..uploads import ext_of, process_logo, to_temp, validate_music
from .media import sign
from .serializers import iso, video_out

router = APIRouter(prefix="/api/arena/{arena_id}", tags=["arena"])
staff = ArenaGate("arena_admin", "arena_operator")
admin = ArenaGate("arena_admin")


def storage_used(db: Session, arena_id: str) -> int:
    v = db.query(func.coalesce(func.sum(Video.bytes), 0)).filter(Video.arena_id == arena_id, Video.deleted_at.is_(None)).scalar()
    e = db.query(func.coalesce(func.sum(ExportJob.output_bytes), 0)).filter(ExportJob.arena_id == arena_id).scalar()
    return int(v) + int(e)


def _arena_out(a: Arena, role: str) -> dict:
    return {"id": a.id, "slug": a.slug, "name": a.name, "status": a.status, "timezone": a.timezone,
            "brandColor": a.brand_color, "allowSelfJoin": a.allow_self_join, "myRole": role,
            "audio": {"defaultMode": a.audio_default_mode, "defaultMusicId": a.audio_default_music_id,
                      "originalVolume": a.audio_original_volume, "musicVolume": a.audio_music_volume,
                      "allowedModes": [m for m in a.audio_allowed_modes.split(",") if m]}}


@router.get("")
def get_arena(c: ArenaCtx = Depends(staff)):
    return _arena_out(c.arena, c.membership.role)


class ArenaPatch(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=120)
    brandColor: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    allowSelfJoin: bool | None = None
    defaultMode: str | None = None
    defaultMusicId: str | None = Field(None, max_length=32)
    originalVolume: float | None = Field(None, ge=0, le=1.5)
    musicVolume: float | None = Field(None, ge=0, le=1.5)
    allowedModes: list[str] | None = None


@router.patch("")
def patch_arena(body: ArenaPatch, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    a, ch = c.arena, body.model_dump(exclude_unset=True)
    if "allowedModes" in ch:
        if not ch["allowedModes"] or any(m not in AUDIO_MODES for m in ch["allowedModes"]):
            raise HTTPException(422, "modos de áudio inválidos")
        a.audio_allowed_modes = ",".join(m for m in AUDIO_MODES if m in ch["allowedModes"])
    if "defaultMode" in ch:
        if ch["defaultMode"] not in AUDIO_MODES:
            raise HTTPException(422, "modo padrão inválido")
        a.audio_default_mode = ch["defaultMode"]
    if "defaultMusicId" in ch:
        mid = ch["defaultMusicId"]
        if mid:
            t = db.get(MusicTrack, mid)
            if not t or t.arena_id != a.id:
                raise HTTPException(422, "faixa não pertence a esta arena")
        a.audio_default_music_id = mid or None
    if a.audio_default_mode not in a.audio_allowed_modes.split(","):
        raise HTTPException(422, "o modo padrão precisa estar entre os modos oferecidos")
    for k, attr in (("name", "name"), ("brandColor", "brand_color"), ("allowSelfJoin", "allow_self_join"),
                    ("originalVolume", "audio_original_volume"), ("musicVolume", "audio_music_volume")):
        if k in ch and ch[k] is not None:
            setattr(a, attr, ch[k])
    audit.record(db, c.user.id, a.id, "arena.update", "arena", a.id, ch)
    db.commit()
    return _arena_out(a, c.membership.role)


# ---------------- dashboard / saúde ----------------
@router.get("/dashboard")
def dashboard(c: ArenaCtx = Depends(staff), db: Session = Depends(get_db)):
    aid = c.arena.id
    since = utcnow() - timedelta(days=7)
    per_court = []
    for ct in db.query(Court).filter_by(arena_id=aid).order_by(Court.number):
        ev = db.query(func.count(ReplayEvent.id)).filter(ReplayEvent.court_id == ct.id, ReplayEvent.status != "duplicate",
                                                          ReplayEvent.created_at >= since).scalar()
        vids = db.query(func.count(Video.id)).filter(Video.court_id == ct.id, Video.deleted_at.is_(None)).scalar()
        last = db.query(func.max(ReplayEvent.captured_at)).filter(ReplayEvent.court_id == ct.id).scalar()
        per_court.append({"courtId": ct.id, "label": ct.label, "status": ct.status, "events7d": ev, "videos": vids,
                          "lastEventAt": iso(last)})
    plan = db.get(Plan, c.arena.plan_id) if c.arena.plan_id else None
    n = lambda *f: db.query(func.count(Video.id)).filter(Video.arena_id == aid, Video.deleted_at.is_(None), *f).scalar()  # noqa: E731
    agents = db.query(ArenaAgent).filter_by(arena_id=aid, active=True).all()
    last_seen = max([a.last_used_at for a in agents if a.last_used_at], default=None)
    return {
        "videos": n(), "published": n(Video.visibility == "arena"), "pendingReview": n(Video.visibility == "private"),
        "downloads": db.query(func.count(AuditLog.id)).filter(AuditLog.arena_id == aid, AuditLog.action.in_(("video.download", "export.download"))).scalar(),
        "exportsFailed": db.query(func.count(ExportJob.id)).filter_by(arena_id=aid, status="failed").scalar(),
        "exportsActive": db.query(func.count(ExportJob.id)).filter(ExportJob.arena_id == aid, ExportJob.status.in_(("queued", "processing"))).scalar(),
        "failedEvents": db.query(func.count(ReplayEvent.id)).filter_by(arena_id=aid, status="failed").scalar(),
        "storageBytes": storage_used(db, aid), "storageLimitMb": plan.max_storage_mb if plan else None,
        "courts": per_court, "agentLastSeenAt": iso(last_seen),
        "agentOnline": bool(last_seen and last_seen > utcnow() - timedelta(minutes=5)),
    }


# ---------------- quadras ----------------
class CourtIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    number: int = Field(1, ge=1, le=99)
    localCourtKey: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,60}$")
    cameraRef: str | None = Field(None, max_length=120)
    buttonRef: str | None = Field(None, max_length=20)
    retentionDays: int = Field(30, ge=1, le=3650)
    visibility: str = Field("arena", pattern=r"^(arena|private)$")
    status: str = Field("active", pattern=r"^(active|maintenance|inactive)$")


class CourtPatch(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=80)
    number: int | None = Field(None, ge=1, le=99)
    cameraRef: str | None = Field(None, max_length=120)
    buttonRef: str | None = Field(None, max_length=20)
    retentionDays: int | None = Field(None, ge=1, le=3650)
    visibility: str | None = Field(None, pattern=r"^(arena|private)$")
    status: str | None = Field(None, pattern=r"^(active|maintenance|inactive)$")


def _court_out(ct: Court) -> dict:
    return {"id": ct.id, "label": ct.label, "number": ct.number, "status": ct.status, "captureStatus": ct.capture_status,
            "localCourtKey": ct.local_court_key, "cameraRef": ct.camera_ref, "buttonRef": ct.button_ref,
            "retentionDays": ct.retention_days, "visibility": ct.visibility}


def _no_credentials(ref: str | None) -> None:
    if ref and ("://" in ref or "@" in ref):
        raise HTTPException(422, "cameraRef é só uma referência; não informe URL RTSP nem credenciais")


@router.get("/courts")
def list_courts(c: ArenaCtx = Depends(staff), db: Session = Depends(get_db)):
    return [_court_out(x) for x in db.query(Court).filter_by(arena_id=c.arena.id).order_by(Court.number)]


@router.post("/courts", status_code=201)
def create_court(body: CourtIn, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    _no_credentials(body.cameraRef)
    plan = db.get(Plan, c.arena.plan_id) if c.arena.plan_id else None
    count = db.query(func.count(Court.id)).filter_by(arena_id=c.arena.id).scalar()
    if plan and count >= plan.max_courts:
        raise HTTPException(409, f"limite de {plan.max_courts} quadras do plano atingido")
    if db.query(Court).filter_by(arena_id=c.arena.id, local_court_key=body.localCourtKey).first():
        raise HTTPException(409, "chave de quadra local já utilizada")
    ct = Court(arena_id=c.arena.id, label=body.label, number=body.number, local_court_key=body.localCourtKey,
               camera_ref=body.cameraRef, button_ref=body.buttonRef, retention_days=body.retentionDays,
               visibility=body.visibility, status=body.status)
    db.add(ct)
    db.flush()
    audit.record(db, c.user.id, c.arena.id, "court.create", "court", ct.id, body.model_dump())
    db.commit()
    return _court_out(ct)


def _court(db: Session, c: ArenaCtx, court_id: str) -> Court:
    ct = db.get(Court, court_id)
    if not ct or ct.arena_id != c.arena.id:
        raise HTTPException(404, "quadra não encontrada")
    return ct


@router.patch("/courts/{court_id}")
def patch_court(court_id: str, body: CourtPatch, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    ct = _court(db, c, court_id)
    ch = body.model_dump(exclude_unset=True)
    _no_credentials(ch.get("cameraRef"))
    for k, attr in (("label", "label"), ("number", "number"), ("cameraRef", "camera_ref"), ("buttonRef", "button_ref"),
                    ("retentionDays", "retention_days"), ("visibility", "visibility"), ("status", "status")):
        if k in ch:
            setattr(ct, attr, ch[k])
    audit.record(db, c.user.id, c.arena.id, "court.update", "court", ct.id, ch)
    db.commit()
    return _court_out(ct)


# ---------------- replays ----------------
@router.get("/videos")
def list_videos(courtId: str | None = None, visibility: str | None = Query(None, pattern=r"^(arena|private)$"),
                limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0),
                c: ArenaCtx = Depends(staff), db: Session = Depends(get_db)):
    q = db.query(Video, Court).join(Court, Court.id == Video.court_id).filter(Video.arena_id == c.arena.id, Video.deleted_at.is_(None))
    if courtId:
        q = q.filter(Video.court_id == courtId)
    if visibility:
        q = q.filter(Video.visibility == visibility)
    rows = q.order_by(Video.captured_at.desc(), Video.id.desc()).offset(offset).limit(limit).all()
    return [video_out(v, ct, c.user.id, admin=True) for v, ct in rows]


class VideoPatch(BaseModel):
    visibility: str | None = Field(None, pattern=r"^(arena|private)$")
    title: str | None = Field(None, max_length=160)


def _video(db: Session, c: ArenaCtx, video_id: str) -> Video:
    v = db.get(Video, video_id)
    if not v or v.arena_id != c.arena.id or v.deleted_at:
        raise HTTPException(404, "replay não encontrado")
    return v


@router.patch("/videos/{video_id}")
def patch_video(video_id: str, body: VideoPatch, c: ArenaCtx = Depends(staff), db: Session = Depends(get_db)):
    v = _video(db, c, video_id)
    ch = body.model_dump(exclude_unset=True)
    if "visibility" in ch:
        v.visibility = ch["visibility"]
    if "title" in ch:
        v.title = ch["title"] or ""
    audit.record(db, c.user.id, c.arena.id, "video.publish" if ch.get("visibility") == "arena" else "video.update", "video", v.id, ch)
    db.commit()
    return video_out(v, db.get(Court, v.court_id), c.user.id, admin=True)


@router.get("/videos/{video_id}/play")
def staff_play(video_id: str, c: ArenaCtx = Depends(staff), db: Session = Depends(get_db)):
    v = _video(db, c, video_id)
    return {"url": sign("video", v.id, c.user.id), "expiresIn": settings.signed_url_ttl_s}


@router.delete("/videos/{video_id}", status_code=204)
def delete_video(video_id: str, confirm: bool = Query(False), c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    if not confirm:
        raise HTTPException(422, "confirme a exclusão com ?confirm=true")
    v = _video(db, c, video_id)
    jobs = db.query(ExportJob).filter_by(video_id=v.id).all()
    for j in jobs:
        storage.delete(j.output_key)
    storage.delete(v.storage_key)
    storage.delete(v.thumb_key)
    v.deleted_at = utcnow()
    v.storage_key, v.thumb_key = "", None
    audit.record(db, c.user.id, c.arena.id, "video.delete", "video", v.id, {"code": v.code})
    db.query(ExportJob).filter_by(video_id=v.id).delete()
    db.commit()


@router.get("/exports")
def export_history(c: ArenaCtx = Depends(staff), db: Session = Depends(get_db)):
    rows = (db.query(ExportJob, Video.code, User.name).join(Video, Video.id == ExportJob.video_id)
            .join(User, User.id == ExportJob.user_id).filter(ExportJob.arena_id == c.arena.id)
            .order_by(ExportJob.created_at.desc()).limit(100).all())
    return [{"id": j.id, "videoCode": code, "user": name, "audioMode": j.audio_mode, "status": j.status,
             "error": j.error, "createdAt": iso(j.created_at), "sponsorVersion": j.sponsor_template_version}
            for j, code, name in rows]


# ---------------- patrocinadores ----------------
def _sponsor_out(s: Sponsor, uid: str) -> dict:
    return {"id": s.id, "name": s.name, "website": s.website, "status": s.status, "logoUrl": sign("logo", s.id, uid),
            "logoWidth": s.logo_width, "logoHeight": s.logo_height}


def _check_site(w: str | None) -> str | None:
    if w and not (w.startswith("https://") or w.startswith("http://")):
        raise HTTPException(422, "site deve começar com http:// ou https://")
    return w or None


@router.get("/sponsors")
def list_sponsors(c: ArenaCtx = Depends(staff), db: Session = Depends(get_db)):
    return [_sponsor_out(s, c.user.id) for s in db.query(Sponsor).filter_by(arena_id=c.arena.id).order_by(Sponsor.name)]


async def _save_logo(db, c: ArenaCtx, file: UploadFile, sponsor_id: str) -> tuple[str, int, int]:
    tmp = await to_temp(file, settings.max_logo_bytes, ".png")
    try:
        data, w, h = process_logo(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    key = storage.make_key(c.arena.id, "logos", f"{sponsor_id}-{new_id()[:8]}.png")
    storage.ensure_parent(key).write_bytes(data)
    return key, w, h


@router.post("/sponsors", status_code=201)
async def create_sponsor(name: str = Form(..., min_length=2, max_length=120), website: str | None = Form(None, max_length=300),
                         logo: UploadFile = File(...), c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    site = _check_site(website)
    sid = new_id()
    key, w, h = await _save_logo(db, c, logo, sid)
    s = Sponsor(id=sid, arena_id=c.arena.id, name=name.strip(), website=site, asset_key=key, logo_width=w, logo_height=h)
    db.add(s)
    audit.record(db, c.user.id, c.arena.id, "sponsor.create", "sponsor", sid, {"name": name, "website": site})
    db.commit()
    return _sponsor_out(s, c.user.id)


def _sponsor(db: Session, c: ArenaCtx, sponsor_id: str) -> Sponsor:
    s = db.get(Sponsor, sponsor_id)
    if not s or s.arena_id != c.arena.id:
        raise HTTPException(404, "patrocinador não encontrado")
    return s


@router.patch("/sponsors/{sponsor_id}")
async def patch_sponsor(sponsor_id: str, name: str | None = Form(None, min_length=2, max_length=120),
                        website: str | None = Form(None, max_length=300), status: str | None = Form(None, pattern=r"^(active|inactive)$"),
                        logo: UploadFile | None = File(None), c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    s = _sponsor(db, c, sponsor_id)
    ch = {}
    if name is not None:
        s.name, ch["name"] = name.strip(), name
    if website is not None:
        s.website, ch["website"] = _check_site(website), website
    if status is not None:
        s.status, ch["status"] = status, status
    if logo is not None and logo.filename:
        old = s.asset_key
        s.asset_key, s.logo_width, s.logo_height = await _save_logo(db, c, logo, s.id)
        storage.delete(old)
        ch["logo"] = "replaced"
    audit.record(db, c.user.id, c.arena.id, "sponsor.update", "sponsor", s.id, ch)
    db.commit()
    return _sponsor_out(s, c.user.id)


@router.delete("/sponsors/{sponsor_id}", status_code=204)
def delete_sponsor(sponsor_id: str, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    s = _sponsor(db, c, sponsor_id)
    storage.delete(s.asset_key)
    audit.record(db, c.user.id, c.arena.id, "sponsor.delete", "sponsor", s.id, {"name": s.name})
    db.delete(s)
    db.commit()


# ---------------- campanhas ----------------
class CampaignIn(BaseModel):
    sponsorId: str = Field(max_length=32)
    courtId: str | None = Field(None, max_length=32)
    startsOn: datetime | None = None
    endsOn: datetime | None = None
    position: str = "bottom-right"
    sizePct: int = Field(15, ge=5, le=40)
    opacity: float = Field(1.0, ge=0.1, le=1.0)
    durationS: float | None = Field(None, gt=0, le=600)
    priority: int = Field(100, ge=1, le=9999)
    active: bool = True


class CampaignPatch(BaseModel):
    courtId: str | None = Field(None, max_length=32)
    startsOn: datetime | None = None
    endsOn: datetime | None = None
    position: str | None = None
    sizePct: int | None = Field(None, ge=5, le=40)
    opacity: float | None = Field(None, ge=0.1, le=1.0)
    durationS: float | None = Field(None, gt=0, le=600)
    priority: int | None = Field(None, ge=1, le=9999)
    active: bool | None = None


def _camp_out(k: SponsorCampaign, s: Sponsor | None) -> dict:
    return {"id": k.id, "sponsorId": k.sponsor_id, "sponsorName": s.name if s else None, "courtId": k.court_id,
            "startsOn": k.starts_on.isoformat() if k.starts_on else None, "endsOn": k.ends_on.isoformat() if k.ends_on else None,
            "position": k.position, "sizePct": k.size_pct, "opacity": k.opacity, "durationS": k.duration_s,
            "priority": k.priority, "active": k.active, "template": k.template}


def _norm_dt(d: datetime | None) -> datetime | None:
    return d.replace(tzinfo=None) if d else None


def _validate_campaign(db: Session, c: ArenaCtx, sponsor_id: str | None, court_id: str | None, position: str | None,
                       starts: datetime | None, ends: datetime | None) -> None:
    if sponsor_id is not None:
        _sponsor(db, c, sponsor_id)  # patrocinador de outra arena => 404
    if court_id:
        _court(db, c, court_id)
    if position is not None and position not in POSITIONS:
        raise HTTPException(422, "posição inválida")
    if starts and ends and ends <= starts:
        raise HTTPException(422, "data final deve ser posterior à inicial")


@router.get("/campaigns")
def list_campaigns(c: ArenaCtx = Depends(staff), db: Session = Depends(get_db)):
    rows = (db.query(SponsorCampaign, Sponsor).join(Sponsor, Sponsor.id == SponsorCampaign.sponsor_id)
            .filter(SponsorCampaign.arena_id == c.arena.id).order_by(SponsorCampaign.priority).all())
    return [_camp_out(k, s) for k, s in rows]


@router.post("/campaigns", status_code=201)
def create_campaign(body: CampaignIn, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    st, en = _norm_dt(body.startsOn), _norm_dt(body.endsOn)
    _validate_campaign(db, c, body.sponsorId, body.courtId, body.position, st, en)
    k = SponsorCampaign(arena_id=c.arena.id, sponsor_id=body.sponsorId, court_id=body.courtId, starts_on=st, ends_on=en,
                        position=body.position, size_pct=body.sizePct, opacity=body.opacity, duration_s=body.durationS,
                        priority=body.priority, active=body.active)
    db.add(k)
    db.flush()
    audit.record(db, c.user.id, c.arena.id, "campaign.create", "campaign", k.id, body.model_dump(mode="json"))
    db.commit()
    return _camp_out(k, db.get(Sponsor, k.sponsor_id))


def _campaign(db: Session, c: ArenaCtx, cid: str) -> SponsorCampaign:
    k = db.get(SponsorCampaign, cid)
    if not k or k.arena_id != c.arena.id:
        raise HTTPException(404, "campanha não encontrada")
    return k


@router.patch("/campaigns/{campaign_id}")
def patch_campaign(campaign_id: str, body: CampaignPatch, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    k = _campaign(db, c, campaign_id)
    ch = body.model_dump(exclude_unset=True)
    st = _norm_dt(ch["startsOn"]) if "startsOn" in ch else k.starts_on
    en = _norm_dt(ch["endsOn"]) if "endsOn" in ch else k.ends_on
    _validate_campaign(db, c, None, ch.get("courtId"), ch.get("position"), st, en)
    for key, attr in (("courtId", "court_id"), ("position", "position"), ("sizePct", "size_pct"), ("opacity", "opacity"),
                      ("durationS", "duration_s"), ("priority", "priority"), ("active", "active")):
        if key in ch:
            setattr(k, attr, ch[key])
    k.starts_on, k.ends_on = st, en
    audit.record(db, c.user.id, c.arena.id, "campaign.update", "campaign", k.id, json.loads(body.model_dump_json(exclude_unset=True)))
    db.commit()
    return _camp_out(k, db.get(Sponsor, k.sponsor_id))


@router.delete("/campaigns/{campaign_id}", status_code=204)
def delete_campaign(campaign_id: str, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    k = _campaign(db, c, campaign_id)
    audit.record(db, c.user.id, c.arena.id, "campaign.delete", "campaign", k.id)
    db.delete(k)
    db.commit()


class PreviewIn(BaseModel):
    sponsorId: str = Field(max_length=32)
    position: str = "bottom-right"
    sizePct: int = Field(15, ge=5, le=40)
    opacity: float = Field(1.0, ge=0.1, le=1.0)
    videoId: str | None = Field(None, max_length=32)


@router.post("/campaigns/preview")
def preview(body: PreviewIn, c: ArenaCtx = Depends(staff), db: Session = Depends(get_db)):
    """Prévia fiel (mesma geometria do FFmpeg) sobre um frame real do replay ou um quadro de exemplo."""
    from io import BytesIO

    from PIL import Image, ImageDraw

    if body.position not in POSITIONS:
        raise HTTPException(422, "posição inválida")
    s = _sponsor(db, c, body.sponsorId)
    base = None
    if body.videoId:
        v = _video(db, c, body.videoId)
        if v.thumb_key and storage.path_for(v.thumb_key).exists():
            base = Image.open(storage.path_for(v.thumb_key)).convert("RGBA")
    if base is None:
        base = Image.new("RGBA", (1280, 720), (30, 60, 90, 255))
        d = ImageDraw.Draw(base)
        for i in range(0, 720, 60):
            d.line([(0, i), (1280, i)], fill=(255, 255, 255, 25), width=2)
        d.rectangle([40, 40, 1240, 680], outline=(255, 255, 255, 60), width=3)
    W = 1280
    base = base.resize((W, round(base.height * W / base.width)))
    H = base.height
    logo = Image.open(storage.path_for(s.asset_key)).convert("RGBA")
    x, y, w, h = policy.overlay_geometry(W, H, logo.width, logo.height, body.position, body.sizePct)
    logo = logo.resize((w, h))
    if body.opacity < 1:
        a = logo.getchannel("A").point(lambda p: int(p * body.opacity))
        logo.putalpha(a)
    base.alpha_composite(logo, (x, y))
    out = BytesIO()
    base.convert("RGB").save(out, "PNG")
    return Response(out.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


# ---------------- música licenciada ----------------
USES = {"play", "export", "social"}


def _track_out(t: MusicTrack, uid: str) -> dict:
    return {"id": t.id, "title": t.title, "artist": t.artist, "description": t.description, "duration": round(t.duration, 1),
            "licenseInfo": t.license_info, "rightsDeclared": t.rights_declared,
            "licenseValidUntil": t.license_valid_until.date().isoformat() if t.license_valid_until else None,
            "allowedUse": [u for u in t.allowed_use.split(",") if u], "status": t.status, "position": t.position,
            "usable": policy.track_usable(t), "audioUrl": sign("music", t.id, uid)}


def _parse_use(raw: str) -> str:
    uses = [u.strip() for u in raw.split(",") if u.strip()]
    if not uses or any(u not in USES for u in uses):
        raise HTTPException(422, "escopo de uso inválido (play, export, social)")
    return ",".join(sorted(set(uses)))


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw[:10]).replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(422, "data de validade inválida (AAAA-MM-DD)")


@router.get("/music")
def list_music(c: ArenaCtx = Depends(staff), db: Session = Depends(get_db)):
    rows = db.query(MusicTrack).filter_by(arena_id=c.arena.id).order_by(MusicTrack.position, MusicTrack.title).all()
    return [_track_out(t, c.user.id) for t in rows]


@router.post("/music", status_code=201)
async def create_music(title: str = Form(..., min_length=2, max_length=160), artist: str = Form("", max_length=160),
                       description: str = Form("", max_length=300), license_info: str = Form(..., min_length=10, max_length=2000),
                       rights_declared: bool = Form(False), license_valid_until: str | None = Form(None),
                       allowed_use: str = Form("export"), file: UploadFile = File(...),
                       c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    uses, valid = _parse_use(allowed_use), _parse_date(license_valid_until)
    ext = ext_of(file.filename)
    tmp = await to_temp(file, settings.max_music_bytes, ext)
    try:
        dur = validate_music(tmp, ext)
        tid = new_id()
        key = storage.make_key(c.arena.id, "music", f"{tid}{ext}")
        storage.move_into(tmp, key)
    finally:
        tmp.unlink(missing_ok=True)
    t = MusicTrack(id=tid, arena_id=c.arena.id, asset_key=key, title=title.strip(), artist=artist.strip(), description=description,
                   duration=dur, license_info=license_info, rights_declared=rights_declared, license_valid_until=valid,
                   allowed_use=uses, status="enabled")
    db.add(t)
    audit.record(db, c.user.id, c.arena.id, "music.create", "music", tid,
                 {"title": title, "rights_declared": rights_declared, "valid_until": license_valid_until, "use": uses})
    db.commit()
    return _track_out(t, c.user.id)


class MusicPatch(BaseModel):
    title: str | None = Field(None, min_length=2, max_length=160)
    artist: str | None = Field(None, max_length=160)
    licenseInfo: str | None = Field(None, min_length=10, max_length=2000)
    rightsDeclared: bool | None = None
    licenseValidUntil: str | None = None
    allowedUse: str | None = None
    status: str | None = Field(None, pattern=r"^(enabled|disabled)$")
    position: int | None = Field(None, ge=0, le=9999)


def _track(db: Session, c: ArenaCtx, tid: str) -> MusicTrack:
    t = db.get(MusicTrack, tid)
    if not t or t.arena_id != c.arena.id:
        raise HTTPException(404, "faixa não encontrada")
    return t


@router.patch("/music/{track_id}")
def patch_music(track_id: str, body: MusicPatch, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    t, ch = _track(db, c, track_id), body.model_dump(exclude_unset=True)
    if "allowedUse" in ch:
        t.allowed_use = _parse_use(ch["allowedUse"])
    if "licenseValidUntil" in ch:
        t.license_valid_until = _parse_date(ch["licenseValidUntil"])
    for k, attr in (("title", "title"), ("artist", "artist"), ("licenseInfo", "license_info"), ("rightsDeclared", "rights_declared"),
                    ("status", "status"), ("position", "position")):
        if k in ch:
            setattr(t, attr, ch[k])
    audit.record(db, c.user.id, c.arena.id, "music.update", "music", t.id, ch)
    db.commit()
    return _track_out(t, c.user.id)


@router.delete("/music/{track_id}", status_code=204)
def delete_music(track_id: str, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    t = _track(db, c, track_id)
    if c.arena.audio_default_music_id == t.id:
        c.arena.audio_default_music_id = None
    storage.delete(t.asset_key)
    audit.record(db, c.user.id, c.arena.id, "music.delete", "music", t.id, {"title": t.title})
    db.delete(t)
    db.commit()


# ---------------- equipe ----------------
class MemberIn(BaseModel):
    email: str = Field(max_length=254)
    role: str


class MemberPatch(BaseModel):
    role: str | None = None
    status: str | None = Field(None, pattern=r"^(active|suspended)$")


@router.get("/members")
def list_members(c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    rows = db.query(Membership, User).join(User, User.id == Membership.user_id).filter(Membership.arena_id == c.arena.id).all()
    return [{"id": m.id, "name": u.name, "email": u.email, "role": m.role, "status": m.status} for m, u in rows]


def _last_admin_guard(db: Session, arena_id: str, m: Membership) -> None:
    if m.role == "arena_admin" and m.status == "active":
        n = db.query(func.count(Membership.id)).filter_by(arena_id=arena_id, role="arena_admin", status="active").scalar()
        if n <= 1:
            raise HTTPException(409, "a arena precisa manter pelo menos um gestor ativo")


@router.post("/members", status_code=201)
def add_member(body: MemberIn, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    if body.role not in ROLES:
        raise HTTPException(422, "papel inválido")
    u = db.query(User).filter_by(email=body.email.strip().lower()).first()
    if not u:
        raise HTTPException(404, "usuário não cadastrado (peça para criar conta no app do usuário)")
    if db.query(Membership).filter_by(user_id=u.id, arena_id=c.arena.id).first():
        raise HTTPException(409, "usuário já faz parte da arena")
    m = Membership(user_id=u.id, arena_id=c.arena.id, role=body.role)
    db.add(m)
    db.flush()
    audit.record(db, c.user.id, c.arena.id, "member.add", "membership", m.id, {"email": u.email, "role": body.role})
    db.commit()
    return {"id": m.id, "name": u.name, "email": u.email, "role": m.role, "status": m.status}


@router.patch("/members/{member_id}")
def patch_member(member_id: str, body: MemberPatch, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    m = db.get(Membership, member_id)
    if not m or m.arena_id != c.arena.id:
        raise HTTPException(404, "membro não encontrado")
    ch = body.model_dump(exclude_unset=True)
    if "role" in ch and ch["role"] not in ROLES:
        raise HTTPException(422, "papel inválido")
    if ("role" in ch and ch["role"] != "arena_admin") or ch.get("status") == "suspended":
        _last_admin_guard(db, c.arena.id, m)
    for k in ("role", "status"):
        if k in ch:
            setattr(m, k, ch[k])
    audit.record(db, c.user.id, c.arena.id, "member.update", "membership", m.id, ch)
    db.commit()
    return {"id": m.id, "role": m.role, "status": m.status}


@router.delete("/members/{member_id}", status_code=204)
def remove_member(member_id: str, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    m = db.get(Membership, member_id)
    if not m or m.arena_id != c.arena.id:
        raise HTTPException(404, "membro não encontrado")
    _last_admin_guard(db, c.arena.id, m)
    audit.record(db, c.user.id, c.arena.id, "member.remove", "membership", m.id)
    db.delete(m)
    db.commit()


# ---------------- agentes locais / auditoria ----------------
class AgentIn(BaseModel):
    label: str = Field(min_length=2, max_length=80)


@router.get("/agents")
def list_agents(c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    rows = db.query(ArenaAgent).filter_by(arena_id=c.arena.id).order_by(ArenaAgent.created_at).all()
    return [{"id": a.id, "label": a.label, "active": a.active, "lastSeenAt": iso(a.last_used_at), "version": a.agent_version,
             "pendingSync": a.pending_sync} for a in rows]


@router.post("/agents", status_code=201)
def create_agent(body: AgentIn, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    token = new_agent_token()
    ag = ArenaAgent(arena_id=c.arena.id, label=body.label, token_hash=hash_agent_token(token))
    db.add(ag)
    db.flush()
    audit.record(db, c.user.id, c.arena.id, "agent.create", "agent", ag.id, {"label": body.label})
    db.commit()
    return {"id": ag.id, "label": ag.label, "token": token, "notice": "guarde agora; não será exibido novamente"}


@router.delete("/agents/{agent_id}", status_code=204)
def revoke_agent(agent_id: str, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    ag = db.get(ArenaAgent, agent_id)
    if not ag or ag.arena_id != c.arena.id:
        raise HTTPException(404, "agente não encontrado")
    ag.active = False
    audit.record(db, c.user.id, c.arena.id, "agent.revoke", "agent", ag.id)
    db.commit()


@router.get("/audit")
def audit_log(limit: int = Query(100, ge=1, le=300), c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    rows = (db.query(AuditLog, User.name).outerjoin(User, User.id == AuditLog.actor_id)
            .filter(AuditLog.arena_id == c.arena.id).order_by(AuditLog.created_at.desc()).limit(limit).all())
    return [{"id": r.id, "actor": name, "action": r.action, "resourceType": r.resource_type, "resourceId": r.resource_id,
             "changes": json.loads(r.changed_fields), "createdAt": iso(r.created_at)} for r, name in rows]
