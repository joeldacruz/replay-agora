"""Configuração por variáveis de ambiente. Nenhum segredo real fica no repositório."""
import os
import secrets
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]  # services/api
REPO_ROOT = Path(__file__).resolve().parents[3]


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


class Settings:
    def __init__(self) -> None:
        self.env = os.environ.get("APP_ENV", "development")
        self.database_url = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR}/var/replay.db")
        self.storage_dir = Path(os.environ.get("STORAGE_DIR", str(BASE_DIR / "var" / "storage"))).resolve()
        secret = os.environ.get("SECRET_KEY")
        if self.env == "production":
            # Um segredo previsível permite falsificar JWTs de qualquer perfil.
            # Não permitir o valor demonstrativo do .env.example nem chaves curtas.
            placeholders = ("troque", "changeme", "change-me", "replace-me", "your-secret", "exemplo")
            if (not secret or len(secret.strip()) < 32
                    or secret.strip().lower().startswith(placeholders)):
                raise RuntimeError("SECRET_KEY de produção deve ser aleatório, ter pelo menos 32 caracteres e não ser um placeholder")
        elif not secret:
            secret = secrets.token_urlsafe(48)  # efêmero: tokens deixam de valer ao reiniciar
        self.secret_key = secret
        self.access_token_ttl_s = _env_int("ACCESS_TOKEN_TTL_S", 12 * 3600)
        self.signed_url_ttl_s = _env_int("SIGNED_URL_TTL_S", 300)
        self.cors_origins = [o for o in os.environ.get("CORS_ORIGINS", "").split(",") if o]
        self.max_video_bytes = _env_int("MAX_VIDEO_BYTES", 500 * 1024 * 1024)
        self.max_logo_bytes = _env_int("MAX_LOGO_BYTES", 1024 * 1024)
        self.max_music_bytes = _env_int("MAX_MUSIC_BYTES", 25 * 1024 * 1024)
        self.max_music_seconds = _env_int("MAX_MUSIC_SECONDS", 20 * 60)
        self.max_video_seconds = _env_int("MAX_VIDEO_SECONDS", 15 * 60)
        self.event_dedup_seconds = _env_int("EVENT_DEDUP_SECONDS", 3)
        self.export_timeout_s = _env_int("EXPORT_TIMEOUT_S", 600)
        self.export_max_attempts = _env_int("EXPORT_MAX_ATTEMPTS", 3)
        self.export_stale_after_s = _env_int("EXPORT_STALE_AFTER_S", 120)
        self.max_active_exports_per_user = _env_int("MAX_ACTIVE_EXPORTS_PER_USER", 3)
        self.login_max_attempts = _env_int("LOGIN_MAX_ATTEMPTS", 8)
        self.login_window_s = _env_int("LOGIN_WINDOW_S", 300)
        self.ffmpeg_bin = os.environ.get("FFMPEG_BIN", "ffmpeg")
        self.ffprobe_bin = os.environ.get("FFPROBE_BIN", "ffprobe")
        # Agentes locais / multiarena
        self.agent_heartbeat_s = _env_int("AGENT_HEARTBEAT_S", 30)
        self.agent_sync_batch_max = _env_int("AGENT_SYNC_BATCH_MAX", 200)
        self.storage_sample_min_interval_s = _env_int("STORAGE_SAMPLE_MIN_INTERVAL_S", 60)
        # Legado: o agente enviava o MP4 à API. O novo contrato mantém o vídeo na arena; desligar em produção.
        self.allow_agent_video_upload = os.environ.get("ALLOW_AGENT_VIDEO_UPLOAD", "true").lower() in ("1", "true", "yes")
        # Entrega de vídeo por relay (o agente conecta de SAÍDA; nada de vídeo fica na central)
        self.relay_window_bytes = _env_int("RELAY_WINDOW_BYTES", 4 * 1024 * 1024)
        self.relay_timeout_s = _env_int("RELAY_TIMEOUT_S", 20)
        self.relay_poll_max_s = _env_int("RELAY_POLL_MAX_S", 25)
        self.relay_spool_dir = Path(os.environ.get("RELAY_SPOOL_DIR", str(Path(tempfile.gettempdir()) / "replay-agora-relay"))).resolve()
        self.agent_reachable_s = _env_int("AGENT_REACHABLE_S", 90)  # arena "acessível" se o agente falou nos últimos N s
        self.render_lease_s = _env_int("RENDER_LEASE_S", 600)
        self.render_max_attempts = _env_int("RENDER_MAX_ATTEMPTS", 3)
        # Credenciais do agente: rotação, assinatura de requisições e mTLS (preparação)
        self.agent_token_ttl_days = _env_int("AGENT_TOKEN_TTL_DAYS", 90)
        self.agent_rotate_before_days = _env_int("AGENT_ROTATE_BEFORE_DAYS", 7)
        self.agent_rotate_grace_s = _env_int("AGENT_ROTATE_GRACE_S", 600)           # credencial anterior ainda vale por 10 min
        self.agent_expired_rotate_days = _env_int("AGENT_EXPIRED_ROTATE_DAYS", 7)   # rotação permitida até N dias após expirar
        self.agent_sig_skew_s = _env_int("AGENT_SIG_SKEW_S", 120)
        self.agent_require_signature = os.environ.get("AGENT_REQUIRE_SIGNATURE", "false").lower() in ("1", "true", "yes")
        self.agent_mtls_mode = os.environ.get("AGENT_MTLS_MODE", "off")  # off|optional|required
        if self.agent_mtls_mode not in ("off", "optional", "required"):
            raise RuntimeError("AGENT_MTLS_MODE deve ser off, optional ou required")
        # Cabeçalho preenchido PELO PROXY REVERSO após validar o certificado de cliente (o proxy deve removê-lo se vier de fora)
        self.agent_mtls_header = os.environ.get("AGENT_MTLS_HEADER", "X-Client-Cert-Sha256").lower()
        # Redundância da API central (preparação): somente o nó 'primary' aceita escrita de agentes.
        # 'epoch' é um token de fencing: sobe a cada promoção do reserva; agentes recusam/ignoram nó com epoch menor.
        self.node_id = os.environ.get("NODE_ID", "central-1")
        self.node_role = os.environ.get("NODE_ROLE", "primary")
        if self.node_role not in ("primary", "standby"):
            raise RuntimeError("NODE_ROLE deve ser 'primary' ou 'standby'")
        self.central_epoch = _env_int("CENTRAL_EPOCH", 1)
        self.frontend_dir = Path(os.environ.get("FRONTEND_DIR", str(REPO_ROOT)))


settings = Settings()
