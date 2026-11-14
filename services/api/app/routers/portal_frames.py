"""Portal Dev — biblioteca de molduras por arena (PNG/ZIP), versionamento, janela do vídeo, composição e prévia REAL.

A prévia usa a MESMA geometria e a MESMA cadeia de filtros FFmpeg (framing.py) que o agente aplica na exportação.
Arquivos ficam em armazenamento privado por arena; nenhuma rota serve a moldura de uma arena para outra.
"""
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import audit, framing, frameimg, remote_config as rc, storage
from ..config import settings
from ..db import get_db, utcnow
from ..deps import Auth, platform_auth
from ..models import Arena, Court, CourtSetting, Frame, FrameVersion
from .serializers import iso

router = APIRouter(prefix="/api/platform", tags=["portal-dev-frames"])
NAME_RE = r"^[^\x00-\x1f<>]{2,80}$"
MAX_ZIP_UPLOAD = 40 * 1024 * 1024


def ver_out(v: FrameVersion) -> dict:
    return {"id": v.id, "version": v.version, "sha256": v.sha256, "bytes": v.bytes, "width": v.width, "height": v.height, "window": json.loads(v.window_json),
            "layout": json.loads(v.layout_json), "note": v.note, "createdAt": iso(v.created_at)}


def frame_out(db: Session, f: Frame, detail: bool = False) -> dict:
    cur = db.get(FrameVersion, f.current_version_id) if f.current_version_id else None
    uses = frame_usage(db, f)
    out = {"id": f.id, "arenaId": f.arena_id, "name": f.name, "description": f.description, "orientation": f.orientation, "kind": f.kind, "courtId": f.court_id,
           "available": f.available, "current": ver_out(cur) if cur else None, "usedBy": uses, "createdAt": iso(f.created_at)}
    if detail:
        out["versions"] = [ver_out(v) for v in db.query(FrameVersion).filter_by(frame_id=f.id).order_by(FrameVersion.version.desc())]
    return out


def frame_usage(db: Session, f: Frame) -> list[dict]:
    q = (db.query(Court, CourtSetting).join(CourtSetting, CourtSetting.court_id == Court.id)
         .filter(Court.arena_id == f.arena_id, (CourtSetting.frame_h_id == f.id) | (CourtSetting.frame_v_id == f.id)))
    return [{"courtId": c.id, "court": c.label} for c, _ in q]


def _frame(db: Session, fid: str) -> Frame:
    f = db.get(Frame, fid)
    if not f or f.deleted_at:
        raise HTTPException(404, "moldura não encontrada")
    return f


def _orient(w: int, h: int, wanted: str) -> str:
    if wanted in ("horizontal", "vertical", "other"):
        return wanted
    return "horizontal" if w >= h else "vertical"


def _store(db: Session, f: Frame, png: bytes, w: int, h: int, sha: str, layout: dict | None, window: dict | None, user_id: str, note: str = "") -> FrameVersion:
    n = (db.query(FrameVersion).filter_by(frame_id=f.id).count()) + 1
    key = storage.make_key(f.arena_id, "frames", f"{f.id}-v{n}.png")
    storage.ensure_parent(key).write_bytes(png)
    win = window or frameimg.detect_window(png)
    v = FrameVersion(frame_id=f.id, arena_id=f.arena_id, version=n, storage_key=key, sha256=sha, bytes=len(png), width=w, height=h,
                     window_json=json.dumps(win), layout_json=json.dumps(layout or framing.default_layout()), note=note[:200], created_by=user_id)
    db.add(v)
    db.flush()
    f.current_version_id = v.id
    return v


async def _read(up: UploadFile, limit: int) -> bytes:
    data = await up.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"arquivo excede {limit // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(422, "arquivo vazio")
    return data


