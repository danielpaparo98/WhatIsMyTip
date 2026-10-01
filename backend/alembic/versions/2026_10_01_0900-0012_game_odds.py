"""game_odds — bookmaker head-to-head odds snapshots (BT-ODDS).

Backtests previously settled every tip at even money (±$10), which
assumes a constant return instead of tipping odds.  This migration adds
the ``game_odds`` table so the daily odds-sync (The Odds API) can store
a decimal price per side for upcoming games; the backtest settles games
with a snapshot at their real price and everything else at a
representative $1.90 fallback.

Revision ID: 0012_game_odds
Revises: 0011_team_identity
"""

from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "0012_game_odds"
down_revision: Union[str, None] = "0011_team_identity"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "game_odds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("home_odds", sa.Float(), nullable=True),
        sa.Column("away_odds", sa.Float(), nullable=True),
        sa.Column("bookmaker", sa.String(length=100), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["game_id"], ["games.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "source", name="uq_game_odds_game_source"),
    )
    op.create_index(op.f("ix_game_odds_id"), "game_odds", ["id"])
    op.create_index(op.f("ix_game_odds_game_id"), "game_odds", ["game_id"])
    op.create_index(op.f("ix_game_odds_source"), "game_odds", ["source"])


def downgrade() -> None:
    op.drop_index(op.f("ix_game_odds_source"), table_name="game_odds")
    op.drop_index(op.f("ix_game_odds_game_id"), table_name="game_odds")
    op.drop_index(op.f("ix_game_odds_id"), table_name="game_odds")
    op.drop_table("game_odds")
