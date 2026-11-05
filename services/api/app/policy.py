"""Regras de negócio puras: música licenciada, modos de áudio, seleção de patrocínios e geometria."""
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .db import utcnow
from .models import AUDIO_MODES, Arena, Court, MusicTrack, Sponsor, SponsorCampaign, Video

MAX_OVERLAYS = 4
MARGIN_PCT = 2.0


def track_usable(t: MusicTrack, now: datetime | None = None) -> bool:
    """Faixa só pode ser exportada com direitos declarados, licença vigente e escopo 'export'."""
    now = now or utcnow()
    uses = {u.strip() for u in (t.allowed_use or "").split(",")}
    return (t.status == "enabled" and t.rights_declared and "export" in uses
            and (t.license_valid_until is None or t.license_valid_until >= now))


def usable_tracks(db: Session, arena_id: str) -> list[MusicTrack]:
    rows = db.query(MusicTrack).filter_by(arena_id=arena_id).order_by(MusicTrack.position, MusicTrack.title).all()
    return [t for t in rows if track_usable(t)]


def allowed_modes(arena: Arena, has_original_audio: bool, usable_music: bool) -> list[str]:
    """Modos realmente oferecidos: política da arena ∩ disponibilidade real (áudio original / música liberada)."""
    configured = [m for m in (arena.audio_allowed_modes or "").split(",") if m in AUDIO_MODES]
    out = []
    for m in configured:
        if m in ("original", "mix") and not has_original_audio:
            continue
        if m in ("music", "mix") and not usable_music:
            continue
        out.append(m)
    return out


def overlay_geometry(W: int, H: int, lw: int, lh: int, position: str, size_pct: int) -> tuple[int, int, int, int]:
    """(x, y, w, h) do logo. Usado igualmente pela prévia (Pillow) e pelo FFmpeg."""
    w = max(2, round(W * size_pct / 100 / 2) * 2)
    h = max(2, round(w * lh / max(lw, 1) / 2) * 2)
    m = round(W * MARGIN_PCT / 100)
    x = m if position.endswith("left") else W - w - m
    y = m if position.startswith("top") else H - h - m
    return x, y, w, h


def select_overlays(db: Session, video: Video) -> list[dict]:
    """Campanhas ativas da MESMA arena, para a quadra do vídeo e a data de captura (fuso da arena).
    No máximo uma por posição (menor número de prioridade vence)."""
    arena = db.get(Arena, video.arena_id)
    local = video.captured_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(arena.timezone)).replace(tzinfo=None)
    rows = (db.query(SponsorCampaign, Sponsor)
            .join(Sponsor, Sponsor.id == SponsorCampaign.sponsor_id)
            .filter(SponsorCampaign.arena_id == video.arena_id, Sponsor.arena_id == video.arena_id,
                    SponsorCampaign.active.is_(True), Sponsor.status == "active")
            .order_by(SponsorCampaign.priority, SponsorCampaign.created_at).all())
    used, out = set(), []
    for c, s in rows:
        if c.court_id and c.court_id != video.court_id:
            continue
        if c.starts_on and local < c.starts_on:
            continue
        if c.ends_on and local >= c.ends_on:
            continue
        if c.position in used:
            continue
        used.add(c.position)
        out.append({"campaign_id": c.id, "sponsor_id": s.id, "name": s.name, "asset_key": s.asset_key,
                    "logo_w": s.logo_width, "logo_h": s.logo_height, "position": c.position,
                    "size_pct": c.size_pct, "opacity": c.opacity, "duration_s": c.duration_s})
        if len(out) >= MAX_OVERLAYS:
            break
    return out


def spec_version(spec: list[dict]) -> str:
    return hashlib.sha1(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:12] if spec else ""


def params_hash(video_id: str, mode: str, music_id: str | None, ov: float, mv: float, spec: list[dict]) -> str:
    raw = json.dumps([video_id, mode, music_id, round(ov, 2), round(mv, 2), spec], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def export_matches_campaigns(db: Session, video: Video, job) -> bool:
    """Evita distribuir versões antigas ou sem os patrocínios vigentes."""
    current = select_overlays(db, video)
    return (job.arena_id == video.arena_id
            and job.video_id == video.id
            and bool(job.apply_sponsors) == bool(current)
            and (job.sponsor_template_version or "") == spec_version(current))
