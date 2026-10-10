"""Venue alias table — sponsor-drift normalization for rugby-league.

NRL venues are sponsor-branded and the branding DRIFTS across seasons
(the Sharks' home: PointsBet Stadium in 2022 → Ocean Protect Stadium in
2026; the Warriors' home: Mt Smart Stadium → Go Media Stadium → One NZ
Stadium → Hnry Stadium — verified in the live feeds, see
``.tmp/external-context/nrl-feed/fixturedownload-feed.md``).  Backtests
must group rows of the same PHYSICAL ground, so every sponsor variant
resolves to one canonical ground name here at the provider boundary.

Unknown venues pass through verbatim — never mangled — and are logged
once as backfill candidates for this table.

Extending: add ``"<feed Location string>": "<canonical ground>"`` to
``_ALIASES``.  Canonical grounds self-resolve (they are seeded into the
normalized map), so a venue that becomes canonical stops logging.
"""

from __future__ import annotations

import re
from typing import Dict, FrozenSet, Optional

from ..logger import get_logger

logger = get_logger(__name__)

#: Sponsor/variant name → canonical ground.  Keys are matched
#: case-, whitespace- and apostrophe-insensitively.
_ALIASES: Dict[str, str] = {
    # Sharks — Cronulla.  "PointsBet → Ocean Protect" verified 2026-10-09.
    "PointsBet Stadium": "Shark Park",
    "Ocean Protect Stadium": "Shark Park",
    "Southern Cross Group Stadium": "Shark Park",
    # Warriors — Auckland.  Four sponsor names, one ground.
    "Mt Smart Stadium": "Mount Smart Stadium",
    "Go Media Stadium": "Mount Smart Stadium",
    "One NZ Stadium": "Mount Smart Stadium",
    "Hnry Stadium": "Mount Smart Stadium",
    # Dragons — Kogarah.
    "Jubilee Oval": "Jubilee Stadium",
    "Netstrata Jubilee Stadium": "Jubilee Stadium",
    "St George Venues Jubilee Stadium": "Jubilee Stadium",
    # Knights — Newcastle (incl. the "sic" 2022 spelling from the feed).
    "McDonald Jones Stadium": "Newcastle Stadium",
    "McDonalds Park": "Newcastle Stadium",
    "Hunter Stadium": "Newcastle Stadium",
    # Sydney Olympic Park — Telstra/ANZ/Accor across the eras.
    "Telstra Stadium": "Stadium Australia",
    "ANZ Stadium": "Stadium Australia",
    "Accor Stadium": "Stadium Australia",
    # Moore Park.
    "Allianz Stadium": "Sydney Football Stadium",
    # Storm — Melbourne.
    "AAMI Park": "Melbourne Rectangular Stadium",
    # Broncos — Brisbane.
    "Suncorp Stadium": "Lang Park",
    # Panthers — Penrith (Polytec is the mid-2026 naming).
    "Pepper Stadium": "Penrith Stadium",
    "BlueBet Stadium": "Penrith Stadium",
    "Polytec Stadium": "Penrith Stadium",
    # Dolphins — Redcliffe (Kayo is the mid-2026 naming).
    "Moreton Daily Stadium": "Dolphin Stadium",
    "Kayo Stadium": "Dolphin Stadium",
    # Cowboys — Townsville.
    "1300SMILES Stadium": "North Queensland Stadium",
    "Queensland Country Bank Stadium": "North Queensland Stadium",
    # Eels — Parramatta.
    "Bankwest Stadium": "Western Sydney Stadium",
    "CommBank Stadium": "Western Sydney Stadium",
    # Raiders — Canberra.
    "GIO Stadium": "Canberra Stadium",
    # Sea Eagles — Brookvale.
    "Lottoland": "Brookvale Oval",
    "4 Pines Park": "Brookvale Oval",
    # Titans — Gold Coast (Robina).
    "Cbus Super Stadium": "Robina Stadium",
    # Perth rectangular venue (DISTINCT from the major Perth stadium).
    "nib Stadium": "Perth Rectangular Stadium",
    "HBF Park": "Perth Rectangular Stadium",
    # Perth major stadium — State of Origin host.
    "Optus Stadium": "Perth Stadium",
    # Minor/satellite sponsor-branded grounds.
    "C.ex Coffs International Stadium": "Coffs International Stadium",
    "Industree Group Stadium": "Central Coast Stadium",
}

#: The canonical grounds — every alias target.  Canonical names are
#: seeded into the lookup so they resolve to themselves and never
#: surface as unknown-venue backfill candidates.
CANONICAL_VENUES: FrozenSet[str] = frozenset(_ALIASES.values())

#: Public alias → canonical view (readable keys, as authored above).
VENUE_ALIASES: Dict[str, str] = dict(_ALIASES)


def _normalize(name: str) -> str:
    """Collapse case, apostrophes and whitespace runs for matching."""
    return re.sub(r"\s+", " ", name.replace("'", "").strip().lower())


_NORMALIZED: Dict[str, str] = {
    _normalize(alias): canonical for alias, canonical in _ALIASES.items()
}
for _canonical in CANONICAL_VENUES:
    _NORMALIZED.setdefault(_normalize(_canonical), _canonical)

#: Already-logged unknown venues (normalized) — keeps a season sync of
#: ~200 fixtures from emitting the same warning per match.
_BACKFILL_LOGGED: set[str] = set()


def resolve_venue(name: Optional[str]) -> Optional[str]:
    """Map a sponsor-branded venue string to its canonical ground.

    Unknown (and blank/None) venues pass through VERBATIM; a first-time
    unknown is logged at WARNING so operators can backfill the alias
    table (required for venue-normalized backtests).
    """
    if name is None or not name.strip():
        return name
    normalized = _normalize(name)
    canonical = _NORMALIZED.get(normalized)
    if canonical is not None:
        return canonical
    if normalized not in _BACKFILL_LOGGED:
        _BACKFILL_LOGGED.add(normalized)
        logger.warning(
            "Unmapped venue %r — passing through verbatim "
            "(venue alias table backfill candidate)",
            name,
        )
    return name


def reset_backfill_log() -> None:
    """Clear the log-once set for unknown venues.

    Production code never needs this — the set exists so a season sync
    (~200 fixtures) warns once per unknown venue instead of per match.
    Tests call it between cases: the process-global set would otherwise
    leak across the suite and suppress a case's expected warning.
    """
    _BACKFILL_LOGGED.clear()


__all__ = [
    "CANONICAL_VENUES",
    "VENUE_ALIASES",
    "reset_backfill_log",
    "resolve_venue",
]
