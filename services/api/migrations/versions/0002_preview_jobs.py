"""Separa pre-visualizacao patrocinada de exportacao do atleta.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("export_jobs") as batch_op:
        batch_op.add_column(
            sa.Column("purpose", sa.String(length=12), nullable=False, server_default="export")
        )


def downgrade() -> None:
    with op.batch_alter_table("export_jobs") as batch_op:
        batch_op.drop_column("purpose")
