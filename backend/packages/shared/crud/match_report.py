"""CRUD operations for stored match reports (grand-final pre-match)."""

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import MatchReport


class MatchReportCRUD:
    """CRUD operations for stored match reports."""

    @staticmethod
    async def get_by_game_id(db: AsyncSession, game_id: int) -> MatchReport | None:
        """Get the match report for a game (one report per game)."""
        result = await db.execute(select(MatchReport).where(MatchReport.game_id == game_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def create_or_update(
        db: AsyncSession, game_id: int, report_type: str, report: dict[str, Any]
    ) -> MatchReport:
        """Create or update the match report stored for a game."""
        existing = await MatchReportCRUD.get_by_game_id(db, game_id)
        if existing:
            existing.report_type = report_type
            existing.report = report
            await db.commit()
            await db.refresh(existing)
            return existing

        row = MatchReport(game_id=game_id, report_type=report_type, report=report)
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row

    @staticmethod
    async def delete_for_game(db: AsyncSession, game_id: int) -> int:
        """Delete the match report for a game (if any). Returns rows deleted.

        Used by the admin regenerate endpoint so the next generation run
        cannot hit the service's skip-if-exists path.
        """
        count_result = await db.execute(
            select(func.count()).select_from(MatchReport).where(MatchReport.game_id == game_id)
        )
        count = count_result.scalar() or 0

        await db.execute(delete(MatchReport).where(MatchReport.game_id == game_id))
        await db.commit()
        return count
