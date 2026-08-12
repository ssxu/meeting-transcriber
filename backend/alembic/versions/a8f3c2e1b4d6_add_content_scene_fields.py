"""add content category and sub_type to recordings and meeting_types

Revision ID: a8f3c2e1b4d6
Revises: 565d72bc9b59
Create Date: 2026-07-25 08:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a8f3c2e1b4d6'
down_revision = '565d72bc9b59'
branch_labels = None
depends_on = None


def upgrade():
    # meeting_types 表新增字段
    op.add_column('meeting_types', sa.Column('category', sa.String(50), server_default='meeting', nullable=False))
    op.add_column('meeting_types', sa.Column('sub_type', sa.String(50), server_default='regular', nullable=False))

    # recordings 表新增字段
    op.add_column('recordings', sa.Column('content_category', sa.String(50), nullable=True))
    op.add_column('recordings', sa.Column('content_sub_type', sa.String(50), nullable=True))


def downgrade():
    op.drop_column('recordings', 'content_sub_type')
    op.drop_column('recordings', 'content_category')
    op.drop_column('meeting_types', 'sub_type')
    op.drop_column('meeting_types', 'category')
