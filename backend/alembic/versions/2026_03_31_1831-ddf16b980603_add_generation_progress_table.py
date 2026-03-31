"""add_generation_progress_table

Revision ID: ddf16b980603
Revises: b2c3d4e5f6a7
Create Date: 2026-03-31 18:31:18.254007

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ddf16b980603'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create generation_progress table
    op.create_table('generation_progress',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('operation_type', sa.String(length=50), nullable=True),
    sa.Column('season', sa.Integer(), nullable=True),
    sa.Column('total_items', sa.Integer(), nullable=True),
    sa.Column('completed_items', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_generation_progress_id'), 'generation_progress', ['id'], unique=False)
    op.create_index(op.f('ix_generation_progress_operation_type'), 'generation_progress', ['operation_type'], unique=False)
    op.create_index(op.f('ix_generation_progress_season'), 'generation_progress', ['season'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_generation_progress_season'), table_name='generation_progress')
    op.drop_index(op.f('ix_generation_progress_operation_type'), table_name='generation_progress')
    op.drop_index(op.f('ix_generation_progress_id'), table_name='generation_progress')
    op.drop_table('generation_progress')
