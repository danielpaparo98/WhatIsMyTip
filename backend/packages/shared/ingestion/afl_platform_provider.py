"""AflPlatformProvider — client for the AFL's public v2 match API
(aflapi.afl.com.au/afl/v2), which serves the AFL-run state/national
competitions without auth.

One provider instance per competition — a registry entry, not new code:

    AflPlatformProvider(competition_id=7, competition_name="VFL")

Competition ids (verified live 2026-09-24): VFL=7, AFLW=3, VFLW=11,
SANFL=14.

API shape (verified live):

* ``GET competitions/{id}/compseasons?pageSize=50``  → ``{"compSeasons":
  [{id, name: "2026 VFL Premiership Season", ...}]}``
* ``GET matches?competitionId=&compSeasonId=&pageSize=300&pageNum=1``
  → ``{"meta": ..., "matches": [...]}``
* ``GET matches/{id}`` → single match document (re-sync helper)

Season resolution: the season ``name`` is the full title ("2026 VFL
Premiership Season"), not the bare year, so an entry matching
``str(season)`` exactly wins, otherwise the first entry whose name
starts with the year (list is newest-first).

Match mapping notes (real payload): ``utcStartTime`` looks like
``"2026-03-20T01:35:00.000+0000"`` and stays UTC-aware — venue-local
conversion is the sync service's job.  SCHEDULED/PLACEHOLDER matches
carry no ``score`` blocks; ``completed`` is true only for
``status == "CONCLUDED"``.  ``external_id`` carries the provider id as
a string, per the provider-assigned-id convention used by the other
feed providers.

Team identity (migration 0011, verified live 2026-09-24): the v2
surface carries NO logo or colour data.  ``GET /teams?compSeasonId=``
exposes only id/providerId/name/abbreviation/nickname/club
(id/providerId/name/abbreviation/nickname)/metadata (always ``{}``
across all 22 VFL teams)/teamType — no ``logoUrl``, ``uuid``,
``image`` or colour keys; ``/matches`` embeds the same team blocks,
and the single-team (``/teams/{id}``) and ``/clubs`` endpoints are
equally bare.  Per the ingestion policy (never guess at unstable CDN
paths), this provider offers NO ``get_team_metadata``: AFL-platform
teams keep NULL identity and the frontend's hard-coded AFL logo/
colour maps remain the fallback.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..logger import get_logger
from .dto import FixtureDTO

logger = get_logger(__name__)

# '+0000' / '+1000' style offsets → '+00:00' for fromisoformat.
_TZ_OFFSET_RE = re.compile(r"([+-]\d{2}:\d{2}|[+-]\d{4})$")


def parse_afl_utc(raw: Optional[str]) -> Optional[datetime]:
    """Parse an AFL-platform ``utcStartTime`` into a UTC-aware datetime.

    ``"2026-03-20T01:35:00.000+0000"`` → ``datetime(2026, 3, 20, 1, 35,
    tzinfo=utc)``.  Returns ``None`` for absent/empty values.
    """
    if not raw:
        return None
    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    def _colon(match):
        token = match.group(1)
        return token if ":" in token else f"{token[:3]}:{token[3:]}"

    return datetime.fromisoformat(_TZ_OFFSET_RE.sub(_colon, text))


class AflPlatformProvider:
    """FeedProvider for one competition on the AFL platform (aflapi)."""

    sport_id = "afl"
    source = "aflapi"

    def __init__(
        self,
        *,
        competition_id: int,
        competition_name: str = "",
        base_url: str = "https://aflapi.afl.com.au/afl/v2",
        fetch_json: Optional[
            Callable[[str, Dict[str, Any]], Awaitable[Any]]
        ] = None,
    ):
        self.competition_id = competition_id
        self.competition_name = competition_name
        self._base_url = base_url.rstrip("/")
        self._fetch_json = fetch_json or self._default_fetch

    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            # Open endpoint — no auth; identify the bot politely.
            "User-Agent": "WhatIsMyTip (contact@whatismytip.com)",
        }

    async def _default_fetch(self, path: str, params: Dict[str, Any]) -> Any:
        import httpx

        async with httpx.AsyncClient(
            timeout=30.0, verify=True, headers=self._headers()
        ) as client:
            response = await client.get(f"{self._base_url}/{path}", params=params)
            response.raise_for_status()
            return response.json()

    # ------------------------------------------------------------------

    async def get_seasons(self) -> List[Dict[str, Any]]:
        """CompSeasons for the configured competition (newest first)."""
        payload = await self._fetch_json(
            f"competitions/{self.competition_id}/compseasons", {"pageSize": 50}
        )
        # Live key is 'compSeasons'; tolerate 'seasons' for safety.
        return list(payload.get("compSeasons") or payload.get("seasons") or [])

    async def get_fixtures(
        self,
        season: int,
        *,
        round: Optional[int] = None,  # shadows builtin, FeedProvider protocol
        complete: Optional[bool] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[FixtureDTO]:
        """Full season of fixtures for the configured competition."""
        season_id = await self._resolve_season_id(season)
        payload = await self._fetch_json(
            "matches",
            {
                "competitionId": self.competition_id,
                "compSeasonId": season_id,
                "pageSize": 300,
                "pageNum": 1,
            },
        )
        return [
            self._match_to_fixture(match, season)
            for match in payload.get("matches", [])
        ]

    async def get_fixture(self, external_id: int | str) -> Optional[FixtureDTO]:
        """Single match via the documented ``GET matches/{id}`` helper.

        Returns ``None`` on a 404 (retired/never-existed id).
        """
        import httpx

        try:
            payload = await self._fetch_json(f"matches/{external_id}", {})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

        match: Any = payload
        if isinstance(payload, dict):
            if isinstance(payload.get("matches"), list) and payload["matches"]:
                match = payload["matches"][0]
            elif "match" in payload:
                match = payload["match"]
        if not isinstance(match, dict) or "id" not in match:
            logger.warning(
                "aflapi single match %s returned an unrecognised payload", external_id
            )
            return None
        return self._match_to_fixture(match, self._season_from_match(match))

    # ------------------------------------------------------------------

    async def _resolve_season_id(self, season: int) -> int:
        wanted = str(season)
        entries = await self.get_seasons()
        # Exact name match wins ("2026"), then year prefix ("2026 VFL
        # Premiership Season").  The list is newest-first, so for the
        # AFLW split-year duplicates the first entry is the later one.
        for entry in entries:
            if entry.get("name") == wanted:
                return int(entry["id"])
        for entry in entries:
            name = str(entry.get("name") or "")
            if name.startswith(f"{wanted} ") or name.startswith(f"{wanted}-"):
                return int(entry["id"])
        raise ValueError(
            f"Season {wanted} not found for competition "
            f"{self.competition_id} at the provider"
        )

    @staticmethod
    def _season_from_match(match: Dict[str, Any]) -> int:
        """Derive the season for a single-match document: compSeason
        name prefix first ("2026 VFL Premiership Season" → 2026), then
        the start time's year as a fallback."""
        name = str(((match.get("compSeason") or {}).get("name")) or "")
        if name[:4].isdigit():
            return int(name[:4])
        start = parse_afl_utc(match.get("utcStartTime"))
        if start is not None:
            return start.year
        raise ValueError(
            f"Cannot derive season from match {match.get('id')!r} "
            "(no compSeason name or utcStartTime)"
        )

    def _match_to_fixture(self, match: Dict[str, Any], season: int) -> FixtureDTO:
        home = match.get("home") or {}
        away = match.get("away") or {}

        def _team(side: Dict[str, Any]) -> Optional[str]:
            return ((side.get("team") or {}).get("name")) or None

        def _score(side: Dict[str, Any]) -> Optional[int]:
            # SCHEDULED/PLACEHOLDER matches have no score block at all.
            score = side.get("score")
            if isinstance(score, dict):
                return score.get("totalScore")
            return None

        round_block = match.get("round") or {}

        return FixtureDTO(
            source=self.source,
            external_id=str(match["id"]),
            season=season,
            round_id=round_block.get("roundNumber"),
            home_participant=_team(home),
            away_participant=_team(away),
            home_score=_score(home),
            away_score=_score(away),
            venue=((match.get("venue") or {}).get("name")) or None,
            starts_at=parse_afl_utc(match.get("utcStartTime")),
            completed=(match.get("status") == "CONCLUDED"),
        )


__all__ = ["AflPlatformProvider", "parse_afl_utc"]
