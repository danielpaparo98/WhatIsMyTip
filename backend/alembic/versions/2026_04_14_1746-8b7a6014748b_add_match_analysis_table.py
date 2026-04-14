"""add_match_analysis_table

Revision ID: 8b7a6014748b
Revises: a2b3c4d5e6f7
Create Date: 2026-04-14 17:46:18.902909

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8b7a6014748b'
down_revision: Union[str, Sequence[str], None] = 'a2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('match_analyses',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('game_id', sa.Integer(), nullable=False),
        sa.Column('analysis_text', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['game_id'], ['games.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_match_analyses_game_id'), 'match_analyses', ['game_id'], unique=True)
    op.create_index(op.f('ix_match_analyses_id'), 'match_analyses', ['id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_match_analyses_id'), table_name='match_analyses')
    op.drop_index(op.f('ix_match_analyses_game_id'), table_name='match_analyses')
    op.drop_table('match_analyses')
