"""CRUD for ``game_odds`` snapshots (BT-ODDS)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import GameOdds

DEFAULT_SOURCE = "the-odds-api"


class GameOddsCRUD:
    """Upsert-style access to per-game bookmaker odds snapshots."""

    @staticmethod
    async def get_for_game(
        db: AsyncSession, game_id: int, source: str = DEFAULT_SOURCE
    ) -> Optional[GameOdds]:
        result = await db.execute(
            select(GameOdds).where(GameOdds.game_id == game_id, GameOdds.source == source).limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def upsert(
        db: AsyncSession,
        *,
        game_id: int,
        home_odds: Optional[float],
        away_odds: Optional[float],
        bookmaker: Optional[str],
        captured_at: Optional[datetime] = None,
        source: str = DEFAULT_SOURCE,
    ) -> GameOdds:
        """Insert or refresh the snapshot for one (game, source).

        The daily job upserts the latest snapshot, so by game day the
        stored price approximates closing odds — what a bet placed at
        the bounce would actually pay.
        """
        row = await GameOddsCRUD.get_for_game(db, game_id, source)
        captured = captured_at or datetime.now(timezone.utc)

        if row is None:
            row = GameOdds(
                game_id=game_id,
                source=source,
                home_odds=home_odds,
                away_odds=away_odds,
                bookmaker=bookmaker,
                captured_at=captured,
            )
            db.add(row)
        else:
            row.home_odds = home_odds
            row.away_odds = away_odds
            row.bookmaker = bookmaker
            row.captured_at = captured

        await db.flush()
        return row


__all__ = ["GameOddsCRUD"]
