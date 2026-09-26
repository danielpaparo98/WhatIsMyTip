"""Response schemas for the sport/competition/season framework (P4-1)."""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel


class SeasonResponse(BaseModel):
    id: int
    label: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_current: bool = False


class CompetitionResponse(BaseModel):
    id: int
    sport_id: str
    name: str
    #: national | state | local
    tier: str
    #: rounds | tournament
    format: str
    timezone: str
    seasons: List[SeasonResponse] = []


class SportResponse(BaseModel):
    id: str
    display_name: str
    competitions: List[CompetitionResponse] = []


class SportsListResponse(BaseModel):
    sports: List[SportResponse]


__all__ = [
    "SeasonResponse",
    "CompetitionResponse",
    "SportResponse",
    "SportsListResponse",
]