@router.get("/frames")
def frames(arenaId: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    return [frame_out(db, f) for f in db.query(Frame).filter(Frame.arena_id == arenaId, Frame.deleted_at.is_(None)).order_by(Frame.name)]


@router.post("/frames", status_code=201)
async def create_frames(arenaId: str = Form(...), name: str = Form("", max_length=80), description: str = Form("", max_length=300),
                        orientation: str = Form("auto"), kind: str = Form("default"), courtId: str | None = Form(None),
                        file: UploadFile = File(...), a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    """PNG único ou ZIP com PNGs (cada PNG vira uma moldura da arena)."""
    if not db.get(Arena, arenaId):
        raise HTTPException(404, "arena não encontrada")
    if kind not in ("default", "court", "event", "sponsor") or orientation not in ("auto", "horizontal", "vertical", "other"):
        raise HTTPException(422, "tipo/formato inválido")
    if courtId:
        c = db.get(Court, courtId)
        if not c or c.arena_id != arenaId:
            raise HTTPException(404, "quadra não encontrada nesta arena")
    data = await _read(file, MAX_ZIP_UPLOAD)
    items: list[tuple[str, bytes]]
    if data[:2] == b"PK":
        items = frameimg.extract_zip_pngs(data)
    else:
        if len(name.strip()) < 2:
            raise HTTPException(422, "informe o nome do modelo")
        items = [(name.strip(), data)]
    created = []
    for fname, blob in items:
        png, w, h, sha = frameimg.validate_png(blob)
        label = name.strip() if len(items) == 1 else (Path(fname).stem.replace("_", " ")[:80] or "Moldura")
        if len(items) > 1 and name.strip():
            label = f"{name.strip()} — {label}"[:80]
        if db.query(Frame).filter(Frame.arena_id == arenaId, Frame.name == label, Frame.deleted_at.is_(None)).first():
            raise HTTPException(409, f"já existe uma moldura chamada '{label}' nesta arena")
        f = Frame(arena_id=arenaId, name=label, description=description.strip(), orientation=_orient(w, h, orientation), kind=kind, court_id=courtId,
                  created_by=a.user.id)
        db.add(f)
        db.flush()
        _store(db, f, png, w, h, sha, None, None, a.user.id, "versão inicial")
        audit.record(db, a.user.id, arenaId, "platform.frame_create", "frame", f.id, {"name": f.name, "sha256": sha, "bytes": len(png)})
        created.append(f)
    db.commit()
    return [frame_out(db, f, detail=True) for f in created]


@router.get("/frames/{fid}")
def get_frame(fid: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    return frame_out(db, _frame(db, fid), detail=True)


class FramePatch(BaseModel):
    name: str | None = Field(None, pattern=NAME_RE)
    description: str | None = Field(None, max_length=300)
    orientation: str | None = Field(None, pattern=r"^(horizontal|vertical|other)$")
    kind: str | None = Field(None, pattern=r"^(default|court|event|sponsor)$")
    courtId: str | None = None
    available: bool | None = None


@router.patch("/frames/{fid}")
def patch_frame(fid: str, body: FramePatch, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    f = _frame(db, fid)
    ch = body.model_dump(exclude_unset=True)
    if "courtId" in ch and ch["courtId"]:
        c = db.get(Court, ch["courtId"])
        if not c or c.arena_id != f.arena_id:
            raise HTTPException(404, "quadra não encontrada nesta arena")
    if "name" in ch:
        dup = db.query(Frame).filter(Frame.arena_id == f.arena_id, Frame.name == ch["name"].strip(), Frame.id != f.id, Frame.deleted_at.is_(None)).first()
        if dup:
            raise HTTPException(409, "já existe uma moldura com este nome na arena")
        ch["name"] = ch["name"].strip()
    if ch.get("available") is False and frame_usage(db, f):
        raise HTTPException(409, "moldura em uso por quadras: troque a moldura delas antes de indisponibilizá-la")
    for k, v in ch.items():
        setattr(f, {"courtId": "court_id"}.get(k, k), v)
    audit.record(db, a.user.id, f.arena_id, "platform.frame_update", "frame", f.id, ch)
    db.commit()
    return frame_out(db, f, detail=True)


@router.post("/frames/{fid}/versions", status_code=201)
async def new_version(fid: str, note: str = Form("", max_length=200), file: UploadFile = File(...), a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    """Substitui o arquivo criando NOVA versão (a anterior fica no histórico; a composição atual é herdada)."""
    f = _frame(db, fid)
    png, w, h, sha = frameimg.validate_png(await _read(file, frameimg.MAX_PNG_BYTES))
    cur = db.get(FrameVersion, f.current_version_id) if f.current_version_id else None
    layout = json.loads(cur.layout_json) if cur else None
    window = frameimg.detect_window(png)
    v = _store(db, f, png, w, h, sha, layout, window, a.user.id, note or "arquivo substituído")
    audit.record(db, a.user.id, f.arena_id, "platform.frame_version", "frame", f.id, {"version": v.version, "sha256": sha})
    db.commit()
    return frame_out(db, f, detail=True)


class LayoutIn(BaseModel):
    layout: dict
    window: dict | None = None
    note: str = Field("", max_length=200)


@router.put("/frames/{fid}/layout", status_code=201)
def set_layout(fid: str, body: LayoutIn, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    """Salva a composição (enquadramento, escala, margens, janela) como NOVA versão — o histórico preserva a anterior."""
    f = _frame(db, fid)
    cur = db.get(FrameVersion, f.current_version_id)
    try:
        layout = framing.validate_layout(body.layout)
        window = framing.validate_window(body.window, cur.width, cur.height) if body.window else json.loads(cur.window_json)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    png = storage.path_for(cur.storage_key).read_bytes()
    v = _store(db, f, png, cur.width, cur.height, cur.sha256, layout, window, a.user.id, body.note or "composição ajustada")
    audit.record(db, a.user.id, f.arena_id, "platform.frame_layout", "frame", f.id, {"version": v.version})
    db.commit()
    return frame_out(db, f, detail=True)


@router.post("/frames/{fid}/restore/{version}", status_code=201)
def restore_version(fid: str, version: int, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    f = _frame(db, fid)
    old = db.query(FrameVersion).filter_by(frame_id=f.id, version=version).first()
    if not old:
        raise HTTPException(404, "versão não encontrada")
    png = storage.path_for(old.storage_key).read_bytes()
    v = _store(db, f, png, old.width, old.height, old.sha256, json.loads(old.layout_json), json.loads(old.window_json), a.user.id, f"restaurada da v{version}")
    audit.record(db, a.user.id, f.arena_id, "platform.frame_restore", "frame", f.id, {"from": version, "version": v.version})
    db.commit()
    return frame_out(db, f, detail=True)


@router.delete("/frames/{fid}", status_code=204)
def delete_frame(fid: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    f = _frame(db, fid)
    uses = frame_usage(db, f)
    if uses:
        raise HTTPException(409, "moldura em uso pelas quadras: " + ", ".join(u["court"] for u in uses))
    for v in db.query(FrameVersion).filter_by(frame_id=f.id):
        storage.delete(v.storage_key)
    audit.record(db, a.user.id, f.arena_id, "platform.frame_delete", "frame", f.id, {"name": f.name})
    db.delete(f)
    db.commit()


@router.get("/frames/{fid}/image")
def frame_image(fid: str, version: int | None = None, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    f = _frame(db, fid)
    v = (db.query(FrameVersion).filter_by(frame_id=f.id, version=version).first() if version else db.get(FrameVersion, f.current_version_id))
    if not v or v.arena_id != f.arena_id:
        raise HTTPException(404, "versão não encontrada")
    return Response(storage.path_for(v.storage_key).read_bytes(), media_type="image/png", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


class PreviewIn(BaseModel):
    layout: dict | None = None
    window: dict | None = None
    size: str = "1920x1080"
    version: int | None = None
    sample: str = "testsrc"


@router.post("/frames/{fid}/preview")
def preview(fid: str, body: PreviewIn, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    """Prévia FIEL: 1 quadro gerado pelo FFmpeg com os mesmos filtros da exportação (vídeo de teste sintético por baixo da moldura)."""
    f = _frame(db, fid)
    v = (db.query(FrameVersion).filter_by(frame_id=f.id, version=body.version).first() if body.version else db.get(FrameVersion, f.current_version_id))
    if not v:
        raise HTTPException(404, "versão não encontrada")
    try:
        W, H = framing.parse_size(body.size)
        layout = framing.validate_layout(body.layout if body.layout is not None else json.loads(v.layout_json))
        window = framing.validate_window(body.window, v.width, v.height) if body.window else json.loads(v.window_json)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    if body.sample not in ("testsrc", "pattern"):
        raise HTTPException(422, "amostra inválida")
    vw, vh = (1280, 720)
    geom = framing.compute(layout, W, H, v.width, v.height, window)
    vid = framing.video_geometry(geom["window"], vw, vh, layout)
    filters = framing.build_filters(geom, vid, layout)
    src = {"testsrc": f"testsrc2=size={vw}x{vh}:rate=25", "pattern": f"smptebars=size={vw}x{vh}:rate=25"}[body.sample]
    out = Path(tempfile.mkdtemp(dir=storage.root())) / "preview.png"
    cmd = [settings.ffmpeg_bin, "-y", "-v", "error", "-nostdin", "-f", "lavfi", "-i", src, "-loop", "1", "-framerate", "25", "-i", str(storage.path_for(v.storage_key)),
           "-filter_complex_threads", "1", "-filter_complex", ";".join(filters), "-map", "[vfr]", "-frames:v", "1", str(out)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
        if r.returncode != 0 or not out.exists():
            raise HTTPException(500, "falha ao gerar a prévia: " + (r.stderr.strip().splitlines() or ["erro desconhecido"])[-1][:160])
        data = out.read_bytes()
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "tempo esgotado ao gerar a prévia") from None
    finally:
        out.unlink(missing_ok=True)
        try:
            out.parent.rmdir()
        except OSError:
            pass
    return Response(data, media_type="image/png", headers={"X-Geometry": json.dumps(geom, separators=(",", ":")), "Cache-Control": "no-store"})
