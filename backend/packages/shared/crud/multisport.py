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

from typing import Dict, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..logger import get_logger
from ..models import (
    Event,
    EventParticipant,
    EventSourceRef,
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

    @staticmethod
    async def upsert_fixture(
        db: AsyncSession,
        *,
        season_id: int,
        fixture: "FixtureDTO",
        home_participant_id: Optional[int],
        away_participant_id: Optional[int],
    ) -> Optional[Event]:
        """Insert or update an event from a canonical fixture DTO.

        Phase 5: the FIRST write path onto the events tables — used by
        the local-competition sync (WAFL pilot).

        Resolution order:
          1. source ref ``(fixture.source, fixture.external_id)``;
          2. natural key ``(season_id, 'match', round_id, starts_at, venue)``;
          3. create new.

        ``fixture.starts_at`` must already be converted to the
        competition's venue-local naive form (the sync service does the
        tz conversion — this layer stays tz-agnostic).  Idempotent:
        re-syncing updates in place and never duplicates.
        """
        import hashlib
        from datetime import datetime, timezone

        from ..models import EventParticipant, EventSourceRef

        now = datetime.now(timezone.utc)
        event: Optional[Event] = None

        # 1. source ref (fast path on re-sync)
        if fixture.external_id is not None:
            ref = (
                await db.execute(
                    select(EventSourceRef).where(
                        EventSourceRef.source == fixture.source,
                        EventSourceRef.external_id == str(fixture.external_id),
                    )
                )
            ).scalar_one_or_none()
            if ref is not None:
                event = (
                    await db.execute(select(Event).where(Event.id == ref.event_id))
                ).scalar_one_or_none()

        # 2. natural key
        if event is None:
            event = (
                await db.execute(
                    select(Event).where(
                        Event.season_id == season_id,
                        Event.event_type == "match",
                        Event.round_id == fixture.round_id,
                        Event.starts_at == fixture.starts_at,
                        Event.venue == fixture.venue,
                    )
                )
            ).scalar_one_or_none()

        has_result = (
            fixture.home_score is not None
            and fixture.away_score is not None
            and fixture.home_score != fixture.away_score
        )
        winning_side = None
        if has_result:
            winning_side = (
                "home" if fixture.home_score > fixture.away_score else "away"
            )

        if event is not None:
            # ---- update in place -----------------------------------
            # NOTE: scores live on EventParticipant sides, never on the
            # event row (ADR 0001).
            changed = False
            new_completed = bool(fixture.completed)
            if event.completed != new_completed:
                event.completed = new_completed
                changed = True
            new_status = "completed" if fixture.completed else "scheduled"
            if event.status != new_status:
                event.status = new_status
                changed = True
            if changed:
                event.sync_version = (event.sync_version or 0) + 1
            event.last_synced_at = now
        else:
            # ---- create --------------------------------------------
            digest = hashlib.sha1(
                f"{fixture.source}:{fixture.external_id}".encode()
            ).hexdigest()[:8]
            slug = f"{fixture.source[:4]}-{digest}"
            event = Event(
                season_id=season_id,
                event_type="match",
                round_id=fixture.round_id,
                venue=fixture.venue,
                starts_at=fixture.starts_at,
                status="completed" if fixture.completed else "scheduled",
                completed=bool(fixture.completed),
                slug=slug,
                last_synced_at=now,
                sync_version=1,
            )
            db.add(event)
            await db.flush()

            if fixture.external_id is not None:
                db.add(
                    EventSourceRef(
                        event_id=event.id,
                        source=fixture.source,
                        external_id=str(fixture.external_id),
                    )
                )

        # ---- event participants (sides) ----------------------------
        existing_sides = (
            await db.execute(
                select(EventParticipant).where(
                    EventParticipant.event_id == event.id
                )
            )
        ).scalars().all()
        sides_by_participant = {s.participant_id: s for s in existing_sides}

        for side, name, score, pid in (
            ("home", fixture.home_participant, fixture.home_score, home_participant_id),
            ("away", fixture.away_participant, fixture.away_score, away_participant_id),
        ):
            if pid is None:
                continue  # TBC side — no participant identity yet
            is_winner = (side == winning_side) if has_result else None
            row = sides_by_participant.get(pid)
            if row is not None:
                row.score = score
                row.is_winner = is_winner
            else:
                db.add(
                    EventParticipant(
                        event_id=event.id,
                        participant_id=pid,
                        side=side,
                        score=score,
                        is_winner=is_winner,
                    )
                )

        await db.flush()
        return event


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
        self,
        name: str,
        *,
        aliases: Iterable[str] = (),
        logo_url: Optional[str] = None,
        primary_color: Optional[str] = None,
        secondary_color: Optional[str] = None,
    ) -> Participant:
        """Resolve or create a team participant, registering aliases.

        Identity kwargs (logo URL / colours, migration 0011) are written
        onto the Team row on create; on an EXISTING team they only
        backfill fields still NULL — a value already on file is never
        clobbered by a later sync.
        """
        identity: Dict[str, str] = {}
        for field, value in (
            ("logo_url", logo_url),
            ("primary_color", primary_color),
            ("secondary_color", secondary_color),
        ):
            if value:
                identity[field] = value

        existing = await self.resolve(name)
        if existing is not None:
            if identity:
                await self._backfill_team_identity(
                    int(existing.id), identity
                )
            return existing

        from ..teams import canonical_team

        canonical = canonical_team(name) or name.strip()
        participant = Participant(sport_id=self.sport_id, kind="team", name=canonical)
        self.db.add(participant)
        await self.db.flush()  # assign id

        team = Team(participant_id=participant.id, **identity)
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

    async def _backfill_team_identity(
        self, participant_id: int, identity: Dict[str, str]
    ) -> None:
        """Fill NULL identity fields on an existing team's Team row."""
        result = await self.db.execute(
            select(Team).where(Team.participant_id == participant_id)
        )
        team = result.scalar_one_or_none()
        if team is None:
            # Data drift: a team participant without its extension row.
            self.db.add(Team(participant_id=participant_id, **identity))
            await self.db.flush()
            return
        changed = False
        for field, value in identity.items():
            if getattr(team, field) is None:
                setattr(team, field, value)
                changed = True
        if changed:
            await self.db.flush()


__all__ = ["EventCRUD", "ParticipantResolver"]
