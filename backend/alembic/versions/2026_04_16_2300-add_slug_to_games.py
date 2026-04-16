"""Add slug column to games table.

Adds a random alphanumeric slug field to games for use in public-facing URLs
instead of sequential integer IDs. This makes URLs non-guessable.

This migration:
1. Adds the slug column as nullable
2. Backfills all existing games with unique random slugs
3. Makes the column non-nullable with a unique constraint and index

Revision ID: add_slug_to_games
Revises: dedup_model_preds
Create Date: 2026-04-16 23:00:00.000000
"""

import secrets
import string

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "add_slug_to_games"
down_revision = "dedup_model_preds"
branch_labels = None
depends_on = None


def _generate_slug(length: int = 10) -> str:
    """Generate a random alphanumeric slug."""
    alphabet = string.ascii_lowercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def upgrade() -> None:
    """Add slug column, backfill existing games, then add constraints."""
    # Step 1: Add column as nullable
    op.add_column("games", sa.Column("slug", sa.String(12), nullable=True))

    # Step 2: Backfill existing games with unique slugs
    games = sa.table("games", sa.column("id", sa.Integer), sa.column("slug", sa.String))

    conn = op.get_bind()
    result = conn.execute(sa.select(games.c.id))
    existing_slugs = set()

    for row in result:
        # Generate a unique slug for each game
        while True:
            slug = _generate_slug()
            if slug not in existing_slugs:
                existing_slugs.add(slug)
                break

        conn.execute(
            sa.update(games).where(games.c.id == row[0]).values(slug=slug)
        )

    # Step 3: Make column non-nullable and add unique constraint + index
    op.alter_column("games", "slug", nullable=False)
    op.create_index("ix_games_slug", "games", ["slug"], unique=True)


def downgrade() -> None:
    """Remove slug column from games table."""
    op.drop_index("ix_games_slug", table_name="games")
    op.drop_column("games", "slug")
