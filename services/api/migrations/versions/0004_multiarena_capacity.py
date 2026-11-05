"""Multiarena: registro de agentes, telemetria por quadra, limites de armazenamento, proteção de vídeos.

Revision ID: 0004
Revises: 0003

Todas as colunas novas são NULL ou têm server_default, preservando dados existentes.
Deploy: aplicar 0004 ANTES de subir a nova versão da API. Rollback: `alembic downgrade 0003`
(descarta apenas telemetria, amostras e metadados de agentes; vídeos e jobs existentes são preservados).
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("arenas") as b:
        b.add_column(sa.Column("storage_limit_mb", sa.Integer(), nullable=True))
        b.add_column(sa.Column("storage_warn_pct", sa.Integer(), nullable=False, server_default="80"))
        b.add_column(sa.Column("storage_critical_pct", sa.Integer(), nullable=False, server_default="95"))
    with op.batch_alter_table("courts") as b:
        b.add_column(sa.Column("storage_limit_mb", sa.Integer(), nullable=True))
    with op.batch_alter_table("arena_agents") as b:
        b.add_column(sa.Column("node_id", sa.String(64), nullable=True))
        b.add_column(sa.Column("hostname", sa.String(120), nullable=True))
        b.add_column(sa.Column("registered_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("config_ack", sa.String(40), nullable=True))
        b.add_column(sa.Column("system_json", sa.Text(), nullable=True))
    with op.batch_alter_table("videos") as b:
        b.add_column(sa.Column("storage_location", sa.String(10), nullable=False, server_default="central"))
        b.add_column(sa.Column("local_id", sa.String(120), nullable=True))
        b.add_column(sa.Column("protected", sa.Boolean(), nullable=False, server_default=sa.false()))
        b.create_unique_constraint("uq_videos_arena_local_id", ["arena_id", "local_id"])
    op.create_table(
        "court_runtime",
        sa.Column("court_id", sa.String(32), sa.ForeignKey("courts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("arena_id", sa.String(32), sa.ForeignKey("arenas.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="ok"),
        sa.Column("queue_depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_render_s", sa.Float(), nullable=True),
        sa.Column("avg_render_s", sa.Float(), nullable=True),
        sa.Column("p95_render_s", sa.Float(), nullable=True),
        sa.Column("storage_used_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "storage_samples",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("arena_id", sa.String(32), sa.ForeignKey("arenas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("court_id", sa.String(32), sa.ForeignKey("courts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("used_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("disk_free_bytes", sa.BigInteger(), nullable=True),
        sa.Column("disk_total_bytes", sa.BigInteger(), nullable=True),
    )
    op.create_index("ix_samples_arena_court_ts", "storage_samples", ["arena_id", "court_id", "ts"])


def downgrade() -> None:
    op.drop_index("ix_samples_arena_court_ts", table_name="storage_samples")
    op.drop_table("storage_samples")
    op.drop_table("court_runtime")
    with op.batch_alter_table("videos") as b:
        b.drop_constraint("uq_videos_arena_local_id", type_="unique")
        b.drop_column("protected")
        b.drop_column("local_id")
        b.drop_column("storage_location")
    with op.batch_alter_table("arena_agents") as b:
        for c in ("system_json", "config_ack", "last_heartbeat_at", "registered_at", "hostname", "node_id"):
            b.drop_column(c)
    with op.batch_alter_table("courts") as b:
        b.drop_column("storage_limit_mb")
    with op.batch_alter_table("arenas") as b:
        for c in ("storage_critical_pct", "storage_warn_pct", "storage_limit_mb"):
            b.drop_column(c)
