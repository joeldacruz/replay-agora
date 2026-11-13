"""Endereco MAC opcional das cameras."""

from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cameras") as batch:
        batch.add_column(
            sa.Column("mac_address", sa.String(length=17), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("cameras") as batch:
        batch.drop_column("mac_address")
