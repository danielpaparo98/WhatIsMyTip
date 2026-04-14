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
    # NOTE: last_synced_at and sync_version columns are already added by
    # migration 9a1b2c3d4e5f (add_cron_job_tables). This migration is a
    # no-op to preserve the revision chain.
    pass


def downgrade() -> None:
    """Downgrade schema."""
    # NOTE: last_synced_at and sync_version columns are dropped by the
    # downgrade of migration 9a1b2c3d4e5f. This is a no-op.
    pass
