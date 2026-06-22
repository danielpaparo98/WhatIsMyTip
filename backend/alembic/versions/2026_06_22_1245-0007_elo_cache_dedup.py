"""Dedupe elo_cache alias and NULL team_name rows

Migration ``0004_canonical_team_names`` rewrote every known team alias to
its canonical compact form across ``games``, ``tips`` and ``elo_cache``.
On production it was *stamped past* (the deploy applied later migrations
without running 0004's body), so its ``elo_cache`` rename never executed.
As a result production's ``elo_cache`` still carries the duplicate rows
the rename was meant to collapse:

* ~8 alias rows alongside their canonical counterpart (e.g. a ``GWS`` row
  next to ``Giants``); and
* 1 row with a ``NULL`` ``team_name`` (rejected at the write boundary but
  already persisted).

This migration removes those rows idempotently, **preserving the more
complete data**: for each alias we first copy its rating/games/season into
the canonical row when the alias has strictly more ``games_played`` (or
the canonical row has none), and only then delete the alias row -- and
only when a canonical row actually exists (so we never orphan an alias
that has no canonical home).  The ``NULL`` row is unconditionally removed.

The alias->canonical pairs are copied verbatim from
``packages/shared/teams.py``'s ``TEAM_NAME_SETS`` (the canonical source of
truth).  Migrations must not depend on runtime Python, so the pairs are
hardcoded here as literals; ``test_migration_0007_alias_pairs_match_teams_py``
pins that they stay in sync.

Revision ID: 0007_elo_cache_dedup
Revises: 0006_model_version_num_width
Create Date: 2026-06-22 12:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = "0007_elo_cache_dedup"
down_revision: Union[str, Sequence[str], None] = "0006_model_version_num_width"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# alias (raw source form) -> canonical (compact form).
# Copied from packages/shared/teams.py TEAM_NAME_SETS at authoring time;
# pinned by test_migration_0007_alias_pairs_match_teams_py.  Only the
# non-identity mappings need cleaning -- canonical rows are kept as-is.
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


def upgrade() -> None:
    """Collapse alias rows into their canonical row and drop the NULL row.

    Safe to run repeatedly: a second pass finds no alias or NULL rows to
    act on, so it is a no-op.

    For each (alias, canonical) pair we:

    1. **Merge** -- when the alias row carries more ``games_played`` than
       the canonical row (or the canonical row has none), copy the alias's
       rating/games/last_updated/season into the canonical row so no
       higher-confidence data is silently lost.
    2. **Delete** the alias row, but **only when a canonical row exists**
       (maximal safety: an alias with no canonical home is left intact
       rather than orphaned).
    """
    bind = op.get_bind()

    for alias, canonical in _ALIAS_TO_CANONICAL.items():
        # 1. Merge the alias's data into the canonical row when the alias
        #    is the more-complete record.  PostgreSQL multi-table UPDATE
        #    ... FROM lets us join the canonical (c) and alias (a) rows in
        #    one statement.  No-op when either row is absent.
        bind.execute(
            text(
                "UPDATE elo_cache c "
                "SET rating = a.rating, "
                "    games_played = a.games_played, "
                "    last_updated = a.last_updated, "
                "    season = a.season "
                "FROM elo_cache a "
                "WHERE c.team_name = :canonical "
                "  AND a.team_name = :alias "
                "  AND (a.games_played > COALESCE(c.games_played, 0) "
                "       OR (c.games_played IS NULL "
                "           AND a.games_played IS NOT NULL))"
            ).bindparams(canonical=canonical, alias=alias)
        )

        # 2. Delete the alias row, but only if its canonical counterpart
        #    exists (the canonical row is authoritative).  Unconditional
        #    DELETE would also be safe after the merge, but guarding on
        #    the canonical row's existence means an alias with no
        #    canonical home is preserved rather than dropped.  Idempotent:
        #    a second run finds no alias row to delete.
        bind.execute(
            text(
                "DELETE FROM elo_cache "
                "WHERE team_name = :alias "
                "  AND EXISTS ("
                "    SELECT 1 FROM elo_cache WHERE team_name = :canonical"
                ")"
            ).bindparams(alias=alias, canonical=canonical)
        )

    # Remove the bad NULL team_name row (the write boundary now rejects
    # these, but one was already persisted).  Idempotent.
    bind.execute(text("DELETE FROM elo_cache WHERE team_name IS NULL"))


def downgrade() -> None:
    """One-way data cleanup: deleted alias / NULL rows cannot be
    reconstructed (we do not know their original ratings), so reverting
    is not meaningful.  Documented no-op.
    """
    pass
