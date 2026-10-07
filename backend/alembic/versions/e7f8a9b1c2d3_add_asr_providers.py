"""add asr_providers table and asr_provider_id to recordings

Revision ID: e7f8a9b1c2d3
Revises: c2a1f3e8b7d9
Create Date: 2026-08-14 12:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e7f8a9b1c2d3'
down_revision = 'c2a1f3e8b7d9'
branch_labels = None
depends_on = None


def upgrade():
    # 创建 asr_providers 表
    op.create_table(
        'asr_providers',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('base_url', sa.String(500), nullable=False),
        sa.Column('auth_header', sa.String(100), server_default='', nullable=False),
        sa.Column('auth_value', sa.String(500), server_default='', nullable=False),
        sa.Column('timeout', sa.Integer, server_default='600', nullable=False),
        sa.Column('supports_speaker', sa.Boolean, server_default='1', nullable=False),
        sa.Column('supports_hotwords', sa.Boolean, server_default='1', nullable=False),
        sa.Column('is_default', sa.Boolean, server_default='0', nullable=False),
        sa.Column('is_enabled', sa.Boolean, server_default='1', nullable=False),
        sa.Column('sort_order', sa.Integer, server_default='0', nullable=False),
        sa.Column('description', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    )

    # 为 recordings 表添加 asr_provider_id 字段（SQLite 需要 batch 模式）
    with op.batch_alter_table('recordings') as batch_op:
        batch_op.add_column(sa.Column('asr_provider_id', sa.Integer, sa.ForeignKey('asr_providers.id', ondelete='SET NULL'), nullable=True))

    # 创建索引
    op.create_index('ix_asr_providers_name', 'asr_providers', ['name'], unique=True)


def downgrade():
    op.drop_index('ix_asr_providers_name', table_name='asr_providers')
    with op.batch_alter_table('recordings') as batch_op:
        batch_op.execute(sa.text("UPDATE recordings SET asr_provider_id = NULL WHERE asr_provider_id IS NOT NULL"))
        batch_op.drop_column('asr_provider_id')
    op.drop_table('asr_providers')
