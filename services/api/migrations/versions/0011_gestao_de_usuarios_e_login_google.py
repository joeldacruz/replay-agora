"""Gestão de usuários (telefone, método de autenticação, último acesso, convite), identidades Google, fluxos OAuth, tokens de conta,
caixa de saída de e-mails e configurações protegidas.

Aditiva: usuários existentes ficam com auth_method='password'. Rollback: `alembic downgrade 0010`.
"""
from alembic import op
import sqlalchemy as sa


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('integration_settings',
    sa.Column('key', sa.String(length=60), nullable=False),
    sa.Column('value_enc', sa.Text(), nullable=False),
    sa.Column('updated_by', sa.String(length=32), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('oauth_flows',
    sa.Column('state', sa.String(length=64), nullable=False),
    sa.Column('app', sa.String(length=10), nullable=False),
    sa.Column('intent', sa.String(length=8), nullable=False),
    sa.Column('nonce', sa.String(length=64), nullable=False),
    sa.Column('code_verifier', sa.String(length=128), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=True),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.Column('ticket_hash', sa.String(length=64), nullable=True),
    sa.Column('ticket_user_id', sa.String(length=32), nullable=True),
    sa.Column('ticket_expires_at', sa.DateTime(), nullable=True),
    sa.Column('ticket_used_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('state')
    )
    with op.batch_alter_table('oauth_flows', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_oauth_flows_ticket_hash'), ['ticket_hash'], unique=False)

    op.create_table('outbox_mail',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('to_email', sa.String(length=254), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('account_tokens',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.Column('created_by', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    with op.batch_alter_table('account_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_account_tokens_user_id'), ['user_id'], unique=False)

    op.create_table('user_identities',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('subject', sa.String(length=64), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('last_login_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'subject'),
    sa.UniqueConstraint('user_id', 'provider')
    )
    with op.batch_alter_table('user_identities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_identities_user_id'), ['user_id'], unique=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('phone', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('auth_method', sa.String(length=10), nullable=False, server_default='password'))
        batch_op.add_column(sa.Column('last_login_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('invited_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('password_set_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('google_link_allowed', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('tokens_valid_after', sa.DateTime(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('tokens_valid_after')
        batch_op.drop_column('google_link_allowed')
        batch_op.drop_column('password_set_at')
        batch_op.drop_column('invited_at')
        batch_op.drop_column('last_login_at')
        batch_op.drop_column('auth_method')
        batch_op.drop_column('phone')

    with op.batch_alter_table('user_identities', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_identities_user_id'))

    op.drop_table('user_identities')
    with op.batch_alter_table('account_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_account_tokens_user_id'))

    op.drop_table('account_tokens')
    op.drop_table('outbox_mail')
    with op.batch_alter_table('oauth_flows', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_oauth_flows_ticket_hash'))

    op.drop_table('oauth_flows')
    op.drop_table('integration_settings')
