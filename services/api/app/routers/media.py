"""Entrega de mídia por URL assinada de curta duração. Revalida autorização a cada requisição."""
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import audit, policy, storage
from ..db import get_db
from ..deps import ARENA_STAFF, can_view_video
from ..models import ExportJob, Membership, MusicTrack, Sponsor, User, Video
from ..security import create_media_token, decode_media_token

router = APIRouter(tags=["media"])


def sign(kind: str, rid: str, user_id: str, disposition: str = "inline") -> str:
    return f"/media/{create_media_token(kind, rid, user_id, disposition)}"


def _staff(db: Session, uid: str, arena_id: str) -> bool:
    return db.query(Membership).filter(Membership.user_id == uid, Membership.arena_id == arena_id,
                                       Membership.status == "active", Membership.role.in_(ARENA_STAFF)).first() is not None


@router.get("/media/{token}")
def serve(token: str, db: Session = Depends(get_db)):
    c = decode_media_token(token)
    if not c:
        raise HTTPException(403, "link expirado ou inválido")
    user = db.get(User, c["uid"])
    if not user or not user.active:
        raise HTTPException(403, "link expirado ou inválido")
    kind, rid, disp = c["k"], c["rid"], c["d"]
    key, mime, name, arena_id = None, "application/octet-stream", "arquivo", None

    if kind in ("video", "thumb"):
        v = db.get(Video, rid)
        if not v or not can_view_video(db, user, v):
            raise HTTPException(404, "não encontrado")
        arena_id = v.arena_id
        if kind == "video":
            # Nunca entregar o video bruto ao atleta quando houver campanha.
            # Equipe autorizada pode acessar a origem somente pelo painel da arena.
            if policy.select_overlays(db, v) and not _staff(db, user.id, v.arena_id):
                raise HTTPException(409, "Replay patrocinado: utilize a prévia processada")
            key, mime, name = v.storage_key, "video/mp4", f"replay-{v.code}.mp4"
        else:
            key, mime, name = v.thumb_key, "image/jpeg", f"replay-{v.code}.jpg"
    elif kind == "export":
        j = db.get(ExportJob, rid)
        if not j or (j.purpose != "preview" and j.user_id != user.id) or j.status != "ready" or not j.output_key:
            raise HTTPException(404, "não encontrado")
        v = db.get(Video, j.video_id)
        if not v or not can_view_video(db, user, v):
            raise HTTPException(404, "não encontrado")
        if j.purpose == "preview" and disp != "inline":
            raise HTTPException(403, "prévia indisponível para download direto")
        if not policy.export_matches_campaigns(db, v, j):
            raise HTTPException(409, "Patrocínio atualizado: gere uma nova versão")
        arena_id = j.arena_id
        key, mime, name = j.output_key, "video/mp4", f"replay-{v.code}-{j.audio_mode}.mp4"
    elif kind == "logo":
        s = db.get(Sponsor, rid)
        if not s or not _staff(db, user.id, s.arena_id):
            raise HTTPException(404, "não encontrado")
        key, mime, name, arena_id = s.asset_key, "image/png", "logo.png", s.arena_id
    elif kind == "music":
        t = db.get(MusicTrack, rid)
        if not t or not _staff(db, user.id, t.arena_id):
            raise HTTPException(404, "não encontrado")
        key, name, arena_id = t.asset_key, "faixa", t.arena_id
        mime = {"mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4", "ogg": "audio/ogg", "flac": "audio/flac"}.get(
            t.asset_key.rsplit(".", 1)[-1], "application/octet-stream")
    else:
        raise HTTPException(404, "não encontrado")

    if not key:
        raise HTTPException(404, "arquivo indisponível")
    path = storage.path_for(key)
    if not path.exists():
        raise HTTPException(404, "arquivo indisponível")
    if disp == "attachment" and kind in ("video", "export"):
        audit.record(db, user.id, arena_id, f"{kind}.download", kind, rid)
        db.commit()
    resp = FileResponse(path, media_type=mime, filename=name if disp == "attachment" else None,
                        content_disposition_type=disp)
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
