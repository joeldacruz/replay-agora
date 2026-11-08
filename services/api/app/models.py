from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, new_id, utcnow

ROLES = ("arena_admin", "arena_operator", "user")
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
    mfa_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)  # TOTP base32
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_last_step: Mapped[int] = mapped_column(Integer, default=0)  # anti-replay
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # --- gestão de usuários (migração 0011) ---
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    auth_method: Mapped[str] = mapped_column(String(10), default="password")  # métodos PERMITIDOS: password|google|both
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    invited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    password_set_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    google_link_allowed: Mapped[bool] = mapped_column(Boolean, default=False)  # convite autoriza vincular o Google deste e-mail (1 vez)
    tokens_valid_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # revoga sessões emitidas antes


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[str] = _id()
    name: Mapped[str] = mapped_column(String(60), unique=True)
    max_courts: Mapped[int] = mapped_column(Integer, default=2)
    max_storage_mb: Mapped[int] = mapped_column(Integer, default=10240)
    max_exports_per_user_day: Mapped[int] = mapped_column(Integer, default=20)
    modules: Mapped[str] = mapped_column(String(200), default="sponsors,music")  # csv
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Arena(Base):
    __tablename__ = "arenas"
    id: Mapped[str] = _id()
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|suspended
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("plans.id", ondelete="SET NULL"), nullable=True)
    timezone: Mapped[str] = mapped_column(String(60), default="America/Sao_Paulo")
    brand_color: Mapped[str] = mapped_column(String(7), default="#0b6bcb")
    allow_self_join: Mapped[bool] = mapped_column(Boolean, default=True)
    # Política de áudio padrão da arena
    audio_default_mode: Mapped[str] = mapped_column(String(10), default="original")
    audio_default_music_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    audio_original_volume: Mapped[float] = mapped_column(Float, default=1.0)
    audio_music_volume: Mapped[float] = mapped_column(Float, default=0.6)
    # modos que a arena oferece ao atleta (csv de AUDIO_MODES); o atleta escolhe dentro deles
    audio_allowed_modes: Mapped[str] = mapped_column(String(60), default="original,music,mix,mute")
    # Armazenamento: override do plano (definido SOMENTE pelo portal) e limiares de alerta (definidos pela arena)
    storage_limit_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_warn_pct: Mapped[int] = mapped_column(Integer, default=80)
    storage_critical_pct: Mapped[int] = mapped_column(Integer, default=95)
    # Recursos do computador da arena (ajustados pelo gestor; o agente aplica dentro dos limites do hardware local)
    agent_max_ffmpeg: Mapped[int] = mapped_column(Integer, default=1)
    agent_hwaccel: Mapped[str] = mapped_column(String(8), default="none")  # none|auto|vaapi|qsv
    agent_render_nice: Mapped[int] = mapped_column(Integer, default=10)
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
    storage_limit_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)  # limite da quadra (≤ limite da arena)
    visibility: Mapped[str] = mapped_column(String(20), default="arena")  # padrão para novos replays
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ArenaAgent(Base):
    """Motor local da arena: credencial por arena (hash) + saúde reportada, sem segredos."""
    __tablename__ = "arena_agents"
    id: Mapped[str] = _id()
    arena_id: Mapped[str] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(80))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    agent_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pending_sync: Mapped[int] = mapped_column(Integer, default=0)
    # Registro do computador da arena: o token fica vinculado ao primeiro node_id (evita clonagem silenciosa)
    node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(120), nullable=True)
    registered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    config_ack: Mapped[str | None] = mapped_column(String(40), nullable=True)  # configVersion aplicado pelo agente
    system_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # CPU/memória/disco do último heartbeat
    # Credenciais rotativas: a anterior segue válida só por uma janela curta (o agente pode perder a resposta da rotação)
    prev_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prev_valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    prev_sig_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    token_rotated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # NULL = legado, sem expiração
    sig_key: Mapped[str | None] = mapped_column(String(64), nullable=True)  # HMAC de requisição; NULL = legado sem assinatura
    cert_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)  # SHA-256 do certificado de cliente (mTLS futuro)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AgentNonce(Base):
    """Nonces já usados por agente: uma requisição assinada só vale UMA vez (anti-replay)."""
    __tablename__ = "agent_nonces"
    __table_args__ = (UniqueConstraint("agent_id", "nonce"), Index("ix_nonces_ts", "ts"))
    id: Mapped[str] = _id()
    agent_id: Mapped[str] = mapped_column(ForeignKey("arena_agents.id", ondelete="CASCADE"))
    nonce: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RelayRequest(Base):
    """Pedido de um trecho (janela de bytes) ao agente da arena. O agente busca por conexão de SAÍDA e devolve os bytes,
    que ficam num spool temporário e são apagados ao serem entregues ao jogador. Nunca vira armazenamento permanente."""
    __tablename__ = "relay_requests"
    __table_args__ = (Index("ix_relay_arena_status", "arena_id", "status", "created_at"),)
    id: Mapped[str] = _id()
    arena_id: Mapped[str] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"))
    agent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(10))  # video|render
    ref: Mapped[str] = mapped_column(String(120))  # local_id do replay ou id do job de renderização
    start: Mapped[int] = mapped_column(BigInteger)
    end: Mapped[int] = mapped_column(BigInteger)  # inclusivo
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending|claimed|done|failed|expired|consumed
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    total_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CourtRuntime(Base):
    """Última telemetria reportada pelo motor local para UMA quadra (sem caminhos nem segredos)."""
    __tablename__ = "court_runtime"
    court_id: Mapped[str] = mapped_column(ForeignKey("courts.id", ondelete="CASCADE"), primary_key=True)
    arena_id: Mapped[str] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"), index=True)
    state: Mapped[str] = mapped_column(String(20), default="ok")  # ok|degraded|storage_failure|camera_down
    queue_depth: Mapped[int] = mapped_column(Integer, default=0)
    completed_total: Mapped[int] = mapped_column(Integer, default=0)
    failed_total: Mapped[int] = mapped_column(Integer, default=0)
    last_render_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_render_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    p95_render_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    storage_used_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    diag_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # diagnóstico da quadra (câmera, buffer, botoeira, fila) — sem segredos
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AgentCommand(Base):
    """Comando enfileirado pelo painel para o agente da arena (hoje: teste manual de acionamento). O agente nunca é chamado
    pela internet: ele busca os comandos no heartbeat (conexão de saída) e devolve o resultado."""
    __tablename__ = "agent_commands"
    __table_args__ = (Index("ix_cmd_arena_status", "arena_id", "status", "created_at"),)
    id: Mapped[str] = _id()
    arena_id: Mapped[str] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"))
    court_id: Mapped[str] = mapped_column(ForeignKey("courts.id", ondelete="CASCADE"))
    court_key: Mapped[str] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(20), default="test_trigger")
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending|delivered|done|failed|expired
    created_by: Mapped[str] = mapped_column(String(32))
    agent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(30), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class StorageSample(Base):
    """Série temporal de uso (arena inteira quando court_id é NULL) para previsão de esgotamento."""
    __tablename__ = "storage_samples"
    __table_args__ = (Index("ix_samples_arena_court_ts", "arena_id", "court_id", "ts"),)
    id: Mapped[str] = _id()
    arena_id: Mapped[str] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"))
    court_id: Mapped[str | None] = mapped_column(ForeignKey("courts.id", ondelete="CASCADE"), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    used_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    disk_free_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    disk_total_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


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
    __table_args__ = (Index("ix_videos_arena_created", "arena_id", "created_at"),
                      UniqueConstraint("arena_id", "local_id", name="uq_videos_arena_local_id"))
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
    # 'central' = arquivo no storage da API (legado/simulado); 'agent' = só metadados aqui, o MP4 fica no computador da arena
    storage_location: Mapped[str] = mapped_column(String(10), default="central")
    local_id: Mapped[str | None] = mapped_column(String(120), nullable=True)  # id estável gerado pelo motor local (idempotência)
    protected: Mapped[bool] = mapped_column(Boolean, default=False)  # protegido contra qualquer exclusão/retenção
    media_state: Mapped[str] = mapped_column(String(12), default="ok")  # ok|missing (o agente não achou o arquivo)


class VideoAccess(Base):
    __tablename__ = "video_access"
    __table_args__ = (UniqueConstraint("user_id", "video_id"),)
    id: Mapped[str] = _id()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    share_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ShareLink(Base):
    """Link de compartilhamento expirável e revogável; resgatado por usuário autenticado."""
    __tablename__ = "share_links"
    id: Mapped[str] = _id()
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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
    __table_args__ = (
        Index("ix_exports_status_created", "status", "created_at"),
        Index("ix_exports_preview_cache_key", "preview_cache_key", unique=True),
    )
    id: Mapped[str] = _id()
    arena_id: Mapped[str] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    audio_mode: Mapped[str] = mapped_column(String(10))
    purpose: Mapped[str] = mapped_column(String(12), default="export")  # export|preview
    preview_cache_key: Mapped[str | None] = mapped_column(String(64), nullable=True)  # unico para previews compartilhados
    music_id: Mapped[str | None] = mapped_column(ForeignKey("music_tracks.id", ondelete="SET NULL"), nullable=True)
    original_volume: Mapped[float] = mapped_column(Float, default=1.0)
    music_volume: Mapped[float] = mapped_column(Float, default=0.6)
    frame_orientation: Mapped[str] = mapped_column(String(12), default="horizontal")  # moldura aplicada pelo agente (horizontal|vertical)
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
    # 'central' = worker FFmpeg da API (legado); 'agent' = renderizado e guardado no computador da arena
    render_location: Mapped[str] = mapped_column(String(8), default="central")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # concessão do agente; expirada => reatribui
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
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


# ====================================================================================================================
# Configuração remota (Portal do Desenvolvedor -> API central -> agente local). Ver docs/CONFIGURACAO-REMOTA.md.
# Segredos de câmera ficam CIFRADOS (secretbox) e nunca são devolvidos por nenhuma consulta.
# ====================================================================================================================
class LocalServer(Base):
    """Servidor local de uma arena. O agente (arena_agents) é a identidade autenticada vinculada a ele."""
    __tablename__ = "local_servers"
    __table_args__ = (UniqueConstraint("arena_id", "name"),)
    id: Mapped[str] = _id()
    arena_id: Mapped[str] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(300), default="")
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("arena_agents.id", ondelete="SET NULL"), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active|revoked
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_state: Mapped[str | None] = mapped_column(String(12), nullable=True)  # último estado observado (para histórico de conexão)
    applied_version: Mapped[int] = mapped_column(Integer, default=0)            # versão de configuração CONFIRMADA pelo agente
    inventory_json: Mapped[str | None] = mapped_column(Text, nullable=True)     # dispositivos/serviços informados pelo agente (sem segredos)
    inventory_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ServerEvent(Base):
    __tablename__ = "server_events"
    __table_args__ = (Index("ix_srvev_server", "server_id", "created_at"),)
    id: Mapped[str] = _id()
    server_id: Mapped[str] = mapped_column(ForeignKey("local_servers.id", ondelete="CASCADE"))
    arena_id: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(24))  # provisioned|connected|disconnected|revoked|config_applied|config_failed|config_reverted
    detail: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Camera(Base):
    __tablename__ = "cameras"
    id: Mapped[str] = _id()
    arena_id: Mapped[str] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"), index=True)
    court_id: Mapped[str | None] = mapped_column(ForeignKey("courts.id", ondelete="SET NULL"), nullable=True)
    server_id: Mapped[str | None] = mapped_column(ForeignKey("local_servers.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(80))
    vendor: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(60), default="")
    host: Mapped[str] = mapped_column(String(253))
    rtsp_port: Mapped[int] = mapped_column(Integer, default=554)
    transport: Mapped[str] = mapped_column(String(3), default="tcp")
    rtsp_path: Mapped[str] = mapped_column(String(200), default="")
    username: Mapped[str] = mapped_column(String(80), default="")
    secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)  # senha cifrada; NUNCA exposta
    secret_version: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    codec: Mapped[str] = mapped_column(String(10), default="h264")
    profile: Mapped[str] = mapped_column(String(40), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    row_version: Mapped[int] = mapped_column(Integer, default=1)  # controle otimista de concorrência
    last_test_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Controller(Base):
    """Controlador de acionamento (Arduino USB, botoeira IP...). Capacidade real (nº de entradas) vem do firmware informado."""
    __tablename__ = "controllers"
    __table_args__ = (UniqueConstraint("server_id", "name"),)
    id: Mapped[str] = _id()
    arena_id: Mapped[str] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"), index=True)
    server_id: Mapped[str] = mapped_column(ForeignKey("local_servers.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16), default="arduino_usb")  # arduino_usb|ip_button
    name: Mapped[str] = mapped_column(String(60))
    port_id: Mapped[str] = mapped_column(String(160), default="")   # identificador estável (by-id/serial), não /dev/ttyUSB0 fixo
    baud: Mapped[int] = mapped_column(Integer, default=9600)
    address: Mapped[str] = mapped_column(String(253), default="")    # botoeira IP
    inputs_capacity: Mapped[int] = mapped_column(Integer, default=1)  # entradas suportadas pelo firmware (declarado/detectado)
    debounce_s: Mapped[float] = mapped_column(Float, default=0.3)
    cooldown_s: Mapped[float] = mapped_column(Float, default=3.0)
    min_interval_s: Mapped[float] = mapped_column(Float, default=0.0)
    reconnect_behavior: Mapped[str] = mapped_column(String(20), default="discard_pending")  # discard_pending|process_pending
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ControllerInput(Base):
    __tablename__ = "controller_inputs"
    __table_args__ = (UniqueConstraint("controller_id", "input_ref"),)
    id: Mapped[str] = _id()
    controller_id: Mapped[str] = mapped_column(ForeignKey("controllers.id", ondelete="CASCADE"), index=True)
    arena_id: Mapped[str] = mapped_column(String(32), index=True)
    input_ref: Mapped[str] = mapped_column(String(40))              # mensagem/entrada emitida pelo firmware (ex.: REPLAY_01)
    label: Mapped[str] = mapped_column(String(60), default="")
    court_id: Mapped[str | None] = mapped_column(ForeignKey("courts.id", ondelete="SET NULL"), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CourtSetting(Base):
    """Configuração técnica de captura/replay de UMA quadra (somente Portal Dev; o gestor da arena não vê)."""
    __tablename__ = "court_settings"
    court_id: Mapped[str] = mapped_column(ForeignKey("courts.id", ondelete="CASCADE"), primary_key=True)
    arena_id: Mapped[str] = mapped_column(String(32), index=True)
    server_id: Mapped[str | None] = mapped_column(ForeignKey("local_servers.id", ondelete="SET NULL"), nullable=True)
    pre_s: Mapped[float] = mapped_column(Float, default=15.0)
    post_s: Mapped[float] = mapped_column(Float, default=5.0)
    close_countdown_s: Mapped[float] = mapped_column(Float, default=2.0)
    buffer_s: Mapped[float] = mapped_column(Float, default=120.0)
    segment_s: Mapped[float] = mapped_column(Float, default=2.0)
    storage_limit_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retention_days: Mapped[int] = mapped_column(Integer, default=30)
    audio_mode: Mapped[str] = mapped_column(String(10), default="original")
    export_formats: Mapped[str] = mapped_column(String(40), default="horizontal")  # horizontal|vertical|horizontal,vertical
    priority: Mapped[int] = mapped_column(Integer, default=5)
    reconnect_min_s: Mapped[float] = mapped_column(Float, default=1.0)
    reconnect_max_s: Mapped[float] = mapped_column(Float, default=30.0)
    frame_h_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    frame_v_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    frames_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    out_h: Mapped[str] = mapped_column(String(12), default="1920x1080")
    out_v: Mapped[str] = mapped_column(String(12), default="1080x1920")
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Frame(Base):
    """Moldura (biblioteca por arena). O arquivo vive em FrameVersion (versionado e com SHA-256)."""
    __tablename__ = "frames"
    __table_args__ = (UniqueConstraint("arena_id", "name"),)
    id: Mapped[str] = _id()
    arena_id: Mapped[str] = mapped_column(ForeignKey("arenas.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(300), default="")
    orientation: Mapped[str] = mapped_column(String(12), default="horizontal")  # horizontal|vertical|other
    kind: Mapped[str] = mapped_column(String(10), default="default")            # default|court|event|sponsor
    court_id: Mapped[str | None] = mapped_column(ForeignKey("courts.id", ondelete="SET NULL"), nullable=True)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    current_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FrameVersion(Base):
    __tablename__ = "frame_versions"
    __table_args__ = (UniqueConstraint("frame_id", "version"),)
    id: Mapped[str] = _id()
    frame_id: Mapped[str] = mapped_column(ForeignKey("frames.id", ondelete="CASCADE"), index=True)
    arena_id: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    bytes: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    window_json: Mapped[str] = mapped_column(Text)   # área do vídeo detectada na arte (px da imagem): {x,y,w,h}
    layout_json: Mapped[str] = mapped_column(Text)   # composição (ver framing.py)
    note: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ConfigRevision(Base):
    """Versão da configuração DESEJADA de um servidor. Só vale como 'aplicada' quando o AGENTE confirma."""
    __tablename__ = "config_revisions"
    __table_args__ = (UniqueConstraint("server_id", "version"), Index("ix_rev_server_status", "server_id", "status"))
    id: Mapped[str] = _id()
    arena_id: Mapped[str] = mapped_column(String(32), index=True)
    server_id: Mapped[str] = mapped_column(ForeignKey("local_servers.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    # rascunho|salvo|pendente|enviado|recebido|aplicando|aplicado|falha|revertido
    status: Mapped[str] = mapped_column(String(12), default="salvo")
    snapshot_json: Mapped[str] = mapped_column(Text)
    base_version: Mapped[int] = mapped_column(Integer, default=0)   # versão que o editor tinha carregado (concorrência)
    allow_restart: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(200), default="")
    reverted_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(String(400), nullable=True)


class AgentTask(Base):
    """Tarefa de DIAGNÓSTICO/OPERAÇÃO de lista fechada (sem shell). O agente a busca por conexão de saída e devolve o resultado."""
    __tablename__ = "agent_tasks"
    __table_args__ = (Index("ix_task_server_status", "server_id", "status", "created_at"),)
    id: Mapped[str] = _id()
    arena_id: Mapped[str] = mapped_column(String(32), index=True)
    server_id: Mapped[str] = mapped_column(ForeignKey("local_servers.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(24))
    params_json: Mapped[str] = mapped_column(Text, default="{}")  # sem segredos (o segredo é resolvido na entrega e descartado)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending|delivered|done|failed|expired
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)


# ====================================================================================================================
# Gestão de usuários e login Google (migração 0011)
# ====================================================================================================================
class UserIdentity(Base):
    """Identidade externa vinculada à conta. Chave estável = (provider, sub); o e-mail NUNCA identifica a conta."""
    __tablename__ = "user_identities"
    __table_args__ = (UniqueConstraint("provider", "subject"), UniqueConstraint("user_id", "provider"))
    id: Mapped[str] = _id()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str] = mapped_column(String(64))
    email: Mapped[str] = mapped_column(String(254))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OAuthFlow(Base):
    """Fluxo Authorization Code + PKCE em andamento (state de uso único, nonce, code_verifier) e ticket de entrega ao navegador."""
    __tablename__ = "oauth_flows"
    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    app: Mapped[str] = mapped_column(String(10))
    intent: Mapped[str] = mapped_column(String(8), default="login")  # login|link
    nonce: Mapped[str] = mapped_column(String(64))
    code_verifier: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)   # intent=link: quem está vinculando
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ticket_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ticket_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ticket_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ticket_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AccountToken(Base):
    """Convite / definição / redefinição de senha: token de uso único, só o hash fica no banco."""
    __tablename__ = "account_tokens"
    id: Mapped[str] = _id()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # invite|reset
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class OutboxMail(Base):
    """E-mails gerados. Com SMTP configurado só registra o envio; sem SMTP (homologação) guarda o texto para o global_admin entregar."""
    __tablename__ = "outbox_mail"
    id: Mapped[str] = _id()
    to_email: Mapped[str] = mapped_column(String(254))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(10), default="")
    status: Mapped[str] = mapped_column(String(10), default="outbox")  # outbox|sent|failed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class IntegrationSetting(Base):
    """Configurações protegidas (ex.: credenciais OAuth do Google). Segredo cifrado (secretbox); nunca devolvido."""
    __tablename__ = "integration_settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value_enc: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
