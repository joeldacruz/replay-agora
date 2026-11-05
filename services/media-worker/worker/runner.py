"""Fila persistente (tabela export_jobs) + execução FFmpeg com timeout, progresso, logs e limpeza."""
import json
import re
import subprocess
import tempfile
import threading
import time
import traceback
from datetime import timedelta
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.orm import Session

from app import mediainfo, policy, storage
from app.config import settings
from app.db import SessionLocal, utcnow
from app.models import ExportJob, MusicTrack, Video

from .ffmpeg import ExportPlan, Overlay, PlanError, SourceMeta, build_command

MAX_LOG = 8000


def _log(job: ExportJob, msg: str) -> None:
    job.log = (job.log + f"[{utcnow().isoformat(timespec='seconds')}] {msg}\n")[-MAX_LOG:]


def recover_stale(db: Session) -> int:
    """Jobs 'processing' sem heartbeat (worker caiu) voltam à fila, até o limite de tentativas."""
    cutoff = utcnow() - timedelta(seconds=settings.export_stale_after_s)
    n = 0
    for j in db.query(ExportJob).filter(ExportJob.status == "processing", ExportJob.heartbeat_at < cutoff).all():
        if j.attempts >= settings.export_max_attempts:
            j.status, j.error, j.finished_at = "failed", "tempo esgotado após várias tentativas", utcnow()
        else:
            j.status, j.progress = "queued", 0
        _log(j, "job órfão recuperado")
        n += 1
    db.commit()
    return n


def claim(db: Session) -> ExportJob | None:
    cand = db.query(ExportJob.id).filter(ExportJob.status == "queued").order_by(ExportJob.created_at).limit(5).all()
    for (jid,) in cand:
        res = db.execute(update(ExportJob).where(ExportJob.id == jid, ExportJob.status == "queued").values(
            status="processing", attempts=ExportJob.attempts + 1, started_at=utcnow(), heartbeat_at=utcnow(), progress=0, error=None))
        db.commit()
        if res.rowcount == 1:
            return db.get(ExportJob, jid)
    return None


def _fail(db: Session, job: ExportJob, msg: str, detail: str = "") -> None:
    job.status, job.error, job.finished_at = "failed", msg, utcnow()
    _log(job, f"FALHA: {msg} {detail}".strip())
    db.commit()


def _build_plan(db: Session, job: ExportJob, tmp_out: Path) -> ExportPlan:
    v = db.get(Video, job.video_id)
    if not v or v.deleted_at or v.arena_id != job.arena_id:
        raise PlanError("replay indisponível")
    src = storage.path_for(v.storage_key)
    if not src.exists():
        raise PlanError("arquivo de origem não encontrado")
    info = mediainfo.video_info(src)  # não confia nos metadados do banco
    music_path = None
    if job.audio_mode in ("music", "mix"):
        t = db.get(MusicTrack, job.music_id) if job.music_id else None
        # revalida licença/arena no momento da renderização (pode ter expirado/sido desabilitada)
        if not t or t.arena_id != job.arena_id or not policy.track_usable(t):
            raise PlanError("faixa musical sem licença válida no momento da exportação")
        music_path = str(storage.path_for(t.asset_key))
    overlays = []
    if job.apply_sponsors:
        for o in json.loads(job.overlay_spec or "[]"):
            if not o["asset_key"].startswith(f"arenas/{job.arena_id}/"):
                raise PlanError("logotipo de outra arena recusado")
            p = storage.path_for(o["asset_key"])
            if not p.is_file():
                raise PlanError("logotipo de patrocinador ausente; exportação recusada")
            overlays.append(Overlay(str(p), o["logo_w"], o["logo_h"], o["position"], o["size_pct"], o["opacity"], o["duration_s"]))
    meta = SourceMeta(info.duration, info.width, info.height, info.video_codec, info.has_audio, info.audio_codec)
    return ExportPlan(str(src), str(tmp_out), meta, job.audio_mode, music_path, job.original_volume, job.music_volume, overlays)


def _verify_output(path: Path, mode: str, expected: float) -> None:
    d = mediainfo.probe(path)
    kinds = [s["codec_type"] for s in d["streams"]]
    if "video" not in kinds:
        raise RuntimeError("saída sem vídeo")
    if mode == "mute" and "audio" in kinds:
        raise RuntimeError("saída 'mute' contém áudio")
    if mode != "mute" and "audio" not in kinds:
        raise RuntimeError("saída sem faixa de áudio")
    if abs(float(d["format"]["duration"]) - expected) > 1.0:
        raise RuntimeError("duração da saída difere do original")


def run_job(db: Session, job: ExportJob) -> None:
    out_key = storage.make_key(job.arena_id, "exports", f"{job.id}.mp4")
    final = storage.ensure_parent(out_key)
    tmp_out = final.with_suffix(".part.mp4")
    try:
        plan = _build_plan(db, job, tmp_out)
        cmd = build_command(plan, settings.ffmpeg_bin)
    except (PlanError, mediainfo.MediaError) as e:
        return _fail(db, job, str(e))
    _log(job, f"modo={job.audio_mode} overlays={len(plan.overlays)} versão={job.sponsor_template_version or '-'}")
    db.commit()

    err = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True)
    timer = threading.Timer(settings.export_timeout_s, proc.kill)
    timer.start()
    last = 0.0
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            m = re.match(r"out_time_(?:us|ms)=(\d+)", line)
            if m and time.monotonic() - last > 1.0:
                last = time.monotonic()
                job.progress = max(1, min(99, int(int(m.group(1)) / 1e6 / plan.meta.duration * 100)))
                job.heartbeat_at = utcnow()
                db.commit()
        rc = proc.wait()
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
    err.seek(0)
    stderr = err.read().decode(errors="replace")[-1500:]
    err.close()
    if rc != 0:
        tmp_out.unlink(missing_ok=True)
        killed = rc < 0
        return _fail(db, job, "tempo limite excedido" if killed else "falha no processamento FFmpeg", stderr)
    try:
        _verify_output(tmp_out, job.audio_mode, plan.meta.duration)
    except (RuntimeError, mediainfo.MediaError, KeyError) as e:
        tmp_out.unlink(missing_ok=True)
        return _fail(db, job, f"saída inválida: {e}")
    tmp_out.replace(final)
    job.output_key, job.output_bytes = out_key, final.stat().st_size
    job.status, job.progress, job.finished_at = "ready", 100, utcnow()
    _log(job, f"pronto ({job.output_bytes} bytes)")
    db.commit()


def process_one(db: Session | None = None) -> bool:
    own = db is None
    db = db or SessionLocal()
    try:
        recover_stale(db)
        job = claim(db)
        if not job:
            return False
        try:
            run_job(db, job)
        except Exception as e:  # nunca derruba o worker
            db.rollback()
            job = db.get(ExportJob, job.id)
            _fail(db, job, "erro interno no worker", f"{type(e).__name__}: {e}\n{traceback.format_exc()[-800:]}")
        return True
    finally:
        if own:
            db.close()


def main(poll: float = 1.0, once: bool = False) -> None:
    print("media-worker iniciado", flush=True)
    while True:
        did = process_one()
        if once and not did:
            return
        if not did:
            time.sleep(poll)
