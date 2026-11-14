"""Cache persistente e deduplicado de prévias patrocinadas.

Revision ID: 0003
Revises: 0002

O índice UNIQUE permite diversos NULL (exportações tradicionais e prévias antigas),
mas garante no máximo um job por chave de cache nova.
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("export_jobs") as batch_op:
        batch_op.add_column(sa.Column("preview_cache_key", sa.String(length=64), nullable=True))
    op.create_index("ix_exports_preview_cache_key", "export_jobs", ["preview_cache_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_exports_preview_cache_key", table_name="export_jobs")
    with op.batch_alter_table("export_jobs") as batch_op:
        batch_op.drop_column("preview_cache_key")
