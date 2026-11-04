from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, new_id, utcnow

ROLES = ("arena_admin", "operator", "user")
AUDIO_MODES = ("original", "music", "mix", "mute")
POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right")


def _id() -> Mapped[str]:
    return mapped_column(String(32), primary_key=True, default=new_id)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = _id()
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    global_role: Mapped[str | None] = mapped_column(String(20), nullable=True)  # 'global_admin' | None
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Arena(Base):
    __tablename__ = "arenas"
    id: Mapped[str] = _id()
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="active")
    timezone: Mapped[str] = mapped_column(String(60), default="America/Sao_Paulo")
    brand_color: Mapped[str] = mapped_column(String(7), default="#0b6bcb")
    allow_self_join: Mapped[bool] = mapped_column(Boolean, default=True)
    # Política de áudio padrão da arena
    audio_default_mode: Mapped[str] = mapped_column(String(10), default="original")
    audio_default_music_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    audio_original_volume: Mapped[float] = mapped_column(Float, default=1.0)
    audio_music_volume: Mapped[float] = mapped_column(Float, default=0.6)
    audio_user_override: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Membership(Base):
    __tablename__ = "arena_memberships"
    __table_args__ = (UniqueConstraint("user_id", "arena_id"),)
    id: Mapped[str] = _id()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    arena_id: Mapped[str] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    user: Mapped[User] = relationship()


class Court(Base):
    __tablename__ = "courts"
    __table_args__ = (UniqueConstraint("arena_id", "local_court_key"),)
    id: Mapped[str] = _id()
    arena_id: Mapped[str] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(80))
    number: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|maintenance|inactive
    capture_status: Mapped[str] = mapped_column(String(20), default="unknown")
    local_court_key: Mapped[str] = mapped_column(String(60))  # ex.: REPLAY_01
    camera_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)  # referência, nunca URL com senha
    button_ref: Mapped[str | None] = mapped_column(String(20), nullable=True)  # ex.: D2
    retention_days: Mapped[int] = mapped_column(Integer, default=30)
    visibility: Mapped[str] = mapped_column(String(20), default="arena")  # padrão para novos replays
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AgentToken(Base):
    __tablename__ = "agent_tokens"
    id: Mapped[str] = _id()
    arena_id: Mapped[str] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(80))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ReplayEvent(Base):
    __tablename__ = "replay_events"
    __table_args__ = (
        UniqueConstraint("arena_id", "idempotency_key"),
        Index("ix_events_arena_created", "arena_id", "created_at"),
    )
    id: Mapped[str] = _id()
    arena_id: Mapped[str] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"))
    court_id: Mapped[str] = mapped_column(ForeignKey("courts.id", ondelete="CASCADE"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(30), default="agent")
    idempotency_key: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|completed|duplicate|failed
    duplicate_of: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pre_seconds: Mapped[int] = mapped_column(Integer, default=15)
    post_seconds: Mapped[int] = mapped_column(Integer, default=5)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (Index("ix_videos_arena_created", "arena_id", "created_at"),)
    id: Mapped[str] = _id()
    arena_id: Mapped[str] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"))
    court_id: Mapped[str] = mapped_column(ForeignKey("courts.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("replay_events.id", ondelete="SET NULL"), nullable=True)
    code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(160), default="")
    storage_key: Mapped[str] = mapped_column(String(300))
    thumb_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    mime: Mapped[str] = mapped_column(String(60), default="video/mp4")
    bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    duration: Mapped[float] = mapped_column(Float, default=0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    video_codec: Mapped[str | None] = mapped_column(String(30), nullable=True)
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    visibility: Mapped[str] = mapped_column(String(20), default="private")  # private|arena
    processing_status: Mapped[str] = mapped_column(String(20), default="ready")
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class VideoAccess(Base):
    __tablename__ = "video_access"
    __table_args__ = (UniqueConstraint("user_id", "video_id"),)
    id: Mapped[str] = _id()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Sponsor(Base):
    __tablename__ = "sponsors"
    id: Mapped[str] = _id()
    arena_id: Mapped[str] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    website: Mapped[str | None] = mapped_column(String(300), nullable=True)
    asset_key: Mapped[str] = mapped_column(String(300))
    logo_width: Mapped[int] = mapped_column(Integer, default=0)
    logo_height: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|inactive
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SponsorCampaign(Base):
    __tablename__ = "sponsor_campaigns"
    id: Mapped[str] = _id()
    arena_id: Mapped[str] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"), index=True)
    sponsor_id: Mapped[str] = mapped_column(ForeignKey("sponsors.id", ondelete="CASCADE"), index=True)
    court_id: Mapped[str | None] = mapped_column(ForeignKey("courts.id", ondelete="CASCADE"), nullable=True)
    starts_on: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ends_on: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    position: Mapped[str] = mapped_column(String(20), default="bottom-right")
    size_pct: Mapped[int] = mapped_column(Integer, default=15)  # largura do logo em % da largura do vídeo
    opacity: Mapped[float] = mapped_column(Float, default=1.0)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)  # None = vídeo inteiro
    priority: Mapped[int] = mapped_column(Integer, default=100)  # menor = mais prioritário
    template: Mapped[str] = mapped_column(String(30), default="standard")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MusicTrack(Base):
    __tablename__ = "music_tracks"
    id: Mapped[str] = _id()
    arena_id: Mapped[str] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"), index=True)
    asset_key: Mapped[str] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(String(160))
    artist: Mapped[str] = mapped_column(String(160), default="")
    description: Mapped[str] = mapped_column(String(300), default="")
    duration: Mapped[float] = mapped_column(Float, default=0)
    license_info: Mapped[str] = mapped_column(Text)  # autoria / licença / comprovante
    rights_declared: Mapped[bool] = mapped_column(Boolean, default=False)
    license_valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    allowed_use: Mapped[str] = mapped_column(String(100), default="export")  # csv: play,export,social
    status: Mapped[str] = mapped_column(String(20), default="enabled")  # enabled|disabled
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ExportJob(Base):
    __tablename__ = "export_jobs"
    __table_args__ = (Index("ix_exports_status_created", "status", "created_at"),)
    id: Mapped[str] = _id()
    arena_id: Mapped[str] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    audio_mode: Mapped[str] = mapped_column(String(10))
    music_id: Mapped[str | None] = mapped_column(ForeignKey("music_tracks.id", ondelete="SET NULL"), nullable=True)
    original_volume: Mapped[float] = mapped_column(Float, default=1.0)
    music_volume: Mapped[float] = mapped_column(Float, default=0.6)
    apply_sponsors: Mapped[bool] = mapped_column(Boolean, default=True)
    overlay_spec: Mapped[str] = mapped_column(Text, default="[]")  # snapshot JSON dos patrocínios
    sponsor_template_version: Mapped[str] = mapped_column(String(16), default="")
    params_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(12), default="queued")  # queued|processing|ready|failed
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    log: Mapped[str] = mapped_column(Text, default="")
    output_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    output_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_arena_created", "arena_id", "created_at"),)
    id: Mapped[str] = _id()
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    arena_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(60))
    resource_type: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    changed_fields: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
