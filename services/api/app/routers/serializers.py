from ..models import Court, ExportJob, Video
from .media import sign


def iso(d):
    return d.isoformat() + "Z" if d else None


def video_out(v: Video, court: Court | None, user_id: str, admin: bool = False) -> dict:
    out = {
        "id": v.id, "code": v.code, "title": v.title or f"Replay {v.code}", "arenaId": v.arena_id,
        "courtId": v.court_id, "courtLabel": court.label if court else None,
        "capturedAt": iso(v.captured_at), "duration": round(v.duration, 1), "hasAudio": v.has_audio,
        "processingStatus": v.processing_status, "width": v.width, "height": v.height,
        "thumbnailUrl": sign("thumb", v.id, user_id) if v.thumb_key else None,
    }
    if admin:
        out |= {"visibility": v.visibility, "bytes": v.bytes, "createdAt": iso(v.created_at)}
    return out


def export_out(j: ExportJob, user_id: str, code: str | None = None) -> dict:
    return {
        "id": j.id, "videoId": j.video_id, "videoCode": code, "audioMode": j.audio_mode, "musicId": j.music_id,
        "originalVolume": j.original_volume, "musicVolume": j.music_volume, "applySponsors": j.apply_sponsors,
        "status": j.status, "progress": j.progress, "error": j.error, "createdAt": iso(j.created_at),
        "finishedAt": iso(j.finished_at), "bytes": j.output_bytes, "attempts": j.attempts,
        "downloadUrl": sign("export", j.id, user_id, "attachment") if j.status == "ready" else None,
    }
