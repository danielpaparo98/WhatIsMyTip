"""Canonical AFL team-name normalisation.

A single source of truth for mapping every known team alias (Squiggle,
AFL Tables, human-friendly forms) to the canonical compact form used by
the rest of the system — DB columns, logo filenames, the ELO cache and
the model layer.

Why this exists: the Squiggle live-sync path writes team names verbatim
(``Western Bulldogs``, ``GWS``, ``Gold Coast`` …) while the frontend
logo map, the ELO cache and several model queries expect the compact
canonical form (``Bulldogs``, ``Giants``, ``GoldCoast`` …).  Without a
normaliser at the write boundary the two worlds never agree and 7 of 18
logos silently render broken in production.

Importing this module is cheap: it builds a small reverse-lookup dict at
import time.
"""
from __future__ import annotations

from typing import Dict, Set

# Canonical team name → all known aliases.  The canonical key is the
# compact form that matches ``frontend/composables/useTeamLogos.ts`` and
# the PNG filenames in ``frontend/public/logos/``.
TEAM_NAME_SETS: Dict[str, Set[str]] = {
    "Adelaide": {"Adelaide", "Adelaide Crows"},
    "Brisbane": {"Brisbane", "Brisbane Lions"},
    "Carlton": {"Carlton"},
    "Collingwood": {"Collingwood"},
    "Essendon": {"Essendon"},
    "Fremantle": {"Fremantle", "Fremantle Dockers"},
    "Geelong": {"Geelong"},
    "Giants": {"Giants", "GWS", "Greater Western Sydney", "GWS Giants"},
    "GoldCoast": {"GoldCoast", "Gold Coast", "Gold Coast Suns"},
    "Hawthorn": {"Hawthorn"},
    "Melbourne": {"Melbourne"},
    "NorthMelbourne": {"NorthMelbourne", "North Melbourne", "Kangaroos"},
    "PortAdelaide": {"PortAdelaide", "Port Adelaide", "Port Power"},
    "Richmond": {"Richmond"},
    "StKilda": {"StKilda", "St Kilda"},
    "Sydney": {"Sydney", "Sydney Swans"},
    "WestCoast": {"WestCoast", "West Coast", "West Coast Eagles"},
    "Bulldogs": {"Bulldogs", "Western Bulldogs", "Footscray"},
}

# Reverse map: any alias (lower-cased) → canonical name.
_ALIAS_TO_CANONICAL: Dict[str, str] = {}
for _canonical, _aliases in TEAM_NAME_SETS.items():
    for _alias in _aliases:
        _ALIAS_TO_CANONICAL[_alias.lower()] = _canonical


def canonical_team(name: str | None) -> str:
    """Map any team name (or alias) to its canonical compact form.

    Unknown names are returned stripped but otherwise unchanged so the
    function is always safe to call.  ``None``/empty input returns an
    empty string.
    """
    if not name:
        return ""
    stripped = name.strip()
    return _ALIAS_TO_CANONICAL.get(stripped.lower(), stripped)


__all__ = ["TEAM_NAME_SETS", "canonical_team"]
