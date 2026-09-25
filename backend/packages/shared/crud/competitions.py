"""CRUD for competition/season registration (Phase 5 — local AFL expansion).

``ensure_competition`` / ``ensure_season`` are IDEMPOTENT registrars:
calling them repeatedly (e.g. from a seeding script or a sync job)
returns the existing rows instead of duplicating them — the property
that makes "add a competition" a data-entry task (ADR 0001 / D3).
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..logger import get_logger
from ..models import Competition, Season, Sport

logger = get_logger(__name__)

_VALID_TIERS = {"national", "state", "local"}


class CompetitionCRUD:
    """Idempotent registration of competitions and seasons."""

    @staticmethod
    async def ensure_competition(
        db: AsyncSession,
        *,
        sport_id: str,
        name: str,
        tier: str = "state",
        format: str = "rounds",
        timezone: str = "Australia/Perth",
    ) -> Competition:
        """Return the named competition, creating it if missing."""
        if tier not in _VALID_TIERS:
            raise ValueError(
                f"Invalid competition tier {tier!r} — must be one of {sorted(_VALID_TIERS)}"
            )

        # The sport must exist — a competition under an unregistered
        # sport is always a configuration error.
        sport = (
            await db.execute(select(Sport).where(Sport.id == sport_id))
        ).scalar_one_or_none()
        if sport is None:
            raise ValueError(
                f"Unknown sport {sport_id!r} — register it in the sports table first"
            )

        existing = (
            await db.execute(
                select(Competition).where(
                    Competition.sport_id == sport_id,
                    Competition.name == name,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        competition = Competition(
            sport_id=sport_id,
            name=name,
            tier=tier,
            format=format,
            timezone=timezone,
        )
        db.add(competition)
        await db.flush()
        await db.refresh(competition)
        logger.info(
            "Registered competition %s (sport=%s tier=%s)", name, sport_id, tier
        )
        return competition

    @staticmethod
    async def ensure_season(
        db: AsyncSession,
        *,
        competition_id: int,
        label: str,
        is_current: Optional[bool] = None,
    ) -> Season:
        """Return the labelled season of a competition, creating it if missing."""
        existing = (
            await db.execute(
                select(Season).where(
                    Season.competition_id == competition_id,
                    Season.label == label,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if is_current is not None and is_current != existing.is_current:
                existing.is_current = is_current
                await db.flush()
            return existing

        season = Season(
            competition_id=competition_id,
            label=label,
            is_current=bool(is_current),
        )
        db.add(season)
        await db.flush()
        await db.refresh(season)
        logger.info(
            "Registered season %s for competition %s", label, competition_id
        )
        return season


__all__ = ["CompetitionCRUD"]
