"""league AFL-parity storage — model predictions, explanations,
match analyses, event weather (PARITY-1).

Revision ID: 0017_league_afl_parity_storage
Revises: 0016_league_tips_confidence_margin
Create Date: 2026-10-14

Absolute AFL/league parity (user request 2026-10-14): every league must
serve exactly what the AFL flagship serves — a model-predictions
breakdown with numbers, persisted AI explanations on the tip cards,
match-analysis talking points on the match page, and match-day weather.
Four additive storage pieces close the gap:

* ``league_model_predictions`` — the ``model_predictions`` counterpart
  keyed on ``events.id`` (one row per ``(event_id, model_name)``, the
  same ``(winner, confidence, margin)`` shape);
* ``league_tips.explanation``  — the persisted-AI-text column the AFL
  ``tips`` table has always had (nightly generation, byte-parity reads);
* ``league_match_analyses``    — the ``match_analyses`` counterpart
  (talking points per event);
* ``league_event_weather``     — the ``match_weather`` counterpart
  (Open-Meteo forecast snapshot per event);
* ``league_match_reports``     — the ``match_reports`` counterpart (the
  structured grand-final pre-match report, stored as JSONB).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0017_league_afl_parity_storage"
down_revision: Union[str, None] = "0016_league_tips_confidence_margin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "league_tips",
        sa.Column("explanation", sa.Text(), nullable=True),
    )
    op.create_table(
        "league_model_predictions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("model_name", sa.String(50), nullable=False, index=True),
        sa.Column("winner", sa.String(150), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("margin", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "event_id", "model_name", name="uq_league_model_predictions_event_model"
        ),
    )
    op.create_table(
        "league_match_analyses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
            unique=True,
        ),
        sa.Column("analysis_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "league_event_weather",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
            unique=True,
        ),
        sa.Column("venue", sa.Text(), nullable=True, index=True),
        sa.Column("match_date", sa.Date(), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("precipitation", sa.Float(), nullable=True),
        sa.Column("wind_speed", sa.Float(), nullable=True),
        sa.Column("wind_direction", sa.Integer(), nullable=True),
        sa.Column("wind_gusts", sa.Float(), nullable=True),
        sa.Column("humidity", sa.Integer(), nullable=True),
        sa.Column("weather_code", sa.Integer(), nullable=True),
        sa.Column("data_type", sa.Text(), nullable=True, default="forecast", index=True),
        sa.Column("raw_hourly", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            onupdate=sa.func.now(),
        ),
    )
    op.create_table(
        "league_match_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
            unique=True,
        ),
        sa.Column(
            "report_type",
            sa.String(length=50),
            nullable=False,
            default="grand_final_pre_match",
            index=True,
        ),
        sa.Column("report", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            onupdate=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("league_match_reports")
    op.drop_table("league_event_weather")
    op.drop_table("league_match_analyses")
    op.drop_table("league_model_predictions")
    op.drop_column("league_tips", "explanation")
