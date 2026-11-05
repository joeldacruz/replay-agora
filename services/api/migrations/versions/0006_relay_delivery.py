"""Entrega de vídeo por relay: pedidos de trecho ao agente e estado de disponibilidade do arquivo.

Revision ID: 0006
Revises: 0005

Aditiva. `videos.media_state` default 'ok'. Rollback: `alembic downgrade 0005` (descarta apenas pedidos transitórios).
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("videos") as b:
        b.add_column(sa.Column("media_state", sa.String(12), nullable=False, server_default="ok"))
    op.create_table(
        "relay_requests",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("arena_id", sa.String(32), sa.ForeignKey("arenas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.String(32), nullable=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("ref", sa.String(120), nullable=False),
        sa.Column("start", sa.BigInteger(), nullable=False),
        sa.Column("end", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("error", sa.String(200), nullable=True),
        sa.Column("total_size", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("done_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_relay_arena_status", "relay_requests", ["arena_id", "status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_relay_arena_status", table_name="relay_requests")
    op.drop_table("relay_requests")
    with op.batch_alter_table("videos") as b:
        b.drop_column("media_state")
