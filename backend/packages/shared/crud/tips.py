from typing import List, Optional

from sqlalchemy import and_, delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..cache import cached, invalidate_cache_pattern, short_cache
from ..models import Tip
from ..teams import canonical_team


class TipCRUD:
    """CRUD operations for tips."""

    @staticmethod
    async def get_by_id(db: AsyncSession, tip_id: int) -> Optional[Tip]:
        """Get a tip by ID."""
        result = await db.execute(select(Tip).where(Tip.id == tip_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_game(db: AsyncSession, game_id: int) -> List[Tip]:
        """Get all tips for a game."""
        result = await db.execute(select(Tip).where(Tip.game_id == game_id).order_by(Tip.heuristic))
        return list(result.scalars().all())

    @staticmethod
    @cached(cache=short_cache, key_prefix="tips_by_heuristic:")
    async def get_by_heuristic(db: AsyncSession, heuristic: str, limit: int = 100) -> List[Tip]:
        """Get tips by heuristic type."""
        result = await db.execute(
            select(Tip)
            .where(Tip.heuristic == heuristic)
            .order_by(Tip.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    @cached(cache=short_cache, key_prefix="tips_by_round:")
    async def get_by_round(db: AsyncSession, season: int, round_id: int) -> List[Tip]:
        """Get all tips for a round."""
        from ..models import Game

        result = await db.execute(
            select(Tip)
            .join(Game, Tip.game_id == Game.id)
            .where(and_(Game.season == season, Game.round_id == round_id))
            .order_by(Tip.heuristic, Game.date)
        )
        return list(result.scalars().all())

    @staticmethod
    async def create(
        db: AsyncSession,
        game_id: int,
        heuristic: str,
        selected_team: str,
        margin: int,
        confidence: float,
        explanation: str,
    ) -> Tip:
        """Create a new tip with proper transaction management."""
        try:
            # Canonicalise so the backtest join never breaks on a stored alias.
            selected_team = canonical_team(selected_team)
            tip = Tip(
                game_id=game_id,
                heuristic=heuristic,
                selected_team=selected_team,
                margin=margin,
                confidence=confidence,
                explanation=explanation,
            )
            db.add(tip)
            await db.commit()
            await db.refresh(tip)

            # Invalidate cache for tip-related queries
            await invalidate_cache_pattern(short_cache, "tips_by_heuristic:")
            await invalidate_cache_pattern(short_cache, "tips_by_round:")

            return tip
        except Exception:
            await db.rollback()
            raise

    @staticmethod
    async def upsert(
        db: AsyncSession,
        game_id: int,
        heuristic: str,
        selected_team: str,
        margin: int,
        confidence: float,
        explanation: str,
    ) -> Tip:
        """Insert-or-update a tip keyed by ``(game_id, heuristic)``.

        Replaces the legacy delete-then-insert pattern that could race
        between concurrent ``regenerate_tips_for_round`` calls and blow
        up with ``IntegrityError`` on the
        ``uq_game_heuristic`` unique constraint.

        Uses Postgres' ``INSERT ... ON CONFLICT DO UPDATE`` so the
        upsert itself is atomic at the database level; no row is
        deleted before the insert, so two concurrent requests
        serialise cleanly instead of colliding.

        Returns the post-write :class:`Tip` row (refreshed so
        ``id``/``created_at`` are populated).
        """
        try:
            # Canonicalise so the backtest join never breaks on a stored alias.
            selected_team = canonical_team(selected_team)
            stmt = pg_insert(Tip).values(
                game_id=game_id,
                heuristic=heuristic,
                selected_team=selected_team,
                margin=margin,
                confidence=confidence,
                explanation=explanation,
            )
            # On conflict, refresh the mutable columns. We deliberately
            # do NOT touch ``id`` or ``created_at``; the existing row
            # keeps its original timestamps so historical tip lifetime
            # remains auditable.
            stmt = stmt.on_conflict_do_update(
                constraint="uq_game_heuristic",
                set_={
                    "selected_team": selected_team,
                    "margin": margin,
                    "confidence": confidence,
                    "explanation": explanation,
                },
            )
            await db.execute(stmt)
            await db.commit()

            # Re-fetch so callers get a fully-populated ORM object.
            row = await TipCRUD.get_by_game_and_heuristic(
                db, game_id=game_id, heuristic=heuristic
            )
            if row is None:
                # Defensive: should be unreachable since the upsert
                # just succeeded.
                raise RuntimeError(
                    f"Tip disappeared after upsert for "
                    f"(game_id={game_id}, heuristic={heuristic})"
                )

            # Invalidate cache for tip-related queries.
            await invalidate_cache_pattern(short_cache, "tips_by_heuristic:")
            await invalidate_cache_pattern(short_cache, "tips_by_round:")

            return row
        except Exception:
            await db.rollback()
            raise

    @staticmethod
    async def get_by_game_and_heuristic(
        db: AsyncSession, game_id: int, heuristic: str
    ) -> Optional[Tip]:
        """Return the single tip for ``(game_id, heuristic)`` or ``None``."""
        result = await db.execute(
            select(Tip).where(
                and_(Tip.game_id == game_id, Tip.heuristic == heuristic)
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create_batch(db: AsyncSession, tips_data: List[dict]) -> List[Tip]:
        """Create multiple tips in a single bulk insert operation.

        Args:
            db: Database session
            tips_data: List of dictionaries containing tip data

        Returns:
            List of created Tip objects
        """
        from ..cache import invalidate_cache_pattern

        try:
            # Canonicalise team names so the backtest join never breaks
            # on a stored alias. Operates on shallow copies to avoid
            # mutating the caller's dicts.
            tips_data = [
                {**d, "selected_team": canonical_team(d["selected_team"])}
                if "selected_team" in d
                else d
                for d in tips_data
            ]
            stmt = insert(Tip).values(tips_data).returning(Tip)
            result = await db.execute(stmt)
            await db.commit()

            # Invalidate cache for tip-related queries
            await invalidate_cache_pattern(short_cache, "tips_by_heuristic:")
            await invalidate_cache_pattern(short_cache, "tips_by_round:")

            return list(result.scalars().all())
        except Exception:
            await db.rollback()
            raise

    @staticmethod
    async def delete_for_game(db: AsyncSession, game_id: int) -> int:
        """Delete all tips for a game using a bulk DELETE."""
        from sqlalchemy import func

        from ..cache import invalidate_cache_pattern

        try:
            # Count before deleting
            count_result = await db.execute(select(func.count()).where(Tip.game_id == game_id))
            count = count_result.scalar() or 0

            await db.execute(delete(Tip).where(Tip.game_id == game_id))
            await db.commit()

            # Invalidate cache for tip-related queries
            await invalidate_cache_pattern(short_cache, "tips_by_heuristic:")
            await invalidate_cache_pattern(short_cache, "tips_by_round:")

            return count
        except Exception:
            await db.rollback()
            raise

    @staticmethod
    async def regenerate_tips_for_round(
        db: AsyncSession,
        season: int,
        round_id: int,
        heuristics: Optional[List[str]] = None,
        force: bool = False,
    ) -> dict:
        """Generate tips for a specific round using the ModelOrchestrator.

        Args:
            db: Database session
            season: Season year
            round_id: Round number
            heuristics: Optional list of heuristics to generate (default: all)

        Returns:
            Dict with generation results including tips count and heuristics used
        """
        from ..orchestrator import ModelOrchestrator
        from . import GameCRUD, ModelPredictionCRUD

        # Get games for round
        games = await GameCRUD.get_by_round(db, season, round_id)

        if not games:
            return {
                "success": False,
                "message": f"No games found for round {round_id}, season {season}",
                "tips_count": 0,
                "tips_created": 0,
                "tips_updated": 0,
                "tips_skipped": 0,
                "heuristics_used": [],
                "season": season,
                "round_id": round_id,
            }

        # Initialize orchestrator
        orchestrator = ModelOrchestrator()

        # Determine which heuristics to use
        if heuristics:
            heuristics_to_use = [
                h for h in heuristics if h in orchestrator.get_available_heuristics()
            ]
        else:
            heuristics_to_use = orchestrator.get_available_heuristics()

        # Track statistics
        tips_created = 0
        tips_updated = 0
        tips_skipped = 0

        # Generate tips using an atomic upsert keyed by
        # (game_id, heuristic). The legacy delete-then-insert pattern
        # could race between concurrent regenerations and violate the
        # ``uq_game_heuristic`` unique constraint; ``upsert`` uses
        # ``INSERT ... ON CONFLICT DO UPDATE`` so the operation is
        # safe under concurrent calls.  Per-game work is wrapped in a
        # single transaction so a mid-loop failure rolls back the whole
        # game rather than leaving partial writes behind.
        for game in games:
            # Snapshot which heuristics already have a tip so we can
            # classify the outcome as "created" vs "updated" vs
            # "skipped" without having to re-query after each upsert.
            existing_tips = await TipCRUD.get_by_game(db, game.id)
            existing_heuristics = {tip.heuristic for tip in existing_tips}

            try:
                for heuristic in heuristics_to_use:
                    # Honour the skip rule unless force=True.  When
                    # force=True we still avoid deleting — the upsert
                    # will overwrite in place.
                    if heuristic in existing_heuristics and not force:
                        tips_skipped += 1
                        continue

                    winner, confidence, margin = await orchestrator.predict(
                        game, heuristic
                    )

                    await TipCRUD.upsert(
                        db=db,
                        game_id=game.id,
                        heuristic=heuristic,
                        selected_team=winner,
                        margin=margin,
                        confidence=confidence,
                        explanation="",  # Explanations can be generated separately
                    )
                    if heuristic in existing_heuristics:
                        tips_updated += 1
                    else:
                        tips_created += 1
            except Exception:
                # Best-effort log + continue with the next game so a
                # single bad game does not abort the whole round.
                import logging

                logger = logging.getLogger(__name__)
                logger.exception(
                    "Tip regeneration failed for game %s", game.id
                )
                continue

            # Generate and store model predictions for this game
            for model in orchestrator.models:
                try:
                    winner, confidence, margin = await model.predict(game, db)
                    await ModelPredictionCRUD.create_or_update(
                        db=db,
                        game_id=game.id,
                        model_name=model.get_name(),
                        winner=winner,
                        confidence=confidence,
                        margin=margin,
                    )
                except Exception as e:
                    # Log error but continue with other models
                    import logging

                    logger = logging.getLogger(__name__)
                    logger.error(
                        f"Error generating prediction for model {model.get_name()}: {e}",
                        exc_info=True,
                    )

        return {
            "success": True,
            "message": f"Generated {tips_created} tips for round {round_id}, season {season}",
            "tips_count": tips_created,
            "tips_created": tips_created,
            "tips_updated": tips_updated,
            "tips_skipped": tips_skipped,
            "heuristics_used": heuristics_to_use,
            "season": season,
            "round_id": round_id,
        }

    @staticmethod
    async def generate_tips_for_game(
        db: AsyncSession, game_id: int, heuristics: Optional[List[str]] = None, force: bool = False
    ) -> dict:
        """Generate tips for a single game.

        Args:
            db: Database session
            game_id: Database ID of game
            heuristics: Optional list of heuristics to generate (default: all)
            force: Whether to force regeneration of existing tips (default: False)

        Returns:
            Dict with generation results:
            - tips_created: Number of tips created
            - tips_updated: Number of tips updated
            - tips_skipped: Number of tips skipped
            - heuristics_used: List of heuristics used
        """
        from ..orchestrator import ModelOrchestrator
        from . import GameCRUD, ModelPredictionCRUD

        # Get game
        game = await GameCRUD.get_by_id(db, game_id)

        if not game:
            return {
                "success": False,
                "message": f"Game {game_id} not found",
                "tips_created": 0,
                "tips_updated": 0,
                "tips_skipped": 0,
                "heuristics_used": [],
                "game_id": game_id,
            }

        # Initialize orchestrator
        orchestrator = ModelOrchestrator()

        # Determine which heuristics to use
        if heuristics:
            heuristics_to_use = [
                h for h in heuristics if h in orchestrator.get_available_heuristics()
            ]
        else:
            heuristics_to_use = orchestrator.get_available_heuristics()

        # Track statistics
        tips_created = 0
        tips_updated = 0
        tips_skipped = 0

        # Check if tips already exist for this game
        existing_tips = await TipCRUD.get_by_game(db, game_id)
        existing_heuristics = {tip.heuristic for tip in existing_tips}

        for heuristic in heuristics_to_use:
            # Check if tip already exists
            if heuristic in existing_heuristics:
                if force:
                    # Delete existing tips for this heuristic
                    await TipCRUD.delete_for_game(db, game_id)
                    existing_heuristics.discard(heuristic)
                    # Re-fetch to get remaining tips
                    remaining_tips = await TipCRUD.get_by_game(db, game_id)
                    existing_heuristics = {tip.heuristic for tip in remaining_tips}
                    # Now create new tip (will be counted as created)
                else:
                    tips_skipped += 1
                    continue

            # Generate prediction using heuristic
            winner, confidence, margin = await orchestrator.predict(game, heuristic)

            try:
                await TipCRUD.create(
                    db=db,
                    game_id=game_id,
                    heuristic=heuristic,
                    selected_team=winner,
                    margin=margin,
                    confidence=confidence,
                    explanation="",  # Explanations can be generated separately
                )
                tips_created += 1
            except Exception as e:
                # Handle unique constraint violation (race condition)
                # If another request created tip, just skip it
                if "uq_game_heuristic" in str(e) or "duplicate" in str(e).lower():
                    tips_skipped += 1
                    continue
                raise

        # Generate and store model predictions for this game
        for model in orchestrator.models:
            try:
                winner, confidence, margin = await model.predict(game, db)
                await ModelPredictionCRUD.create_or_update(
                    db=db,
                    game_id=game_id,
                    model_name=model.get_name(),
                    winner=winner,
                    confidence=confidence,
                    margin=margin,
                )
            except Exception as e:
                # Log error but continue with other models
                import logging

                logger = logging.getLogger(__name__)
                logger.error(
                    f"Error generating prediction for model {model.get_name()}: {e}", exc_info=True
                )

        return {
            "success": True,
            "message": f"Generated {tips_created} tips for game {game_id}",
            "tips_created": tips_created,
            "tips_updated": tips_updated,
            "tips_skipped": tips_skipped,
            "heuristics_used": heuristics_to_use,
            "game_id": game_id,
        }
