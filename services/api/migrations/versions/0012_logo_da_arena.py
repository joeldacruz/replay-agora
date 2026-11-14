"""Logo própria da arena (separada das logos de patrocinadores)."""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

def upgrade() -> None:
    with op.batch_alter_table("arenas") as batch:
        batch.add_column(sa.Column("logo_key", sa.String(length=300), nullable=True))

def downgrade() -> None:
    with op.batch_alter_table("arenas") as batch:
        batch.drop_column("logo_key")
