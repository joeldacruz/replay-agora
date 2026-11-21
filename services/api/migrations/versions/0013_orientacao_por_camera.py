"""Orientacao da imagem por camera e por replay.

Revision ID: 0013
Revises: 0012
"""

from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "cameras",
        sa.Column(
            "rotation_deg",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    op.add_column(
        "videos",
        sa.Column(
            "rotation_deg",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )



    op.add_column(
        "cameras",
        sa.Column(
            "allow_player_rotation",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

def downgrade():
    with op.batch_alter_table("videos") as batch:
        batch.drop_column("rotation_deg")

    with op.batch_alter_table("cameras") as batch:
        batch.drop_column("allow_player_rotation")
        batch.drop_column("rotation_deg")
