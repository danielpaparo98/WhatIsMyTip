"""CRUD for the sport-generic framework tables (sports/competitions/seasons).

Read-only for now — sports and competitions are seeded by migration
0010 and administered out-of-band until a management surface exists.
"""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..logger import get_logger
from ..models import Competition, Season, Sport

logger = get_logger(__name__)


class SportsCRUD:
    """Read access to the sport/competition/season framework tables."""

    @staticmethod
    async def list_sports_with_competitions(db: AsyncSession) -> List[Dict[str, Any]]:
        """All sports with their competitions and seasons, API-shaped.

        Three ordered queries + in-memory grouping — the table sizes are
        tiny (tens of rows), so the simplicity wins over join plumbing.
        """
        sports = (
            (await db.execute(select(Sport).order_by(Sport.id))).scalars().all()
        )
        competitions = (
            (
                await db.execute(
                    select(Competition).order_by(Competition.sport_id, Competition.name)
                )
            )
            .scalars()
            .all()
        )
        seasons = (
            (await db.execute(select(Season).order_by(Season.label))).scalars().all()
        )

        seasons_by_competition: Dict[int, List[Dict[str, Any]]] = {}
        for season in seasons:
            seasons_by_competition.setdefault(season.competition_id, []).append(
                {
                    "id": season.id,
                    "label": season.label,
                    "start_date": season.start_date,
                    "end_date": season.end_date,
                    "is_current": season.is_current,
                }
            )

        competitions_by_sport: Dict[str, List[Dict[str, Any]]] = {}
        for competition in competitions:
            competitions_by_sport.setdefault(competition.sport_id, []).append(
                {
                    "id": competition.id,
                    "sport_id": competition.sport_id,
                    "name": competition.name,
                    "tier": competition.tier,
                    "format": competition.format,
                    "timezone": competition.timezone,
                    "seasons": seasons_by_competition.get(competition.id, []),
                }
            )

        return [
            {
                "id": sport.id,
                "display_name": sport.display_name,
                "competitions": competitions_by_sport.get(sport.id, []),
            }
            for sport in sports
        ]


__all__ = ["SportsCRUD"]
