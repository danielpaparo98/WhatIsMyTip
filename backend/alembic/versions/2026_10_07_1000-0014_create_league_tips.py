"""create_league_tips — league-generic heuristic tips (performance-per-league D3).

The multisport tables (``events`` / ``event_participants``, migration
0010) had no home for predictions: the legacy ``tips`` table is keyed to
AFL ``games`` and stores a team-name string.  This migration adds
``league_tips``, the league-generic counterpart, so that syncing a state
league can immediately generate and grade heuristic tips:

- ``event_id``                — FK to ``events`` (CASCADE, like ``tips.game_id``)
- ``heuristic``               — e.g. ``home_advantage`` / ``form`` / ``ladder``
- ``selected_participant_id`` — FK to ``event_participants``, NULLABLE on
  draw-no-pick (a heuristic that expects a draw makes no pick)
- ``competition_id``/``season_id`` — denormalized scope so per-league,
  per-season backtests do not need to join through events
- ``generated_at``            — server-defaulted generation timestamp

One row per ``(event_id, heuristic)`` via ``uq_league_tips_event_heuristic``,
which makes the generation hook's refresh an idempotent upsert.

Revision ID: 0014_create_league_tips
Revises: 0013_add_model_artifact_columns
"""

from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "0014_create_league_tips"
down_revision: Union[str, None] = "0013_add_model_artifact_columns"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_table(
        "league_tips",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("heuristic", sa.String(length=50), nullable=False),
        sa.Column("selected_participant_id", sa.Integer(), nullable=True),
        sa.Column("competition_id", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Integer(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            ondelete="CASCADE",
            name=op.f("fk_league_tips_event_id_events"),
        ),
        sa.ForeignKeyConstraint(
            ["selected_participant_id"],
            ["event_participants.id"],
            name=op.f("fk_league_tips_selected_participant_id_event_participants"),
        ),
        sa.ForeignKeyConstraint(
            ["competition_id"],
            ["competitions.id"],
            name=op.f("fk_league_tips_competition_id_competitions"),
        ),
        sa.ForeignKeyConstraint(
            ["season_id"],
            ["seasons.id"],
            name=op.f("fk_league_tips_season_id_seasons"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_league_tips")),
        sa.UniqueConstraint(
            "event_id", "heuristic", name="uq_league_tips_event_heuristic"
        ),
    )
    op.create_index(op.f("ix_league_tips_id"), "league_tips", ["id"], unique=True)
    op.create_index(
        op.f("ix_league_tips_event_id"), "league_tips", ["event_id"], unique=False
    )
    op.create_index(
        op.f("ix_league_tips_selected_participant_id"),
        "league_tips",
        ["selected_participant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_league_tips_competition_id"),
        "league_tips",
        ["competition_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_league_tips_season_id"), "league_tips", ["season_id"], unique=False
    )


def downgrade() -> None:
    # Drop in reverse order of creation; the table holds no data yet (the
    # generation hook lands with the league tips pipeline), so no
    # data-migration guard is required.
    op.drop_index(op.f("ix_league_tips_season_id"), table_name="league_tips")
    op.drop_index(op.f("ix_league_tips_competition_id"), table_name="league_tips")
    op.drop_index(
        op.f("ix_league_tips_selected_participant_id"), table_name="league_tips"
    )
    op.drop_index(op.f("ix_league_tips_event_id"), table_name="league_tips")
    op.drop_index(op.f("ix_league_tips_id"), table_name="league_tips")
    op.drop_table("league_tips")
