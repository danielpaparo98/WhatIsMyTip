"""add league tip confidence margin — AFL-parity tip shape (FCL-14).

Revision ID: 0016_league_tips_confidence_margin
Revises: 0015_model_versions_competition_scope
Create Date: 2026-10-13

FCL-14 (absolute AFL/league parity, user request 2026-10-13): the
league cards must render exactly like the AFL cards — heuristic badge,
confidence %, winner and ``Margin: N pts``.  The derived heuristics
already COMPUTE an AFL-shaped ``(side, confidence, margin)`` tip
(``LeagueConsensusTip``); generation just never stored the numbers.
Two nullable columns on ``league_tips`` close that gap:

* ``confidence`` — FLOAT 0..1 (the combiners' learned confidence, the
  fallback's 0.55, the vote-share/yolo mappings);
* ``margin``     — INTEGER home-signed margin in points.

Both NULL for the base result-derived models (picks, not numbers) and
for draw-no-pick rows — the same advisory-shape rule the AFL tips
table applies to its own draw rows.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_league_tips_confidence_margin"
down_revision: Union[str, None] = "0015_model_versions_competition_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "league_tips",
        sa.Column("confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "league_tips",
        sa.Column("margin", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("league_tips", "margin")
    op.drop_column("league_tips", "confidence")
