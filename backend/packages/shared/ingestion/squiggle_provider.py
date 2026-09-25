"""SquiggleProvider — the AFL bootstrap FeedProvider (P3-1).

Owns the entire Squiggle vendor dialect:

* field names (``hteam/ateam/hscore/ascore/year/round/venue/date``)
* the completion sentinel (``complete: 100`` means final — see
  :func:`parse_squiggle_complete`)
* ISO-Z datetime parsing

No other module may reference a Squiggle field name.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..squiggle.client import SquiggleClient
from ..squiggle.utils import parse_squiggle_complete
from .dto import FixtureDTO


def fixture_from_squiggle(data: dict) -> FixtureDTO:
    """Translate one Squiggle game payload into a canonical DTO."""
    starts_at: Optional[datetime] = None
    raw_date = data.get("date")
    if raw_date:
        starts_at = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))

    home = data.get("hteam")
    away = data.get("ateam")

    return FixtureDTO(
        source="squiggle",
        external_id=data.get("id"),
        season=data.get("year") or 0,
        round_id=data.get("round") or 0,
        home_participant=home if home else None,
        away_participant=away if away else None,
        home_score=data.get("hscore"),
        away_score=data.get("ascore"),
        venue=data.get("venue"),
        starts_at=starts_at,
        completed=parse_squiggle_complete(data.get("complete", False)),
    )


class SquiggleProvider:
    """FeedProvider over the Squiggle AFL API."""

    sport_id = "afl"

    def __init__(self, client: Optional[SquiggleClient] = None):
        self._client = client or SquiggleClient()

    @property
    def client(self) -> SquiggleClient:
        """The underlying client — transitional access for callers not
        yet on the provider boundary (match-completion rework pending)."""
        return self._client

    async def get_fixtures(
        self,
        season: int,
        *,
        round: Optional[int] = None,
        complete: Optional[bool] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[FixtureDTO]:
        raw = await self._client.get_games(
            year=season,
            round=round,
            complete=complete,
            start_date=start_date,
            end_date=end_date,
        )
        return [fixture_from_squiggle(game) for game in raw]

    async def get_fixture(self, external_id: int) -> Optional[FixtureDTO]:
        raw = await self._client.get_game(external_id)
        if not raw:
            return None
        return fixture_from_squiggle(raw)


__all__ = ["SquiggleProvider", "fixture_from_squiggle"]
