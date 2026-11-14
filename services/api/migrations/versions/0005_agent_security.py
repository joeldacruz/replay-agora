"""Segurança do agente: credenciais rotativas, assinatura de requisições (HMAC), nonces e fingerprint mTLS.

Revision ID: 0005
Revises: 0004

Aditiva: colunas NULL; agentes legados continuam válidos (sem expiração, sem assinatura) até rotacionarem.
Deploy: aplicar 0005 antes da API. Rollback: `alembic downgrade 0004` (perde apenas chaves/nonces; tokens atuais seguem válidos).
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("arena_agents") as b:
        b.add_column(sa.Column("prev_token_hash", sa.String(64), nullable=True))
        b.add_column(sa.Column("prev_valid_until", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("prev_sig_key", sa.String(64), nullable=True))
        b.add_column(sa.Column("token_rotated_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("token_expires_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("sig_key", sa.String(64), nullable=True))
        b.add_column(sa.Column("cert_fingerprint", sa.String(64), nullable=True))
    op.create_table(
        "agent_nonces",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("agent_id", sa.String(32), sa.ForeignKey("arena_agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("nonce", sa.String(64), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("agent_id", "nonce"),
    )
    op.create_index("ix_nonces_ts", "agent_nonces", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_nonces_ts", table_name="agent_nonces")
    op.drop_table("agent_nonces")
    with op.batch_alter_table("arena_agents") as b:
        for c in ("cert_fingerprint", "sig_key", "token_expires_at", "token_rotated_at", "prev_sig_key", "prev_valid_until", "prev_token_hash"):
            b.drop_column(c)
