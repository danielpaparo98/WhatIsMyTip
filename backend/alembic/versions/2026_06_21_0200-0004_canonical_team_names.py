"""Canonicalise team names in existing data (logo fix)

Backfills the compact canonical team form into every table that stores a
team name, so logos, the ELO cache and model queries agree with the new
write-boundary normaliser in GameCRUD.create_or_update_with_tracking.
Affects games.home_team / away_team, tips.selected_team and
elo_cache.team_name.

This fixes the production bug where 7 of 18 logos rendered broken because
the live Squiggle sync stored "Western Bulldogs" / "GWS" / "Gold Coast"
verbatim while the logo map keys expect "Bulldogs" / "Giants" /
"GoldCoast".

Revision ID: 0004_canonical_team_names
Revises: 0003_job_executions_metrics_index
Create Date: 2026-06-21 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "0004_canonical_team_names"
down_revision: Union[str, Sequence[str], None] = (
    "0003_job_executions_metrics_index"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# alias (raw source form) -> canonical (compact form).  Only the
# non-identity mappings need a backfill; canonical names are already
# correct.  Must stay in sync with packages/shared/teams.py.
_ALIAS_TO_CANONICAL = {
    "Adelaide Crows": "Adelaide",
    "Brisbane Lions": "Brisbane",
    "Fremantle Dockers": "Fremantle",
    "GWS": "Giants",
    "Greater Western Sydney": "Giants",
    "GWS Giants": "Giants",
    "Gold Coast": "GoldCoast",
    "Gold Coast Suns": "GoldCoast",
    "North Melbourne": "NorthMelbourne",
    "Kangaroos": "NorthMelbourne",
    "Port Adelaide": "PortAdelaide",
    "Port Power": "PortAdelaide",
    "St Kilda": "StKilda",
    "Sydney Swans": "Sydney",
    "West Coast": "WestCoast",
    "West Coast Eagles": "WestCoast",
    "Western Bulldogs": "Bulldogs",
    "Footscray": "Bulldogs",
}

# (table, column) pairs that store a team name.
_TEAM_COLUMNS = [
    ("games", "home_team"),
    ("games", "away_team"),
    ("tips", "selected_team"),
    ("elo_cache", "team_name"),
]


def upgrade() -> None:
    """Rewrite every known alias to its canonical form in all team columns."""
    bind = op.get_bind()
    for table, column in _TEAM_COLUMNS:
        for alias, canonical in _ALIAS_TO_CANONICAL.items():
            bind.execute(
                text(
                    "UPDATE " + table + " SET " + column + " = :canonical "
                    "WHERE " + column + " = :alias"
                ),
                {"canonical": canonical, "alias": alias},
            )


def downgrade() -> None:
    """One-way data migration: the canonical form is the intended
    long-term storage, so reverting is not meaningful.  No-op."""
    pass
