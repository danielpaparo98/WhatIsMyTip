"""add_sync_tracking_to_games

Revision ID: ef5dc0ca76d2
Revises: 9a1b2c3d4e5f
Create Date: 2026-04-02 21:43:11.465776

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ef5dc0ca76d2'
down_revision: Union[str, Sequence[str], None] = '9a1b2c3d4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add sync tracking columns to games table
    op.add_column('games', sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('games', sa.Column('sync_version', sa.Integer(), nullable=True, server_default='0'))


def downgrade() -> None:
    """Downgrade schema."""
    # Remove sync tracking columns from games table
    op.drop_column('games', 'sync_version')
    op.drop_column('games', 'last_synced_at')
