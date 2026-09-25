"""CRUD for read-side access to the 0010 events tables (ADR 0001).

The first read-side cutover increment: serves the new event-scoped
public routes so multi-league data becomes reachable by the site.
Write-side access lives in :mod:`.multisport` (``EventCRUD``).

Queries are batched — events, then their participants in a single
``IN`` query — so listing a round costs three queries total
(season+competition resolution, events, participants) regardless of
how many events are returned (no N+1).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..logger import get_logger
from ..models import Competition, Event, EventParticipant, Participant, Season

logger = get_logger(__name__)


class EventsCRUD:
    """Read access to events joined with their participants."""

    @staticmethod
    async def get_by_competition_season(
        db: AsyncSession,
        competition_id: int,
        season_label: str,
        round_id: Optional[int] = None,
        limit: int = 100,
    ) -> Optional[List[Dict[str, Any]]]:
        """Events (with participants) for one competition season.

        Resolves the ``seasons`` row scoped by ``competition_id`` +
        ``season_label`` — returns ``None`` when either the competition
        or the season does not exist (the router maps that to 404); an
        empty list means the season simply has no events yet.
        """
        season_stmt = (
            select(Season, Competition)
            .join(Competition, Season.competition_id == Competition.id)
            .where(Competition.id == competition_id, Season.label == season_label)
        )
        row = (await db.execute(season_stmt)).first()
        if row is None:
            return None
        season, competition = row

        conditions = [Event.season_id == season.id]
        if round_id is not None:
            conditions.append(Event.round_id == round_id)
        events_stmt = (
            select(Event)
            .where(*conditions)
            .order_by(Event.starts_at.asc(), Event.id.asc())
            .limit(limit)
        )
        events = (await db.execute(events_stmt)).scalars().all()

        participants_by_event = await EventsCRUD._participants_for_events(
            db, [event.id for event in events]
        )

        return [
            EventsCRUD._to_dict(event, competition.name, season.label, participants_by_event)
            for event in events
        ]

    @staticmethod
    async def get_latest_season_label(
        db: AsyncSession, competition_id: int
    ) -> Optional[str]:
        """The latest season label of a competition, by label ordering.

        Used by the legacy ``/api/games`` route to default
        ``season_label`` when only ``competition`` is given.  Returns
        ``None`` when the competition does not exist (or has no seasons
        yet) — the router maps that to 404.
        """
        stmt = (
            select(Season.label)
            .where(Season.competition_id == competition_id)
            .order_by(Season.label.desc())
            .limit(1)
        )
        row = (await db.execute(stmt)).first()
        return row[0] if row else None

    @staticmethod
    async def get_by_slug_with_participants(
        db: AsyncSession, slug: str
    ) -> Optional[Dict[str, Any]]:
        """A single event (with participants) by its public slug.

        Returns ``None`` when no event exists for ``slug``.  The
        competition/season names are resolved via joins in the same
        query, so this costs exactly two queries.
        """
        event_stmt = (
            select(Event, Season.label, Competition.name)
            .join(Season, Event.season_id == Season.id)
            .join(Competition, Season.competition_id == Competition.id)
            .where(Event.slug == slug)
        )
        row = (await db.execute(event_stmt)).first()
        if row is None:
            return None
        event, season_label, competition_name = row

        participants_by_event = await EventsCRUD._participants_for_events(
            db, [event.id]
        )

        return EventsCRUD._to_dict(
            event, competition_name, season_label, participants_by_event
        )

    # -- internals ----------------------------------------------------------

    @staticmethod
    async def _participants_for_events(
        db: AsyncSession, event_ids: List[int]
    ) -> Dict[int, List[Dict[str, Any]]]:
        """Batched participants lookup — one query for ALL the given
        events (the N+1 avoidance).  Rows are grouped by event id and
        keep their insertion order (home side is stored first).
        """
        participants_by_event: Dict[int, List[Dict[str, Any]]] = {}
        if not event_ids:
            return participants_by_event

        rows: List[Tuple[EventParticipant, str]] = (
            await db.execute(
                select(EventParticipant, Participant.name)
                .join(Participant, EventParticipant.participant_id == Participant.id)
                .where(EventParticipant.event_id.in_(event_ids))
                .order_by(EventParticipant.id.asc())
            )
        ).all()

        for event_participant, participant_name in rows:
            participants_by_event.setdefault(event_participant.event_id, []).append(
                {
                    "side": event_participant.side,
                    "participant_name": participant_name,
                    "score": event_participant.score,
                    "is_winner": event_participant.is_winner,
                }
            )
        return participants_by_event

    @staticmethod
    def _to_dict(
        event: Event,
        competition_name: str,
        season_label: str,
        participants_by_event: Dict[int, List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """API-shaped event dict matching :class:`EventResponse`."""
        return {
            "id": event.id,
            "slug": event.slug,
            "round_id": event.round_id,
            "venue": event.venue,
            "starts_at": event.starts_at,
            "status": event.status,
            "completed": event.completed,
            "competition": competition_name,
            "season": season_label,
            "participants": participants_by_event.get(event.id, []),
        }


__all__ = ["EventsCRUD"]
