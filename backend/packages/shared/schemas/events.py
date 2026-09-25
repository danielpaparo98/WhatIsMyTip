"""Response schemas for the event-scoped read endpoints (ADR 0001).

The 0010 events tables are sport-generic: an ``Event`` has
``EventParticipant`` sides (home/away/``n/a``) instead of the legacy
``games.home_team/away_team`` columns, so participants are always a
nested list — team sports yield two entries, races/field events any
number.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class EventParticipantResponse(BaseModel):
    """One side of an event, with its score and result.

    ``is_winner`` is ``None`` for draws and unfinished events.
    """

    side: str  # home | away | n/a
    participant_name: str
    score: Optional[int] = None
    is_winner: Optional[bool] = None


class EventResponse(BaseModel):
    """An event plus its participants and competition/season names.

    ``competition``/``season`` are human-readable names resolved from
    the competitions/seasons framework tables (not raw FK ids).
    """

    id: int
    slug: str
    #: Round/week number; NULL for tournaments and race events.
    round_id: Optional[int] = None
    venue: Optional[str] = None
    #: Venue-local naive timestamp — interpreted via the competition
    #: timezone (see the multisport model docstring for the tz policy).
    starts_at: Optional[datetime] = None
    status: str  # scheduled | completed | cancelled | void
    completed: bool
    competition: str
    season: str
    participants: List[EventParticipantResponse] = []


class EventListResponse(BaseModel):
    events: List[EventResponse]
    count: int


__all__ = [
    "EventParticipantResponse",
    "EventResponse",
    "EventListResponse",
]
