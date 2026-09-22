"""Sport-generic CRUD + participant identity (P1-5 / P1-6, ADR 0001).

The defining property of this layer: **scoping is mandatory**.  Every
multi-row lookup takes ``season_id`` as a required keyword-only
argument — an unscoped query is a ``TypeError`` at the call site
(compile-time-visible), never silent cross-sport row mixing at runtime.
The legacy AFL CRUD keeps its legacy signatures until the cutover.

``ParticipantResolver`` replaces ``canonical_team()`` on write paths:
identity is resolved against the ``participants``/``team_aliases``
tables, with the teams.py canonical map as a transitional AFL fallback
until aliases are fully seeded (ADR 0001 / P1-6).
"""

from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..logger import get_logger
from ..models import (
    Event,
    Participant,
    Season,
    Team,
    TeamAlias,
)

logger = get_logger(__name__)


class EventCRUD:
    """Read access to ``events`` — every multi-row lookup is scoped."""

    @staticmethod
    async def get_by_season(
        db: AsyncSession, *, season_id: int, limit: Optional[int] = None
    ) -> list[Event]:
        stmt = select(Event).where(Event.season_id == season_id).order_by(Event.starts_at)
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_by_round(
        db: AsyncSession, *, season_id: int, round_id: int
    ) -> list[Event]:
        result = await db.execute(
            select(Event)
            .where(Event.season_id == season_id, Event.round_id == round_id)
            .order_by(Event.starts_at)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_upcoming(
        db: AsyncSession, *, season_id: int, limit: int = 50
    ) -> list[Event]:
        result = await db.execute(
            select(Event)
            .where(
                Event.season_id == season_id,
                Event.completed.is_(False),
                Event.status == "scheduled",
            )
            .order_by(Event.starts_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_slug(db: AsyncSession, slug: str) -> Optional[Event]:
        result = await db.execute(select(Event).where(Event.slug == slug))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_participants(
        db: AsyncSession, *, event_id: int
    ) -> list["EventParticipantRow"]:  # noqa: F821 - forward typing hint only
        from ..models import EventParticipant

        result = await db.execute(
            select(EventParticipant).where(EventParticipant.event_id == event_id)
        )
        return list(result.scalars().all())  # type: ignore[return-value]


class ParticipantResolver:
    """Resolve raw feed/source names to ``participants`` rows.

    Resolution order:
      1. exact (sport, kind='team', name) match;
      2. ``team_aliases`` lookup scoped to the sport;
      3. ``canonical_team()`` AFL fallback → retry step 1 (transitional
         safety net until aliases are seeded — see ADR 0001 / P1-6).
    """

    def __init__(self, db: AsyncSession, sport_id: str):
        self.db = db
        self.sport_id = sport_id

    async def resolve(self, name: Optional[str]) -> Optional[Participant]:
        from ..teams import canonical_team

        if not name or not name.strip():
            return None
        stripped = name.strip()

        # 1. exact name
        result = await self.db.execute(
            select(Participant).where(
                Participant.sport_id == self.sport_id,
                Participant.kind == "team",
                Participant.name == stripped,
            )
        )
        participant = result.scalar_one_or_none()
        if participant is not None:
            return participant

        # 2. alias table
        result = await self.db.execute(
            select(Participant)
            .join(TeamAlias, TeamAlias.team_participant_id == Participant.id)
            .where(
                Participant.sport_id == self.sport_id,
                TeamAlias.alias == stripped,
            )
        )
        participant = result.scalar_one_or_none()
        if participant is not None:
            return participant

        # 3. AFL canonical fallback (transitional)
        canonical = canonical_team(stripped)
        if canonical and canonical != stripped:
            result = await self.db.execute(
                select(Participant).where(
                    Participant.sport_id == self.sport_id,
                    Participant.kind == "team",
                    Participant.name == canonical,
                )
            )
            return result.scalar_one_or_none()

        return None

    async def ensure_team(
        self, name: str, *, aliases: Iterable[str] = ()
    ) -> Participant:
        """Resolve or create a team participant, registering aliases."""
        existing = await self.resolve(name)
        if existing is not None:
            return existing

        from ..teams import canonical_team

        canonical = canonical_team(name) or name.strip()
        participant = Participant(sport_id=self.sport_id, kind="team", name=canonical)
        self.db.add(participant)
        await self.db.flush()  # assign id

        team = Team(participant_id=participant.id)
        self.db.add(team)

        for alias in aliases:
            alias_value = (alias or "").strip()
            if not alias_value or alias_value == canonical:
                continue
            self.db.add(
                TeamAlias(team_participant_id=participant.id, alias=alias_value)
            )

        logger.debug(
            "ParticipantResolver: created team participant %s (sport=%s)",
            canonical,
            self.sport_id,
        )
        return participant


__all__ = ["EventCRUD", "ParticipantResolver"]
