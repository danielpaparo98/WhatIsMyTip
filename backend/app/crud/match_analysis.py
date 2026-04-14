from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import MatchAnalysis


class MatchAnalysisCRUD:
    """CRUD operations for match analyses."""

    @staticmethod
    async def get_by_game_id(db: AsyncSession, game_id: int) -> MatchAnalysis | None:
        """Get match analysis by game ID."""
        result = await db.execute(select(MatchAnalysis).where(MatchAnalysis.game_id == game_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def create(db: AsyncSession, game_id: int, analysis_text: str) -> MatchAnalysis:
        """Create a new match analysis."""
        analysis = MatchAnalysis(game_id=game_id, analysis_text=analysis_text)
        db.add(analysis)
        await db.commit()
        await db.refresh(analysis)
        return analysis

    @staticmethod
    async def create_or_update(db: AsyncSession, game_id: int, analysis_text: str) -> MatchAnalysis:
        """Create or update a match analysis for a game."""
        existing = await MatchAnalysisCRUD.get_by_game_id(db, game_id)
        if existing:
            existing.analysis_text = analysis_text
            await db.commit()
            await db.refresh(existing)
            return existing
        return await MatchAnalysisCRUD.create(db, game_id, analysis_text)
