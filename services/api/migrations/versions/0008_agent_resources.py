"""Recursos do agente por arena: FFmpeg simultâneos, aceleração por hardware e prioridade de render.

Revision ID: 0008
Revises: 0007

Aditiva (defaults conservadores: 1 FFmpeg, CPU, nice 10). Rollback: `alembic downgrade 0007`.
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("arenas") as b:
        b.add_column(sa.Column("agent_max_ffmpeg", sa.Integer(), nullable=False, server_default="1"))
        b.add_column(sa.Column("agent_hwaccel", sa.String(8), nullable=False, server_default="none"))
        b.add_column(sa.Column("agent_render_nice", sa.Integer(), nullable=False, server_default="10"))


def downgrade() -> None:
    with op.batch_alter_table("arenas") as b:
        b.drop_column("agent_render_nice")
        b.drop_column("agent_hwaccel")
        b.drop_column("agent_max_ffmpeg")
