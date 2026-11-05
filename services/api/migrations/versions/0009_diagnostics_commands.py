"""Diagnóstico por quadra e comandos do painel para o agente (teste manual de acionamento).

Revision ID: 0009
Revises: 0008

Aditiva. Rollback: `alembic downgrade 0008` (descarta diagnósticos e comandos transitórios).
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("court_runtime") as b:
        b.add_column(sa.Column("diag_json", sa.Text(), nullable=True))
    op.create_table(
        "agent_commands",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("arena_id", sa.String(32), sa.ForeignKey("arenas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("court_id", sa.String(32), sa.ForeignKey("courts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("court_key", sa.String(60), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="test_trigger"),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("created_by", sa.String(32), nullable=False),
        sa.Column("agent_id", sa.String(32), nullable=True),
        sa.Column("outcome", sa.String(30), nullable=True),
        sa.Column("job_id", sa.String(120), nullable=True),
        sa.Column("detail", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_cmd_arena_status", "agent_commands", ["arena_id", "status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_cmd_arena_status", table_name="agent_commands")
    op.drop_table("agent_commands")
    with op.batch_alter_table("court_runtime") as b:
        b.drop_column("diag_json")
