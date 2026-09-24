"""SportixProvider — generic client for leagues hosted on the Sportix
platform (Phase 5).

The official WAFL site is one tenant of this platform; other
competitions on the same tenant (WAFLW, Colts, Reserves) — or other
leagues' tenants — are constructor calls, not new code:

    SportixProvider(source="sportix-waflw", competition_name="WAFLW")

API shape (verified against the live WAFL tenant):

* ``GET seasons``                                  → [{id, name, active}]
* ``GET matches?season_slug=<year>``               → competition discovery
* ``GET matches?competition=&season=&round=all``   → full season

Round mapping: ``Round N`` → N; finals map past the home-and-away
rounds (``finals-week-1`` → 101, …, ``grand-final`` → 104) so the
max(round) grand-final heuristic keeps working.  UTC datetimes are
converted to venue-local naive form by the sync service, not here.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..logger import get_logger
from .dto import FixtureDTO

logger = get_logger(__name__)

_ROUND_NUM_RE = re.compile(r"round-(\d+)$")

# Finals round numbering — past any plausible home-and-away length.
FINALS_ROUND_MAP = {
    "finals-week-1": 101,
    "finals-week-2": 102,
    "preliminary-final": 103,
    "grand-final": 104,
    "elimination-final": 101,
    "qualifying-final": 101,
    "semi-final": 102,
}


def round_id_from_slug(slug: Optional[str]) -> Optional[int]:
    if not slug:
        return None
    m = _ROUND_NUM_RE.match(slug)
    if m:
        return int(m.group(1))
    return FINALS_ROUND_MAP.get(slug)


class SportixProvider:
    """FeedProvider for a competition on a Sportix-platform tenant."""

    sport_id = "afl"

    def __init__(
        self,
        *,
        source: str,
        competition_name: str,
        season_name: Optional[str] = None,
        api_url: str = "https://api.sportix.cloud/public",
        api_key: str = "290|yQfFH5WycjbEb8eUtVtTCXZt2aWOxFpDjUYEdxgQ9326de46",
        tenant_id: str = "3b47430d-e8a4-4f13-bc22-1b622d4e9bda",
        fetch_json: Optional[Callable[[str, Dict[str, Any]], Awaitable[Any]]] = None,
    ):
        self.source = source
        self.competition_name = competition_name
        self._season_name = season_name
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._tenant_id = tenant_id
        self._fetch_json = fetch_json or self._default_fetch

    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            "tenant-id": self._tenant_id,
            # Identify our bot per the platform's expectations.
            "User-Agent": "WhatIsMyTip (contact@whatismytip.com)",
        }

    async def _default_fetch(self, path: str, params: Dict[str, Any]) -> Any:
        import httpx

        async with httpx.AsyncClient(
            timeout=30.0, verify=True, headers=self._headers()
        ) as client:
            response = await client.get(f"{self._api_url}/{path}", params=params)
            response.raise_for_status()
            return response.json()

    # ------------------------------------------------------------------

    async def get_seasons(self) -> List[Dict[str, Any]]:
        """Available seasons (newest first), e.g. ``[{'id': …, 'name': '2026'}]``."""
        return await self._fetch_json("seasons", {})

    async def get_fixtures(
        self,
        season: int,
        *,
        round: Optional[int] = None,
        complete: Optional[bool] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[FixtureDTO]:
        """Full season of fixtures for the configured competition."""
        season_id = await self._resolve_season_id(season)
        competition_id = await self._resolve_competition_id(str(season))

        payload = await self._fetch_json(
            "matches",
            {"competition": competition_id, "season": season_id, "round": "all"},
        )

        fixtures: List[FixtureDTO] = []
        for competition in payload.get("competitions", []):
            if competition.get("name") != self.competition_name:
                continue
            for match in competition.get("matches", []):
                if match.get("bye"):
                    continue
                fixtures.append(self._match_to_fixture(match, season))
        return fixtures

    async def get_fixture(self, external_id: int) -> Optional[FixtureDTO]:
        raise NotImplementedError(
            "Single-fixture lookup is not part of the Sportix public surface; "
            "completion is detected by batch season re-sync."
        )

    # ------------------------------------------------------------------

    async def _resolve_season_id(self, season: int) -> str:
        wanted = self._season_name or str(season)
        for entry in await self.get_seasons():
            if entry.get("name") == wanted:
                return entry["id"]
        raise ValueError(f"Season {wanted} not found at the provider")

    async def _resolve_competition_id(self, season_slug: str) -> str:
        payload = await self._fetch_json("matches", {"season_slug": season_slug})
        for competition in payload.get("competitions", []):
            if competition.get("name") == self.competition_name:
                return competition["id"]
        raise ValueError(
            f"Competition {self.competition_name!r} not found for season {season_slug}"
        )

    def _match_to_fixture(self, match: Dict[str, Any], season: int) -> FixtureDTO:
        home = (match.get("home") or {}).get("name")
        away = (match.get("away") or {}).get("name")
        starts_at: Optional[datetime] = None
        raw_start = match.get("start_datetime")
        if raw_start:
            # "2026-04-03T05:10:00.000000Z"
            starts_at = datetime.fromisoformat(
                str(raw_start).replace("Z", "+00:00")
            )

        return FixtureDTO(
            source=self.source,
            external_id=match.get("id"),
            season=season,
            round_id=round_id_from_slug((match.get("round") or {}).get("slug")),
            home_participant=home or None,
            away_participant=away or None,
            home_score=match.get("home_4_total"),
            away_score=match.get("away_4_total"),
            venue=(match.get("venue") or {}).get("name"),
            starts_at=starts_at,
            completed=bool(match.get("completed")),
        )


__all__ = ["SportixProvider", "round_id_from_slug", "FINALS_ROUND_MAP"]
