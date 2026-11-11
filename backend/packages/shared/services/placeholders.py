"""Bracket-placeholder guards for league sync + tipping (AFLW-2026).

The AFL platform feed emits unresolved finals as fixtures at venue
"To Be Confirmed" whose sides are bracket expressions ("1st" vs "4th",
"Loser of QF1" vs "Winner of EF1").  The feed never closes those rows
out when the real fixtures land (the real games are published as new
matches), so placeholders sit ``scheduled`` forever, get tipped by the
nightly sweeps ("1st" to win at 90%!), and — until the frontend learned
to ignore them — pinned league pages to the first finals round.

AFLW-2026 REGRESSION (2026-11-11, prod): the AFL platform provider now
skips TBC-venue fixtures at ingestion (mirroring the Sportix provider's
existing skip), and the tipping sweeps filter any placeholder rows that
are already in the database.  This module carries the shared predicate.
"""

from __future__ import annotations

PLACEHOLDER_VENUE = "to be confirmed"


def is_placeholder_venue(venue: str | None) -> bool:
    """True when a fixture's venue marks it an unresolved placeholder.

    Case- and whitespace-insensitive on the exact marker the feeds use
    ("To Be Confirmed").  ``None``/unknown venues are NOT placeholders —
    an undecidable row stays real.
    """
    return (venue or "").strip().lower() == PLACEHOLDER_VENUE


__all__ = ["PLACEHOLDER_VENUE", "is_placeholder_venue"]
