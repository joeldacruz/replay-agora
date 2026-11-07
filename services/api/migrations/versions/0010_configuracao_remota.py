"""Configuração remota: servidores locais, câmeras (senha cifrada), controladores, molduras versionadas, revisões e tarefas.

Revision ID: 0010
Revises: 0009

Aditiva (exceto export_jobs.frame_orientation com default). Rollback: `alembic downgrade 0009` (descarta a configuração remota).
"""
from alembic import op
import sqlalchemy as sa


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('frames',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('arena_id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.String(length=300), nullable=False),
    sa.Column('orientation', sa.String(length=12), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('court_id', sa.String(length=32), nullable=True),
    sa.Column('available', sa.Boolean(), nullable=False),
    sa.Column('current_version_id', sa.String(length=32), nullable=True),
    sa.Column('created_by', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('deleted_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['arena_id'], ['arenas.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['court_id'], ['courts.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('arena_id', 'name')
    )
    with op.batch_alter_table('frames', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_frames_arena_id'), ['arena_id'], unique=False)

    op.create_table('local_servers',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('arena_id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.String(length=300), nullable=False),
    sa.Column('agent_id', sa.String(length=32), nullable=True),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('created_by', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.Column('last_state', sa.String(length=12), nullable=True),
    sa.Column('applied_version', sa.Integer(), nullable=False),
    sa.Column('inventory_json', sa.Text(), nullable=True),
    sa.Column('inventory_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['agent_id'], ['arena_agents.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['arena_id'], ['arenas.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('agent_id'),
    sa.UniqueConstraint('arena_id', 'name')
    )
    with op.batch_alter_table('local_servers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_local_servers_arena_id'), ['arena_id'], unique=False)

    op.create_table('agent_tasks',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('arena_id', sa.String(length=32), nullable=False),
    sa.Column('server_id', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('params_json', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('created_by', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('delivered_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('result_json', sa.Text(), nullable=True),
    sa.Column('error', sa.String(length=300), nullable=True),
    sa.ForeignKeyConstraint(['server_id'], ['local_servers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('agent_tasks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_agent_tasks_arena_id'), ['arena_id'], unique=False)
        batch_op.create_index('ix_task_server_status', ['server_id', 'status', 'created_at'], unique=False)

    op.create_table('cameras',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('arena_id', sa.String(length=32), nullable=False),
    sa.Column('court_id', sa.String(length=32), nullable=True),
    sa.Column('server_id', sa.String(length=32), nullable=True),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('vendor', sa.String(length=40), nullable=False),
    sa.Column('model', sa.String(length=60), nullable=False),
    sa.Column('host', sa.String(length=253), nullable=False),
    sa.Column('rtsp_port', sa.Integer(), nullable=False),
    sa.Column('transport', sa.String(length=3), nullable=False),
    sa.Column('rtsp_path', sa.String(length=200), nullable=False),
    sa.Column('username', sa.String(length=80), nullable=False),
    sa.Column('secret_enc', sa.Text(), nullable=True),
    sa.Column('secret_version', sa.Integer(), nullable=False),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.Column('fps', sa.Float(), nullable=True),
    sa.Column('codec', sa.String(length=10), nullable=False),
    sa.Column('profile', sa.String(length=40), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('is_primary', sa.Boolean(), nullable=False),
    sa.Column('row_version', sa.Integer(), nullable=False),
    sa.Column('last_test_json', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['arena_id'], ['arenas.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['court_id'], ['courts.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['server_id'], ['local_servers.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('cameras', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_cameras_arena_id'), ['arena_id'], unique=False)

    op.create_table('config_revisions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('arena_id', sa.String(length=32), nullable=False),
    sa.Column('server_id', sa.String(length=32), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('snapshot_json', sa.Text(), nullable=False),
    sa.Column('base_version', sa.Integer(), nullable=False),
    sa.Column('allow_restart', sa.Boolean(), nullable=False),
    sa.Column('note', sa.String(length=200), nullable=False),
    sa.Column('reverted_from', sa.Integer(), nullable=True),
    sa.Column('created_by', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.Column('received_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('result_json', sa.Text(), nullable=True),
    sa.Column('error', sa.String(length=400), nullable=True),
    sa.ForeignKeyConstraint(['server_id'], ['local_servers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('server_id', 'version')
    )
    with op.batch_alter_table('config_revisions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_config_revisions_arena_id'), ['arena_id'], unique=False)
        batch_op.create_index('ix_rev_server_status', ['server_id', 'status'], unique=False)

    op.create_table('controllers',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('arena_id', sa.String(length=32), nullable=False),
    sa.Column('server_id', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('port_id', sa.String(length=160), nullable=False),
    sa.Column('baud', sa.Integer(), nullable=False),
    sa.Column('address', sa.String(length=253), nullable=False),
    sa.Column('inputs_capacity', sa.Integer(), nullable=False),
    sa.Column('debounce_s', sa.Float(), nullable=False),
    sa.Column('cooldown_s', sa.Float(), nullable=False),
    sa.Column('min_interval_s', sa.Float(), nullable=False),
    sa.Column('reconnect_behavior', sa.String(length=20), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('row_version', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['arena_id'], ['arenas.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['server_id'], ['local_servers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('server_id', 'name')
    )
    with op.batch_alter_table('controllers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_controllers_arena_id'), ['arena_id'], unique=False)

    op.create_table('court_settings',
    sa.Column('court_id', sa.String(length=32), nullable=False),
    sa.Column('arena_id', sa.String(length=32), nullable=False),
    sa.Column('server_id', sa.String(length=32), nullable=True),
    sa.Column('pre_s', sa.Float(), nullable=False),
    sa.Column('post_s', sa.Float(), nullable=False),
    sa.Column('close_countdown_s', sa.Float(), nullable=False),
    sa.Column('buffer_s', sa.Float(), nullable=False),
    sa.Column('segment_s', sa.Float(), nullable=False),
    sa.Column('storage_limit_mb', sa.Integer(), nullable=True),
    sa.Column('retention_days', sa.Integer(), nullable=False),
    sa.Column('audio_mode', sa.String(length=10), nullable=False),
    sa.Column('export_formats', sa.String(length=40), nullable=False),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('reconnect_min_s', sa.Float(), nullable=False),
    sa.Column('reconnect_max_s', sa.Float(), nullable=False),
    sa.Column('frame_h_id', sa.String(length=32), nullable=True),
    sa.Column('frame_v_id', sa.String(length=32), nullable=True),
    sa.Column('frames_enabled', sa.Boolean(), nullable=False),
    sa.Column('out_h', sa.String(length=12), nullable=False),
    sa.Column('out_v', sa.String(length=12), nullable=False),
    sa.Column('row_version', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['court_id'], ['courts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['server_id'], ['local_servers.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('court_id')
    )
    with op.batch_alter_table('court_settings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_court_settings_arena_id'), ['arena_id'], unique=False)

    op.create_table('frame_versions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('frame_id', sa.String(length=32), nullable=False),
    sa.Column('arena_id', sa.String(length=32), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('storage_key', sa.String(length=300), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('bytes', sa.Integer(), nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('window_json', sa.Text(), nullable=False),
    sa.Column('layout_json', sa.Text(), nullable=False),
    sa.Column('note', sa.String(length=200), nullable=False),
    sa.Column('created_by', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['frame_id'], ['frames.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('frame_id', 'version')
    )
    with op.batch_alter_table('frame_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_frame_versions_arena_id'), ['arena_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_frame_versions_frame_id'), ['frame_id'], unique=False)

    op.create_table('server_events',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('server_id', sa.String(length=32), nullable=False),
    sa.Column('arena_id', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('detail', sa.String(length=300), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['server_id'], ['local_servers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('server_events', schema=None) as batch_op:
        batch_op.create_index('ix_srvev_server', ['server_id', 'created_at'], unique=False)

    op.create_table('controller_inputs',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('controller_id', sa.String(length=32), nullable=False),
    sa.Column('arena_id', sa.String(length=32), nullable=False),
    sa.Column('input_ref', sa.String(length=40), nullable=False),
    sa.Column('label', sa.String(length=60), nullable=False),
    sa.Column('court_id', sa.String(length=32), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('last_event_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['controller_id'], ['controllers.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['court_id'], ['courts.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('controller_id', 'input_ref')
    )
    with op.batch_alter_table('controller_inputs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_controller_inputs_arena_id'), ['arena_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_controller_inputs_controller_id'), ['controller_id'], unique=False)

    with op.batch_alter_table('export_jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('frame_orientation', sa.String(length=12), nullable=False, server_default='horizontal'))



def downgrade() -> None:
    with op.batch_alter_table('export_jobs', schema=None) as batch_op:
        batch_op.drop_column('frame_orientation')

    with op.batch_alter_table('controller_inputs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_controller_inputs_controller_id'))
        batch_op.drop_index(batch_op.f('ix_controller_inputs_arena_id'))

    op.drop_table('controller_inputs')
    with op.batch_alter_table('server_events', schema=None) as batch_op:
        batch_op.drop_index('ix_srvev_server')

    op.drop_table('server_events')
    with op.batch_alter_table('frame_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_frame_versions_frame_id'))
        batch_op.drop_index(batch_op.f('ix_frame_versions_arena_id'))

    op.drop_table('frame_versions')
    with op.batch_alter_table('court_settings', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_court_settings_arena_id'))

    op.drop_table('court_settings')
    with op.batch_alter_table('controllers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_controllers_arena_id'))

    op.drop_table('controllers')
    with op.batch_alter_table('config_revisions', schema=None) as batch_op:
        batch_op.drop_index('ix_rev_server_status')
        batch_op.drop_index(batch_op.f('ix_config_revisions_arena_id'))

    op.drop_table('config_revisions')
    with op.batch_alter_table('cameras', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_cameras_arena_id'))

    op.drop_table('cameras')
    with op.batch_alter_table('agent_tasks', schema=None) as batch_op:
        batch_op.drop_index('ix_task_server_status')
        batch_op.drop_index(batch_op.f('ix_agent_tasks_arena_id'))

    op.drop_table('agent_tasks')
    with op.batch_alter_table('local_servers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_local_servers_arena_id'))

    op.drop_table('local_servers')
    with op.batch_alter_table('frames', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_frames_arena_id'))

    op.drop_table('frames')
