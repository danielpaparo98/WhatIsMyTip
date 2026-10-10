"""NrlProvider — the FixtureDownload FeedProvider for rugby-league
(Phase 5.2, multi-sport).

fixturedownload.com serves NRL fixtures + results as free JSON at
``https://fixturedownload.com/feed/json/{slug}`` — no auth, no key,
slugs ``nrl-{year}`` (2017+ verified), ``nrlw-{year}`` and
``state-of-origin-{year}`` (live-verified 2026-10-09, see
``.tmp/external-context/nrl-feed/fixturedownload-feed.md``).

Politeness: the site self-declares the data "is usually only updated
once a day", so the daily cadence costs AT MOST one fetch per slug per
day — an in-process TTL cache keyed by slug, SHARED by every
``NrlProvider`` instance in the process.  Single-fixture lookups ride
the same cache (never trigger a fetch).

Vendor dialect (verified schema — the site warns it "may change at any
time without notice", so every mapping lives in
:func:`fixture_from_fixturedownload`):

* ``MatchNumber`` → ``external_id``
* ``RoundNumber`` → ``round_id``
* ``DateUtc`` (``"2026-03-01 02:15:00Z"``) → tz-aware UTC ``starts_at``
* ``HomeTeam``/``AwayTeam`` → participant names
* ``Location`` → venue, resolved through the venue alias table
  (:mod:`.venue_aliases`) so sponsor drift collapses onto canonical
  grounds; unknown venues pass through verbatim (logged for backfill)
* ``HomeTeamScore``/``AwayTeamScore`` → scores; nulls mean pre-match

Completion is driven by score presence — ``Winner`` is deliberately
IGNORED because rugby league has draws: a level full-time scoreline
leaves ``Winner`` null on a completed game.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Tuple,
)

from .dto import FixtureDTO
from .venue_aliases import resolve_venue

#: One fetch per slug per day — the feed self-declares daily refreshes.
DEFAULT_TTL_SECONDS = 86400.0

_FETCH_JSON = Callable[[str], Awaitable[Any]]
_CLOCK = Callable[[], float]
_CACHE = MutableMapping[str, Tuple[float, Any]]

#: Process-wide slug → (fetched_at, payload).  Shared by every instance
#: so the daily cadence costs ≤ 1 fetch per slug per day in total.
_SHARED_CACHE: Dict[str, Tuple[float, Any]] = {}


def _parse_utc(raw: Any) -> Optional[datetime]:
    """Feed timestamps are UTC (``"2026-03-01 02:15:00Z"``); a naive
    value (schema drift) is assumed UTC rather than leaking naive
    datetimes into the storage boundary."""
    if not raw:
        return None
    parsed = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


#: FixtureDownload's ``MatchNumber`` is SEASON-SCOPED — every season's
#: feed restarts at 1 — while the events layer keys source refs on
#: ``(source, external_id)`` globally (ADR 0001 UQ).  The provider
#: therefore owns a deterministic season-composite external id:
#: ``season * 1000 + MatchNumber`` (2026 match 1 → 2026001), reversible
#: in :meth:`NrlProvider.get_fixture`.
SEASON_FACTOR = 1000


def make_external_id(season: int, match_number: Optional[int]) -> Optional[int]:
    """Composite ``(season, MatchNumber)`` → globally-unique id."""
    if match_number is None:
        return None
    return season * SEASON_FACTOR + match_number


def fixture_from_fixturedownload(
    entry: Mapping[str, Any], *, season: int, source: str
) -> FixtureDTO:
    """Map one verified feed record to the canonical fixture DTO.

    The one function that knows the FixtureDownload dialect — schema
    drift upstream is a fix here, and nowhere else.  ``external_id``
    is the season-composite id (see ``SEASON_FACTOR``): MatchNumber
    alone would collide across seasons in the global source-ref UQ.
    """
    home_score = entry.get("HomeTeamScore")
    away_score = entry.get("AwayTeamScore")
    return FixtureDTO(
        source=source,
        external_id=make_external_id(season, entry.get("MatchNumber")),
        season=season,
        round_id=entry.get("RoundNumber"),
        home_participant=entry.get("HomeTeam") or None,
        away_participant=entry.get("AwayTeam") or None,
        home_score=home_score,
        away_score=away_score,
        venue=resolve_venue(entry.get("Location")),
        starts_at=_parse_utc(entry.get("DateUtc")),
        # Draws (level scores, null Winner) are complete — see docstring.
        completed=home_score is not None and away_score is not None,
    )


def _wanted(
    fixture: FixtureDTO,
    *,
    round: Optional[int],
    complete: Optional[bool],
    start_date: Optional[str],
    end_date: Optional[str],
) -> bool:
    """Season-narrowing filters (all optional, combined with AND).
    Date bounds are inclusive ISO dates (``YYYY-MM-DD``)."""
    if round is not None and fixture.round_id != round:
        return False
    if complete is not None and fixture.completed != complete:
        return False
    if start_date is None and end_date is None:
        return True
    if fixture.starts_at is None:
        return False
    day = fixture.starts_at.date()
    if start_date is not None and day < date.fromisoformat(start_date):
        return False
    if end_date is not None and day > date.fromisoformat(end_date):
        return False
    return True


class NrlProvider:
    """FeedProvider for one rugby-league competition on FixtureDownload."""

    sport_id = "rugby-league"

    #: Feed slug per competition key (the URL pattern is ``{slug}``).
    COMPETITION_SLUGS: Mapping[str, str] = {
        "nrl": "nrl-{year}",
        "nrlw": "nrlw-{year}",
        "origin": "state-of-origin-{year}",
    }

    FEED_URL_TEMPLATE = "https://fixturedownload.com/feed/json/{slug}"

    def __init__(
        self,
        competition: str = "nrl",
        *,
        fetch_json: Optional[_FETCH_JSON] = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: _CLOCK = time.time,
        cache: Optional[_CACHE] = None,
    ):
        if competition not in self.COMPETITION_SLUGS:
            raise ValueError(
                f"Unknown competition {competition!r} — "
                f"available: {sorted(self.COMPETITION_SLUGS)}"
            )
        self.competition = competition
        self.source = f"fixturedownload-{competition}"
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._fetch_json = fetch_json or self._default_fetch
        self._cache: _CACHE = cache if cache is not None else _SHARED_CACHE
        # Slugs fetched THROUGH THIS INSTANCE, in fetch order —
        # get_fixture scans only these, because MatchNumber is
        # season-scoped at the feed and a cross-competition scan would
        # be ambiguous (every season's match 1 shares the id).
        self._fetched_slugs: List[Tuple[str, int]] = []

    # ------------------------------------------------------------------
    # Transport

    def _headers(self) -> Dict[str, str]:
        # No auth, no key — we identify our bot per platform etiquette.
        return {
            "Accept": "application/json",
            "User-Agent": "WhatIsMyTip (contact@whatismytip.com)",
        }

    async def _default_fetch(self, url: str) -> Any:
        import httpx

        async with httpx.AsyncClient(
            timeout=30.0, headers=self._headers(), follow_redirects=True
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()

    # ------------------------------------------------------------------
    # FeedProvider protocol

    async def get_fixtures(
        self,
        season: int,
        *,
        round: Optional[int] = None,
        complete: Optional[bool] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[FixtureDTO]:
        """Fixtures for one competition-season (optionally narrowed)."""
        payload = await self._season_payload(season)
        fixtures = [
            fixture_from_fixturedownload(entry, season=season, source=self.source)
            for entry in payload
        ]
        return [
            f
            for f in fixtures
            if _wanted(
                f,
                round=round,
                complete=complete,
                start_date=start_date,
                end_date=end_date,
            )
        ]

    async def get_fixture(self, external_id: int) -> Optional[FixtureDTO]:
        """Resolve a fixture from already-fetched season payloads (most
        recently fetched season first); ``None`` when nothing cached
        matches.  Lookups NEVER trigger a fetch — the daily cache is
        the politeness contract.  ``external_id`` is the season
        composite (``season * 1000 + MatchNumber``): the season picks
        the cached slug, the remainder picks the match.  Callers
        needing a specific fixture should ensure its season was
        fetched (or use ``get_fixtures``)."""
        season, match_number = (
            external_id // SEASON_FACTOR,
            external_id % SEASON_FACTOR,
        )
        for slug, cached_season in reversed(self._fetched_slugs):
            if cached_season != season:
                continue
            cached = self._cache.get(slug)
            if not cached:
                continue
            for entry in cached[1]:
                if entry.get("MatchNumber") == match_number:
                    return fixture_from_fixturedownload(
                        entry, season=season, source=self.source
                    )
        return None

    # ------------------------------------------------------------------

    def _slug(self, season: int) -> str:
        return self.COMPETITION_SLUGS[self.competition].format(year=season)

    async def _season_payload(self, season: int) -> List[Mapping[str, Any]]:
        """Fresh-or-cached season payload; refetches only after the TTL
        expires, so the daily cadence issues at most one fetch per slug."""
        slug = self._slug(season)
        now = self._clock()
        cached = self._cache.get(slug)
        if cached is not None and now - cached[0] < self.ttl_seconds:
            return cached[1]
        payload = await self._fetch_json(self.FEED_URL_TEMPLATE.format(slug=slug))
        if not isinstance(payload, list):
            raise ValueError(
                f"Unexpected FixtureDownload payload for {slug!r}: "
                f"expected a JSON list, got {type(payload).__name__}"
            )
        self._cache[slug] = (now, payload)
        self._fetched_slugs.append((slug, season))
        return payload


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "SEASON_FACTOR",
    "NrlProvider",
    "fixture_from_fixturedownload",
    "make_external_id",
]
