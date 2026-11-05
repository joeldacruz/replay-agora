"""Renderização no agente: local do render, concessão (lease) e hash da saída.

Revision ID: 0007
Revises: 0006

Aditiva; jobs existentes ficam 'central' (continuam no worker da API). Rollback: `alembic downgrade 0006`
(jobs 'agent' em andamento ficam sem render_location e seriam reprocessados pelo worker central se o arquivo existir).
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("export_jobs") as b:
        b.add_column(sa.Column("render_location", sa.String(8), nullable=False, server_default="central"))
        b.add_column(sa.Column("lease_until", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("output_sha256", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("export_jobs") as b:
        b.drop_column("output_sha256")
        b.drop_column("lease_until")
        b.drop_column("render_location")
