"""SportContext — the per-sport configuration object (P2-2, ADR 0001).

One frozen dataclass replaces the scattered single-sport assumptions:
hardcoded off-season months, a global season, an AFL-only venue
registry, the global Redis cache namespace, and scoring semantics.
Every sport plugs in by declaring a context, not by editing branches.

``AFL`` is the bootstrap context: the system's existing behaviour,
made explicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SportContext:
    """Everything sport-specific about the prediction pipeline."""

    sport_id: str
    display_name: str
    #: "team" (head-to-head fixtures) or "individual" (golf/tennis).
    participant_model: str
    #: Whether drawn outcomes exist (AFL yes; tennis no; golf n/a → False).
    has_draws: bool
    #: Whether venue-side advantage applies (False for most individual
    #: sports and neutral-venue formats).
    has_home_advantage: bool
    #: The unit ``score_projection`` is denominated in — "points",
    #: "sets", "strokes" — or None when the sport has no useful
    #: projection (e.g. match-play).
    scoring_unit: Optional[str]
    #: Scheduler/display timezone for the competition.
    cron_timezone: str
    #: Cache key namespace — every sport-scoped Redis key lives under
    #: ``wimt:{cache_namespace}:...`` so ratings/margins never
    #: cross-contaminate by name.
    cache_namespace: str
    #: Months (1-12) in which the sport is OFF-season — used by the
    #: daily-sync noise-reduction policy (reduced run window).  Empty
    #: for year-round sports (golf, tennis) which never skip.
    off_season_months: frozenset = frozenset()

    def cache_key(self, *parts: str) -> str:
        """Build a namespaced cache key, e.g. ``wimt:afl:elo_ratings``."""
        return ":".join(("wimt", self.cache_namespace, *parts))

    def is_off_season_month(self, month: int) -> bool:
        """True when ``month`` falls in this sport's off-season."""
        return month in self.off_season_months


AFL = SportContext(
    sport_id="afl",
    display_name="Australian Football",
    participant_model="team",
    has_draws=True,
    has_home_advantage=True,
    scoring_unit="points",
    cron_timezone="Australia/Perth",
    cache_namespace="afl",
    off_season_months=frozenset({10, 11, 12, 1, 2}),
)

DEFAULT_CONTEXT = AFL

__all__ = ["SportContext", "AFL", "DEFAULT_CONTEXT"]
