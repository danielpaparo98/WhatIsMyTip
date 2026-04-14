"""clear_existing_explanations

Revision ID: a2b3c4d5e6f7
Revises: ef5dc0ca76d2
Create Date: 2026-04-14 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, Sequence[str], None] = 'ef5dc0ca76d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Clear all existing explanation data from the tips table.
    
    This ensures that stale/empty explanations are cleared so the
    new explanation generation pipeline can populate them fresh.
    """
    op.execute("UPDATE tips SET explanation = '' WHERE explanation IS NOT NULL AND explanation != ''")


def downgrade() -> None:
    """No downgrade needed - we can't recover the cleared explanations."""
    pass
