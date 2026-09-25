"""ISportsProvider — client for the iSports platform that hosts the
Queensland competitions (stats.isports.net.au/api, open — no auth).

One provider instance per league:

    ISportsProvider(league_id=1)   # QAFL (QAFLW = 4)

API shape (verified live 2026-09-24):

* ``GET leagues/{id}/seasons``      → [{id, name: "2026", ...}] (bare list)
* ``GET seasons/{id}/matches``      → [{id, round, date, status,
  homeTeamId, awayTeamId, homeTeam{name}, awayTeam{name},
  location{name}, teamReports[{teamId, score, ...}], ...}] (bare list)
* ``GET seasons/{id}/teams``        → [{id, name, logo, seasonId}]

Team identity (migration 0011, verified live 2026-09-24): the
``/teams`` payload's ``logo`` is a bare filename
(``"<uuid>.png"``); the iSports SPA resolves it against
``https://storage-isports-prod.s3.ap-southeast-2.amazonaws.com/images``
(probed HTTP 200) — that pair is what
:meth:`ISportsProvider.get_team_metadata` emits.  The API exposes NO
per-team colours anywhere, so colours stay NULL and the frontend
fallbacks apply.

Dialect notes (real payload):

* Matches reference teams by ``homeTeamId``/``awayTeamId``, so the
  season's ``/teams`` payload is fetched and folded into an id→name
  map.  Newer payloads ALSO embed ``homeTeam``/``awayTeam`` objects —
  used only as a fallback when a team is missing from the map.
* Scores are NOT match fields: they arrive in ``teamReports`` entries
  keyed by ``teamId``.  A match counts as completed only when
  ``status == "PUBLISHED"`` and BOTH team reports carry a score.
* ``location.name`` (when present) is the venue; matches without a
  location block map to ``venue=None``.
* Single-fixture lookup is not offered by this API — completion is
  detected by batch season re-sync.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..logger import get_logger
from .dto import FixtureDTO

logger = get_logger(__name__)


def parse_isports_utc(raw: Optional[str]) -> Optional[datetime]:
    """Parse an iSports ``date`` ("2026-05-30T14:00:00.000Z") into a
    UTC-aware datetime.  Returns ``None`` for absent/empty values."""
    if not raw:
        return None
    return datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))


class ISportsProvider:
    """FeedProvider for one league on the iSports platform."""

    sport_id = "afl"
    source = "isports"

    #: Base for the bare logo filenames the ``/teams`` payload carries —
    #: the iSports SPA builds ``${base}/${logo}`` from exactly this S3
    #: images prefix (verified live 2026-09-24, HTTP 200).
    TEAM_LOGO_BASE_URL = (
        "https://storage-isports-prod.s3.ap-southeast-2.amazonaws.com/images"
    )

    def __init__(
        self,
        *,
        league_id: int,
        base_url: str = "https://stats.isports.net.au/api",
        fetch_json: Optional[
            Callable[[str, Dict[str, Any]], Awaitable[Any]]
        ] = None,
    ):
        self.league_id = league_id
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
        """Seasons for the configured league, e.g. ``[{'id': 54, 'name': '2026'}]``."""
        return list(await self._fetch_json(f"leagues/{self.league_id}/seasons", {}))

    async def get_fixtures(
        self,
        season: int,
        *,
        round: Optional[int] = None,  # shadows builtin, FeedProvider protocol
        complete: Optional[bool] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[FixtureDTO]:
        """Full season of fixtures for the configured league."""
        season_id = await self._resolve_season_id(season)
        matches = await self._fetch_json(f"seasons/{season_id}/matches", {})
        if isinstance(matches, dict):  # tolerate a wrapped payload
            matches = matches.get("matches") or []
        teams = await self._fetch_json(f"seasons/{season_id}/teams", {})
        if isinstance(teams, dict):
            teams = teams.get("teams") or []
        team_names = {
            team.get("id"): team.get("name")
            for team in teams
            if isinstance(team, dict)
        }
        return [
            self._match_to_fixture(match, season, team_names)
            for match in matches
        ]

    async def get_team_metadata(self, season: int) -> Dict[str, Dict[str, Any]]:
        """Team identity for one season (migration 0011): raw team name
        → ``{logo_url}``.

        Keys are the same raw names the match payloads use, so the sync
        service can look identity up per fixture side.  Teams without a
        logo are omitted; colours are left out entirely (the iSports API
        carries none — see the module docstring).
        """
        season_id = await self._resolve_season_id(season)
        teams = await self._fetch_json(f"seasons/{season_id}/teams", {})
        if isinstance(teams, dict):  # tolerate a wrapped payload
            teams = teams.get("teams") or []
        metadata: Dict[str, Dict[str, Any]] = {}
        for team in teams:
            if not isinstance(team, dict):
                continue
            name = team.get("name")
            logo = team.get("logo")
            if not name or not logo:
                continue
            metadata[str(name).strip()] = {
                "logo_url": f"{self.TEAM_LOGO_BASE_URL}/{str(logo).lstrip('/')}"
            }
        return metadata

    async def get_fixture(self, external_id: int | str) -> Optional[FixtureDTO]:
        """Not offered by the iSports API.

        There is no documented single-match endpoint that returns the
        list-shape document (``GET /api/matches/{id}`` exists but omits
        ``teamReports``, i.e. carries no scores).  Completion is
        detected by batch season re-sync instead.
        """
        raise NotImplementedError(
            "iSports offers no single-fixture lookup with scores; "
            "completion is detected by batch season re-sync."
        )

    # ------------------------------------------------------------------

    async def _resolve_season_id(self, season: int) -> int:
        wanted = str(season)
        for entry in await self.get_seasons():
            name = str(entry.get("name") or "")
            if name == wanted or name.startswith(f"{wanted} "):
                return int(entry["id"])
        raise ValueError(f"Season {wanted} not found at the provider")

    def _match_to_fixture(
        self, match: Dict[str, Any], season: int, team_names: Dict[Any, Any]
    ) -> FixtureDTO:
        home_id = match.get("homeTeamId")
        away_id = match.get("awayTeamId")

        def _team_name(team_id: Any, nested_key: str) -> Optional[str]:
            # id→name map is primary; embedded team object is a fallback.
            return team_names.get(team_id) or (
                (match.get(nested_key) or {}).get("name") or None
            )

        # Scores live in teamReports keyed by teamId — order is not
        # guaranteed to be home-first, so match on the id.
        home_score: Optional[int] = None
        away_score: Optional[int] = None
        for report in match.get("teamReports") or []:
            team_id = report.get("teamId")
            if team_id == home_id:
                home_score = report.get("score")
            elif team_id == away_id:
                away_score = report.get("score")

        return FixtureDTO(
            source=self.source,
            external_id=str(match["id"]),
            season=season,
            round_id=match.get("round"),
            home_participant=_team_name(home_id, "homeTeam"),
            away_participant=_team_name(away_id, "awayTeam"),
            home_score=home_score,
            away_score=away_score,
            venue=((match.get("location") or {}).get("name")) or None,
            starts_at=parse_isports_utc(match.get("date")),
            completed=(
                match.get("status") == "PUBLISHED"
                and home_score is not None
                and away_score is not None
            ),
        )


__all__ = ["ISportsProvider", "parse_isports_utc"]
