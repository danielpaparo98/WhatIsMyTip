"""Consolidated PostgreSQL schema — all tables from scratch

Revision ID: 0001_consolidated
Revises:
Create Date: 2026-05-28 16:13:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0001_consolidated"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all tables for a fresh PostgreSQL database."""

    # ------------------------------------------------------------------
    # 1. games
    #    No foreign‑key dependencies.  All timestamp columns use
    #    TIMESTAMP WITH TIME ZONE via DateTime(timezone=True).
    # ------------------------------------------------------------------
    op.create_table(
        "games",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("squiggle_id", sa.Integer(), nullable=True),
        sa.Column("round_id", sa.Integer(), nullable=True),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("home_team", sa.Text(), nullable=True),
        sa.Column("away_team", sa.Text(), nullable=True),
        sa.Column("home_score", sa.Integer(), nullable=True),
        sa.Column("away_score", sa.Integer(), nullable=True),
        sa.Column("venue", sa.Text(), nullable=True),
        sa.Column("date", sa.DateTime(), nullable=True),
        sa.Column("completed", sa.Boolean(), nullable=True, server_default=sa.text("false")),
        sa.Column("predictions_generated", sa.Boolean(), nullable=True, server_default=sa.text("false")),
        sa.Column("tips_generated", sa.Boolean(), nullable=True, server_default=sa.text("false")),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_version", sa.Integer(), nullable=True, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_games_slug"),
        sa.UniqueConstraint("squiggle_id", name="uq_games_squiggle_id"),
    )
    op.create_index("ix_games_id", "games", ["id"], unique=False)
    op.create_index("ix_games_slug", "games", ["slug"], unique=True)
    op.create_index("ix_games_squiggle_id", "games", ["squiggle_id"], unique=True)
    op.create_index("ix_games_round_id", "games", ["round_id"], unique=False)
    op.create_index("ix_games_season", "games", ["season"], unique=False)
    op.create_index("ix_games_predictions_generated", "games", ["predictions_generated"], unique=False)
    op.create_index("ix_games_tips_generated", "games", ["tips_generated"], unique=False)

    # ------------------------------------------------------------------
    # 2. tips
    #    FK → games.id with ON DELETE CASCADE.
    # ------------------------------------------------------------------
    op.create_table(
        "tips",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=True),
        sa.Column("heuristic", sa.Text(), nullable=True),
        sa.Column("selected_team", sa.Text(), nullable=True),
        sa.Column("margin", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "heuristic", name="uq_game_heuristic"),
    )
    op.create_index("ix_tips_id", "tips", ["id"], unique=False)
    op.create_index("ix_tips_game_id", "tips", ["game_id"], unique=False)
    op.create_index("ix_tips_heuristic", "tips", ["heuristic"], unique=False)

    # ------------------------------------------------------------------
    # 3. model_predictions
    #    FK → games.id with ON DELETE CASCADE.
    # ------------------------------------------------------------------
    op.create_table(
        "model_predictions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=True),
        sa.Column("model_name", sa.Text(), nullable=True),
        sa.Column("winner", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("margin", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "model_name", name="uq_game_model"),
    )
    op.create_index("ix_model_predictions_id", "model_predictions", ["id"], unique=False)
    op.create_index("ix_model_predictions_game_id", "model_predictions", ["game_id"], unique=False)
    op.create_index("ix_model_predictions_model_name", "model_predictions", ["model_name"], unique=False)

    # ------------------------------------------------------------------
    # 4. backtest_results
    #    No FK dependencies.
    # ------------------------------------------------------------------
    op.create_table(
        "backtest_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("heuristic", sa.Text(), nullable=True),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("round_id", sa.Integer(), nullable=True),
        sa.Column("tips_made", sa.Integer(), nullable=True),
        sa.Column("tips_correct", sa.Integer(), nullable=True),
        sa.Column("accuracy", sa.Float(), nullable=True),
        sa.Column("profit", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "season", "round_id", "heuristic",
            name="uq_backtest_season_round_heuristic",
        ),
    )
    op.create_index("ix_backtest_results_id", "backtest_results", ["id"], unique=False)
    op.create_index("ix_backtest_results_heuristic", "backtest_results", ["heuristic"], unique=False)

    # ------------------------------------------------------------------
    # 5. generation_progress
    #    No FK dependencies.
    # ------------------------------------------------------------------
    op.create_table(
        "generation_progress",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_type", sa.Text(), nullable=True),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("total_items", sa.Integer(), nullable=True, server_default=sa.text("0")),
        sa.Column("completed_items", sa.Integer(), nullable=True, server_default=sa.text("0")),
        sa.Column("status", sa.Text(), nullable=True, server_default=sa.text("'pending'")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("job_execution_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_generation_progress_id", "generation_progress", ["id"], unique=False)
    op.create_index("ix_generation_progress_operation_type", "generation_progress", ["operation_type"], unique=False)
    op.create_index("ix_generation_progress_season", "generation_progress", ["season"], unique=False)
    op.create_index("ix_generation_progress_job_execution_id", "generation_progress", ["job_execution_id"], unique=False)

    # ------------------------------------------------------------------
    # 6. job_executions
    #    No FK dependencies.
    # ------------------------------------------------------------------
    op.create_table(
        "job_executions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_name", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("items_processed", sa.Integer(), nullable=True),
        sa.Column("items_failed", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_executions_id", "job_executions", ["id"], unique=False)
    op.create_index("ix_job_executions_job_name", "job_executions", ["job_name"], unique=False)

    # ------------------------------------------------------------------
    # 7. job_locks
    #    No FK dependencies.
    # ------------------------------------------------------------------
    op.create_table(
        "job_locks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_name", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_name", name="uq_job_locks_job_name"),
    )
    op.create_index("ix_job_locks_id", "job_locks", ["id"], unique=False)

    # ------------------------------------------------------------------
    # 8. elo_cache
    #    No FK dependencies.
    # ------------------------------------------------------------------
    op.create_table(
        "elo_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("team_name", sa.Text(), nullable=True),
        sa.Column("rating", sa.Float(), nullable=False),
        sa.Column("games_played", sa.Integer(), nullable=True, server_default=sa.text("0")),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_name", name="uq_elo_cache_team_name"),
    )
    op.create_index("ix_elo_cache_id", "elo_cache", ["id"], unique=False)

    # ------------------------------------------------------------------
    # 9. match_analyses
    #    FK → games.id with ON DELETE CASCADE.
    #    One‑to‑one with games (unique constraint on game_id).
    # ------------------------------------------------------------------
    op.create_table(
        "match_analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("analysis_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", name="uq_match_analyses_game_id"),
    )
    op.create_index("ix_match_analyses_id", "match_analyses", ["id"], unique=False)
    op.create_index("ix_match_analyses_game_id", "match_analyses", ["game_id"], unique=True)


def downgrade() -> None:
    """Drop all tables in reverse dependency order."""
    op.drop_table("match_analyses")
    op.drop_table("elo_cache")
    op.drop_table("job_locks")
    op.drop_table("job_executions")
    op.drop_table("generation_progress")
    op.drop_table("backtest_results")
    op.drop_table("model_predictions")
    op.drop_table("tips")
    op.drop_table("games")
