"""Add weather, players, and injury data tables

Revision ID: 0002_weather_players_injuries
Revises: 0001_consolidated
Create Date: 2026-06-10 06:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = "0002_weather_players_injuries"
down_revision: Union[str, Sequence[str], None] = "0001_consolidated"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add afltables_match_id to games, plus 5 new tables."""

    # ------------------------------------------------------------------
    # 0. Add afltables_match_id column to existing games table
    # ------------------------------------------------------------------
    op.add_column(
        "games",
        sa.Column("afltables_match_id", sa.Text(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_games_afltables_match_id", "games", ["afltables_match_id"]
    )
    op.create_index(
        "ix_games_afltables_match_id", "games", ["afltables_match_id"], unique=True
    )

    # ------------------------------------------------------------------
    # 1. match_weather
    #    FK → games.id with ON DELETE CASCADE. One-to-one with games.
    # ------------------------------------------------------------------
    op.create_table(
        "match_weather",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("venue", sa.Text(), nullable=True),
        sa.Column("match_date", sa.Date(), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("precipitation", sa.Float(), nullable=True),
        sa.Column("wind_speed", sa.Float(), nullable=True),
        sa.Column("wind_direction", sa.Integer(), nullable=True),
        sa.Column("wind_gusts", sa.Float(), nullable=True),
        sa.Column("humidity", sa.Integer(), nullable=True),
        sa.Column("weather_code", sa.Integer(), nullable=True),
        sa.Column(
            "data_type",
            sa.Text(),
            nullable=True,
            server_default=sa.text("'historical'"),
        ),
        sa.Column("raw_hourly", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", name="uq_match_weather_game_id"),
    )
    op.create_index("ix_match_weather_id", "match_weather", ["id"], unique=False)
    op.create_index(
        "ix_match_weather_game_id", "match_weather", ["game_id"], unique=True
    )
    op.create_index("ix_match_weather_venue", "match_weather", ["venue"], unique=False)
    op.create_index(
        "ix_match_weather_data_type", "match_weather", ["data_type"], unique=False
    )

    # ------------------------------------------------------------------
    # 2. players
    #    No FK dependencies. Master player registry.
    # ------------------------------------------------------------------
    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("afltables_id", sa.Text(), nullable=True),
        sa.Column("footywire_id", sa.Integer(), nullable=True),
        sa.Column("current_team", sa.Text(), nullable=True),
        sa.Column("position", sa.Text(), nullable=True),
        sa.Column("height", sa.Text(), nullable=True),
        sa.Column("weight", sa.Text(), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("draft_info", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_players_name"),
        sa.UniqueConstraint("afltables_id", name="uq_players_afltables_id"),
    )
    op.create_index("ix_players_id", "players", ["id"], unique=False)
    op.create_index("ix_players_name", "players", ["name"], unique=True)
    op.create_index(
        "ix_players_afltables_id", "players", ["afltables_id"], unique=True
    )
    op.create_index(
        "ix_players_footywire_id", "players", ["footywire_id"], unique=False
    )
    op.create_index(
        "ix_players_current_team", "players", ["current_team"], unique=False
    )

    # ------------------------------------------------------------------
    # 3. player_match_stats
    #    FK → games.id, FK → players.id with ON DELETE CASCADE.
    # ------------------------------------------------------------------
    op.create_table(
        "player_match_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("team", sa.Text(), nullable=True),
        sa.Column(
            "kicks", sa.Integer(), nullable=True, server_default=sa.text("0")
        ),
        sa.Column(
            "handballs", sa.Integer(), nullable=True, server_default=sa.text("0")
        ),
        sa.Column(
            "disposals", sa.Integer(), nullable=True, server_default=sa.text("0")
        ),
        sa.Column(
            "marks", sa.Integer(), nullable=True, server_default=sa.text("0")
        ),
        sa.Column(
            "goals", sa.Integer(), nullable=True, server_default=sa.text("0")
        ),
        sa.Column(
            "behinds", sa.Integer(), nullable=True, server_default=sa.text("0")
        ),
        sa.Column(
            "tackles", sa.Integer(), nullable=True, server_default=sa.text("0")
        ),
        sa.Column(
            "hitouts", sa.Integer(), nullable=True, server_default=sa.text("0")
        ),
        sa.Column(
            "frees_for", sa.Integer(), nullable=True, server_default=sa.text("0")
        ),
        sa.Column(
            "frees_against", sa.Integer(), nullable=True, server_default=sa.text("0")
        ),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "player_id", name="uq_pms_game_player"),
    )
    op.create_index(
        "ix_player_match_stats_id", "player_match_stats", ["id"], unique=False
    )
    op.create_index(
        "ix_player_match_stats_game_id",
        "player_match_stats",
        ["game_id"],
        unique=False,
    )
    op.create_index(
        "ix_player_match_stats_player_id",
        "player_match_stats",
        ["player_id"],
        unique=False,
    )
    op.create_index(
        "ix_player_match_stats_team", "player_match_stats", ["team"], unique=False
    )

    # ------------------------------------------------------------------
    # 4. player_advanced_stats
    #    FK → games.id, FK → players.id with ON DELETE CASCADE.
    # ------------------------------------------------------------------
    op.create_table(
        "player_advanced_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("round_label", sa.Text(), nullable=True),
        sa.Column("opponent", sa.Text(), nullable=True),
        sa.Column("tog_pct", sa.Float(), nullable=True),
        sa.Column("metres_gained", sa.Integer(), nullable=True),
        sa.Column("score_involvements", sa.Integer(), nullable=True),
        sa.Column("contested_possessions", sa.Integer(), nullable=True),
        sa.Column("pressure_acts", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "player_id", name="uq_pas_game_player"),
    )
    op.create_index(
        "ix_player_advanced_stats_id", "player_advanced_stats", ["id"], unique=False
    )
    op.create_index(
        "ix_player_advanced_stats_game_id",
        "player_advanced_stats",
        ["game_id"],
        unique=False,
    )
    op.create_index(
        "ix_player_advanced_stats_player_id",
        "player_advanced_stats",
        ["player_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # 5. injuries
    #    FK → players.id with ON DELETE CASCADE.
    # ------------------------------------------------------------------
    op.create_table(
        "injuries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=True),
        sa.Column("player_name", sa.Text(), nullable=False),
        sa.Column("team", sa.Text(), nullable=True),
        sa.Column("injury_type", sa.Text(), nullable=True),
        sa.Column("return_timeline", sa.Text(), nullable=True),
        sa.Column(
            "source",
            sa.Text(),
            nullable=True,
            server_default=sa.text("'footywire'"),
        ),
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "player_name", "injury_type", name="uq_injuries_player_injury"
        ),
    )
    op.create_index("ix_injuries_id", "injuries", ["id"], unique=False)
    op.create_index("ix_injuries_player_id", "injuries", ["player_id"], unique=False)
    op.create_index("ix_injuries_team", "injuries", ["team"], unique=False)
    op.create_index(
        "ix_injuries_scraped_at", "injuries", ["scraped_at"], unique=False
    )


def downgrade() -> None:
    """Drop new tables and column in reverse dependency order."""
    op.drop_table("injuries")
    op.drop_table("player_advanced_stats")
    op.drop_table("player_match_stats")
    op.drop_table("players")
    op.drop_table("match_weather")
    op.drop_index("ix_games_afltables_match_id", table_name="games")
    op.drop_constraint("uq_games_afltables_match_id", "games", type_="unique")
    op.drop_column("games", "afltables_match_id")
