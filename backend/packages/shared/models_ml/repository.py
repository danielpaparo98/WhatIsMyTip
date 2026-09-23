"""Game-history repository — the point-in-time seam between prediction
models and storage (P2-4).

THE rule (previously re-implemented with slight variations inside every
model, and enforced here in exactly one place):

* every returned game is ``completed``;
* every returned game is strictly BEFORE the reference date.

Models receive a repository instead of hand-writing SQL against a raw
``AsyncSession``.  The legacy per-model queries migrate to this seam
incrementally (``FormModel`` first); models keep accepting ``db`` for
backward compatibility until the orchestrator hands out repositories.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence, runtime_checkable

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Game


@runtime_checkable
class GameHistoryRepository(Protocol):
    """Point-in-time game-history queries for prediction models."""

    async def recent_games_for_participant(
        self,
        participant: str,
        *,
        before: datetime,
        limit: int = 5,
    ) -> Sequence[Game]: ...


class SqlGameHistoryRepository:
    """The single SQL implementation of :class:`GameHistoryRepository`."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def recent_games_for_participant(
        self,
        participant: str,
        *,
        before: datetime,
        limit: int = 5,
    ) -> Sequence[Game]:
        result = await self._db.execute(
            select(Game)
            .where(
                Game.completed,
                Game.date < before,
                or_(
                    Game.home_team == participant,
                    Game.away_team == participant,
                ),
            )
            .order_by(Game.date.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


__all__ = ["GameHistoryRepository", "SqlGameHistoryRepository"]
