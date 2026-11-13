"""Publicação automática, moderação e música atribuída por replay (aditiva, sem apagar dados)."""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("videos") as b:
        b.add_column(sa.Column("published_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("moderation_state", sa.String(length=10), nullable=False, server_default="none"))
        b.add_column(sa.Column("moderation_reason", sa.String(length=200), nullable=True))
        b.add_column(sa.Column("moderated_by", sa.String(length=32), nullable=True))
        b.add_column(sa.Column("moderated_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("music_id", sa.String(length=32), nullable=True))
        b.add_column(sa.Column("music_reason", sa.String(length=60), nullable=False, server_default=""))
        b.create_index("ix_videos_music_id", ["music_id"])
        b.create_foreign_key("fk_videos_music_id", "music_tracks", ["music_id"], ["id"], ondelete="SET NULL")
    with op.batch_alter_table("arenas") as b:
        b.add_column(sa.Column("music_last_id", sa.String(length=32), nullable=True))
    # replays já visíveis à arena passam a constar como publicados (sem alterar a visibilidade de nenhum registro)
    op.execute("UPDATE videos SET published_at = created_at WHERE visibility = 'arena'")


def downgrade() -> None:
    with op.batch_alter_table("arenas") as b:
        b.drop_column("music_last_id")
    with op.batch_alter_table("videos") as b:
        b.drop_constraint("fk_videos_music_id", type_="foreignkey")
        b.drop_index("ix_videos_music_id")
        for c in ("music_reason", "music_id", "moderated_at", "moderated_by", "moderation_reason", "moderation_state", "published_at"):
            b.drop_column(c)
