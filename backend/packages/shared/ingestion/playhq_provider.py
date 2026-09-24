"""PlayHQProvider — client for leagues hosted on PlayHQ
(api.playhq.com/graphql, open — no auth).

Tasmania's NWFL and SFL both run on PlayHQ under the ``afl`` tenant.
One provider instance per organisation + grade set:

    PlayHQProvider(
        organisation_routing_code="abb4e230",
        grade_ids={"NWFL Premier League Senior Men": "c2721398"},
    )

Recipe (verified against the live API 2026-09-24; every query HTTP
200; query field names taken verbatim from the www.playhq.com SPA
bundle ``assets/index.107a513e.js`` — not guessed):

* ``POST /graphql`` with ``Content-Type: application/json`` and the
  preflight headers the SPA sends: ``Origin``,
  ``Referer``, ``Apollo-Require-Preflight: true`` and ``tenant: afl``.
  No auth.
* ``discoverOrganisation(code:)`` — the ROUTING code is not the
  organisation id: the SFL resolves code ``cc453fd4`` → id
  ``c81f5b0c``, so the id must always be resolved through this query.
* ``discoverCompetitions(organisationID:)`` → competitions with their
  ``seasons`` (name + ``status.value`` ACTIVE/COMPLETED/…).
* ``discoverSeason(seasonID:)`` → the season's ``grades``.
* ``discoverGradeFixture(gradeID:)`` → the FULL season as a list of
  rounds; each round carries ``name``, ``isFinalsRound``, a ``byes``
  array (team references — not games) and the ``games``.

Dialect notes (real NWFL 2026 payload, grade ``c2721398``):

* Games carry ``home``/``away`` unions of ``DiscoverTeam`` (id+name)
  and ``ProvisionalTeam`` (name only, finals placeholders) — both
  expose ``name``, which is all the DTO needs.
* Scores arrive per side as ``result.home/away.statistics`` entries
  typed ``TOTAL_SCORE`` / ``TOTAL_GOALS`` / ``TOTAL_BEHINDS``;
  TOTAL_SCORE is used when present, goals × 6 + behinds otherwise.
* ``status.value`` is ``FINAL`` for played games; future games come
  back with no ``result`` block at all → ``completed=False``.
* ``allocation`` may carry ``time`` ("14:00:00") plus a ``court.venue``;
  a date-only game maps to midnight (naive, venue-local — the payload
  has no offsets; tz conversion is sync-service policy).
* Round names: ``Round N`` → N; finals map past the home-and-away
  rounds (``Finals Round 1`` → 101, ``Preliminary Final`` → 103,
  ``Grand Final`` → 104) like Sportix, with an isFinalsRound position
  fallback for names the map doesn't know.
* Single-fixture lookup is not part of the discover surface —
  completion is detected by batch season re-sync.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..logger import get_logger
from .dto import FixtureDTO

logger = get_logger(__name__)

_ROUND_NUM_RE = re.compile(r"^round (\d+)$")

# Finals round numbering — past any plausible home-and-away length.
# Keys are lower-cased round names with collapsed whitespace.
FINALS_ROUND_MAP = {
    "finals round 1": 101,
    "finals week 1": 101,
    "elimination final": 101,
    "qualifying final": 101,
    "finals round 2": 102,
    "finals week 2": 102,
    "semi final": 102,
    "preliminary final": 103,
    "grand final": 104,
}

# Verified-live Tasmania recipe (recon 2026-09-24): routing codes and
# the Premier League Senior Men grade ids per league.
NWFL_ROUTING_CODE = "abb4e230"
SFL_ROUTING_CODE = "cc453fd4"
NWFL_GRADE_IDS = {"NWFL Premier League Senior Men": "c2721398"}
SFL_GRADE_IDS = {"SFL Premier League Senior Men": "879a3be0"}


def _normalize_round_name(name: Optional[str]) -> Optional[str]:
    return re.sub(r"\s+", " ", name).strip().lower() if name else None


def round_id_from_name(
    name: Optional[str], *, is_finals_round: bool = False, finals_position: int = 0
) -> Optional[int]:
    """Map a PlayHQ round name to the canonical round bucket.

    ``Round N`` → N; known finals names map past the home-and-away
    rounds (``grand final`` → 104, …).  An unrecognized finals round
    falls back to ``101 + <position among finals rounds>`` so the
    max(round) grand-final heuristic keeps working; non-finals rounds
    without a number map to ``None``.
    """
    normalized = _normalize_round_name(name)
    if not normalized:
        return 101 + finals_position if is_finals_round else None
    m = _ROUND_NUM_RE.match(normalized)
    if m:
        return int(m.group(1))
    mapped = FINALS_ROUND_MAP.get(normalized)
    if mapped:
        return mapped
    if is_finals_round:
        return 101 + finals_position
    return None


class PlayHQProvider:
    """FeedProvider for one organisation's grades on PlayHQ."""

    sport_id = "afl"
    source = "playhq"

    def __init__(
        self,
        *,
        organisation_routing_code: str,
        grade_ids: Optional[Dict[str, str]] = None,
        grade_name: Optional[str] = None,
        api_url: str = "https://api.playhq.com/graphql",
        query: Optional[Callable[[str, Dict[str, Any]], Awaitable[Any]]] = None,
    ):
        if not grade_ids and not grade_name:
            raise ValueError(
                "PlayHQProvider needs a grade selection: grade_ids "
                "{grade name: id} or a grade_name selector"
            )
        self.organisation_routing_code = organisation_routing_code
        self.grade_ids = dict(grade_ids or {})
        self._grade_name = grade_name
        self._api_url = api_url
        self._query = query or self._default_query

    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {
            # The discover API requires the SPA's preflight header set
            # (verified live 2026-09-24) — no auth.
            "Content-Type": "application/json",
            "Origin": "https://www.playhq.com",
            "Referer": "https://www.playhq.com/",
            "Apollo-Require-Preflight": "true",
            "tenant": "afl",
            # Identify our bot politely.
            "User-Agent": "WhatIsMyTip (contact@whatismytip.com)",
        }

    async def _default_query(self, document: str, variables: Dict[str, Any]) -> Any:
        import asyncio

        import httpx

        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(
                    timeout=30.0, verify=True, headers=self._headers()
                ) as client:
                    response = await client.post(
                        self._api_url,
                        json={"query": document, "variables": variables},
                    )
                    response.raise_for_status()
                    return response.json()
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_error = e
                await asyncio.sleep(1.5 * (attempt + 1))
        raise last_error  # type: ignore[misc]

    async def _run(self, document: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        payload = await self._query(document, variables)
        if not isinstance(payload, dict):
            raise RuntimeError(f"PlayHQ returned a non-object payload for {document[:60]!r}")
        if payload.get("errors"):
            messages = "; ".join(
                str(error.get("message", error)) for error in payload["errors"]
            )
            raise RuntimeError(f"PlayHQ GraphQL error: {messages}")
        return payload.get("data") or {}

    # -- discover chain -------------------------------------------------

    async def _discover_organisation(self) -> Dict[str, Any]:
        data = await self._run(
            "query org($code: String!) { discoverOrganisation(code: $code) "
            "{ id type name } }",
            {"code": self.organisation_routing_code},
        )
        org = data.get("discoverOrganisation")
        if not isinstance(org, dict):
            raise ValueError(
                f"Organisation routing code {self.organisation_routing_code!r} "
                "not found at the provider"
            )
        return org

    async def _discover_competitions(self, organisation_id: str) -> List[Dict[str, Any]]:
        data = await self._run(
            "query comps($organisationID: ID!) { discoverCompetitions(organisationID: "
            "$organisationID) { id name seasons(organisationID: $organisationID) "
            "{ id name startDate endDate status { name value } } } }",
            {"organisationID": organisation_id},
        )
        return list(data.get("discoverCompetitions") or [])

    async def _discover_season_grades(self, season_id: str) -> List[Dict[str, Any]]:
        data = await self._run(
            "query season($id: String!) { discoverSeason(seasonID: $id) { id name "
            "status { name value } grades { id name } } }",
            {"id": season_id},
        )
        season = data.get("discoverSeason") or {}
        return list(season.get("grades") or [])

    async def _discover_grade_fixture(self, grade_id: str) -> List[Dict[str, Any]]:
        # home/away are DiscoverPossibleTeam unions — the inline
        # fragment spreads are REQUIRED (a bare `home { name }`
        # selection fails GraphQL validation with a 400).
        data = await self._run(
            "query gradeAllRounds($gradeID: ID!) { discoverGradeFixture(gradeID: "
            "$gradeID) { id name provisionalDates isFinalsRound byes { id name } "
            "games { id "
            "home { ... on ProvisionalTeam { name } ... on DiscoverTeam { name } } "
            "away { ... on ProvisionalTeam { name } ... on DiscoverTeam { name } } "
            "result { home { statistics "
            "{ count type { value } } } away { statistics { count type { value } } } } "
            "status { name value } date allocation { time court { name venue "
            "{ name } } } } } }",
            {"gradeID": grade_id},
        )
        return list(data.get("discoverGradeFixture") or [])

    # -- FeedProvider ---------------------------------------------------

    async def get_fixtures(
        self,
        season: int,
        *,
        round: Optional[int] = None,  # shadows builtin, FeedProvider protocol
        complete: Optional[bool] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[FixtureDTO]:
        """Full season of fixtures for the configured grade(s)."""
        organisation = await self._discover_organisation()
        competitions = await self._discover_competitions(organisation["id"])
        season_entry = self._find_season(competitions, season)
        grades = await self._discover_season_grades(season_entry["id"])
        resolved_ids = self._resolve_grade_ids(grades)

        fixtures: List[FixtureDTO] = []
        for grade_id in resolved_ids:
            rounds = await self._discover_grade_fixture(grade_id)
            finals_seen = 0
            for round_entry in rounds:
                is_finals = bool(round_entry.get("isFinalsRound"))
                round_id = round_id_from_name(
                    round_entry.get("name"),
                    is_finals_round=is_finals,
                    finals_position=finals_seen,
                )
                if is_finals:
                    finals_seen += 1
                for game in round_entry.get("games") or []:
                    dto = self._game_to_fixture(game, season, round_id)
                    if dto is not None:
                        fixtures.append(dto)
        return fixtures

    async def get_fixture(self, external_id: int | str) -> Optional[FixtureDTO]:
        raise NotImplementedError(
            "PlayHQ offers no single-game discover query; "
            "completion is detected by batch season re-sync."
        )

    # ------------------------------------------------------------------

    def _find_season(
        self, competitions: List[Dict[str, Any]], season: int
    ) -> Dict[str, Any]:
        wanted = str(season)
        matches: List[Dict[str, Any]] = [
            season_entry
            for competition in competitions
            for season_entry in competition.get("seasons") or []
            if str(season_entry.get("name") or "") == wanted
        ]
        if not matches:
            raise ValueError(f"Season {wanted} not found at the provider")
        # Prefer the ACTIVE one when several competitions share the label.
        for season_entry in matches:
            if (season_entry.get("status") or {}).get("value") == "ACTIVE":
                return season_entry
        return matches[0]

    def _resolve_grade_ids(self, grades: List[Dict[str, Any]]) -> List[str]:
        if self.grade_ids:
            return list(self.grade_ids.values())
        if self._grade_name:
            wanted = self._grade_name.strip().lower()
            named = [g for g in grades if str(g.get("name") or "").lower() == wanted]
            if not named:
                named = [
                    g for g in grades if wanted in str(g.get("name") or "").lower()
                ]
            if not named:
                raise ValueError(
                    f"Grade {self._grade_name!r} not found in the season's grades"
                )
            return [str(named[0]["id"])]
        raise ValueError("No grade configured — set grade_ids or grade_name")

    def _game_to_fixture(
        self, game: Dict[str, Any], season: int, round_id: Optional[int]
    ) -> Optional[FixtureDTO]:
        home = (game.get("home") or {}).get("name")
        away = (game.get("away") or {}).get("name")
        if not home or not away:
            # Byes travel in the round's `byes` array, never as games;
            # a game with a missing side is an unresolved TBC placeholder.
            return None
        result = game.get("result") or {}
        home_score = self._side_score(result.get("home"))
        away_score = self._side_score(result.get("away"))
        return FixtureDTO(
            source=self.source,
            # PlayHQ game ids are opaque hex strings (FixtureDTO's int
            # is historical — ISportsProvider already ships str ids).
            external_id=str(game.get("id")),  # type: ignore[arg-type]
            season=season,
            round_id=round_id,
            home_participant=home,
            away_participant=away,
            home_score=home_score,
            away_score=away_score,
            venue=self._venue(game),
            starts_at=self._starts_at(game),
            completed=home_score is not None and away_score is not None,
        )

    @staticmethod
    def _side_score(side: Optional[Dict[str, Any]]) -> Optional[int]:
        if not side:
            return None
        counts: Dict[str, Any] = {}
        for entry in side.get("statistics") or []:
            value = (entry.get("type") or {}).get("value")
            if value:
                counts[value] = entry.get("count")
        if counts.get("TOTAL_SCORE") is not None:
            return int(counts["TOTAL_SCORE"])
        goals = counts.get("TOTAL_GOALS")
        behinds = counts.get("TOTAL_BEHINDS")
        if goals is not None and behinds is not None:
            return int(goals) * 6 + int(behinds)
        return None

    @staticmethod
    def _venue(game: Dict[str, Any]) -> Optional[str]:
        allocation = game.get("allocation") or {}
        court = allocation.get("court") or {}
        venue = court.get("venue") or {}
        return venue.get("name") or court.get("name") or None

    @staticmethod
    def _starts_at(game: Dict[str, Any]) -> Optional[datetime]:
        date = game.get("date")
        time = None
        allocation = game.get("allocation") or {}
        if date:
            time = allocation.get("time")
        elif allocation.get("dateTimeList"):
            first = allocation["dateTimeList"][0] or {}
            date = first.get("date")
            time = first.get("time")
        if not date:
            return None
        try:
            if time:
                return datetime.fromisoformat(f"{date}T{time}")
            return datetime.fromisoformat(str(date))
        except ValueError:
            return datetime.fromisoformat(str(date))


__all__ = [
    "FINALS_ROUND_MAP",
    "NWFL_GRADE_IDS",
    "NWFL_ROUTING_CODE",
    "PlayHQProvider",
    "SFL_GRADE_IDS",
    "SFL_ROUTING_CODE",
    "round_id_from_name",
]
