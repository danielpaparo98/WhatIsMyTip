"""Service for detecting and processing completed matches."""

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..cache import invalidate_cache_pattern, medium_cache
from ..config import settings
from ..crud.games import GameCRUD
from ..logger import get_logger
from ..models_ml.elo import EloModel
from ..squiggle import SquiggleClient
from ..squiggle.utils import parse_squiggle_complete

logger = get_logger(__name__)


class MatchCompletionDetectorService:
    """Service for detecting completed matches and updating final scores.

    This service handles:
    - Finding games that should be completed based on scheduled time
    - Checking Squiggle API for final scores
    - Marking games as completed in database
    - Handling edge cases (postponed games, cancelled games)
    """

    def __init__(
        self, squiggle_client: SquiggleClient, db_session: AsyncSession, buffer_minutes: int = 60
    ):
        """Initialize the MatchCompletionDetectorService.

        Args:
            squiggle_client: Squiggle API client
            db_session: Database session
            buffer_minutes: Buffer time after scheduled game time (default: 60 minutes)
        """
        self.client = squiggle_client
        self.db = db_session
        self.buffer_minutes = buffer_minutes
        self.logger = logger

    async def detect_and_process_completed_matches(self) -> Dict[str, Any]:
        """Detect and process completed matches.

        This method:
        1. Finds games where scheduled time has passed (plus buffer)
        2. Queries Squiggle API for these games
        3. Updates games with final scores and marks as completed
        4. Returns statistics about the process

        Returns:
            Dictionary with completion detection statistics:
            - games_checked: Number of games checked
            - games_completed: Number of games marked as completed
            - games_already_completed: Number of games already completed
            - games_not_ready: Number of games not yet ready (buffer not elapsed)
            - errors: List of error messages
            - duration_seconds: Time taken to process
        """
        start_time = time.time()
        self.logger.info(
            f"Starting match completion detection with {self.buffer_minutes} minute buffer"
        )

        stats = {
            "games_checked": 0,
            "games_completed": 0,
            "games_already_completed": 0,
            "games_not_ready": 0,
            "games_no_change": 0,
            "errors": [],
        }

        try:
            # Find games that might be completed
            recently_finished_games = await GameCRUD.get_recently_finished_games(
                self.db, buffer_minutes=self.buffer_minutes
            )

            stats["games_checked"] = len(recently_finished_games)

            if not recently_finished_games:
                self.logger.info("No games found that need completion checking")
                stats["duration_seconds"] = time.time() - start_time
                return stats

            self.logger.info(f"Found {len(recently_finished_games)} games to check for completion")

            # Fetch games from Squiggle API for current season, narrowed
            # to a date window that covers the recent games we're
            # checking (ME-003).  Without the window we'd re-download
            # the full ~200-game season every 15 minutes.
            current_year = datetime.now().year
            now = datetime.now(timezone.utc)
            # Use the oldest recent game date minus a small slack as the
            # lower bound, and ``now`` as the upper bound.  Falls back
            # to a 4-hour window if no recent games have a date.
            window_start = (now - timedelta(hours=4)).date().isoformat()
            for g in recently_finished_games:
                g_date = getattr(g, "date", None)
                if g_date is not None:
                    window_start = (g_date - timedelta(days=1)).date().isoformat()
                    break
            window_end = now.date().isoformat()

            games_data = await self.client.get_games(
                year=current_year,
                start_date=window_start,
                end_date=window_end,
            )

            # Create a mapping of squiggle_id to game data
            games_by_squiggle_id = {game_data["id"]: game_data for game_data in games_data}

            # Process each game
            for game in recently_finished_games:
                try:
                    # Get the latest data from Squiggle
                    squiggle_data = games_by_squiggle_id.get(game.squiggle_id)

                    if not squiggle_data:
                        self.logger.warning(
                            f"Game {game.squiggle_id} not found in Squiggle API response"
                        )
                        stats["games_not_ready"] += 1
                        continue

                    # Check if the game is complete in Squiggle
                    is_complete = parse_squiggle_complete(squiggle_data.get("complete", False))

                    if is_complete:
                        # Update game with final scores
                        updated_game = await GameCRUD.update_game_completion(
                            self.db, game_id=game.id, squiggle_data=squiggle_data
                        )

                        if updated_game:
                            stats["games_completed"] += 1
                            self.logger.info(
                                f"Marked game {game.squiggle_id} as completed: "
                                f"{game.home_team} {updated_game.home_score} - "
                                f"{updated_game.away_score} {game.away_team}"
                            )
                        else:
                            stats["games_already_completed"] += 1
                    else:
                        # Game not complete yet in Squiggle
                        stats["games_not_ready"] += 1
                        self.logger.debug(f"Game {game.squiggle_id} not yet complete in Squiggle")

                except Exception as e:
                    error_msg = f"Error processing game {game.squiggle_id}: {str(e)}"
                    self.logger.error(error_msg, exc_info=True)
                    stats["errors"].append(error_msg)

            duration = time.time() - start_time
            stats["duration_seconds"] = duration

            self.logger.info(
                f"Match completion detection completed: "
                f"{stats['games_completed']} games marked complete, "
                f"{stats['games_not_ready']} not ready, "
                f"{stats['games_already_completed']} already complete "
                f"in {duration:.2f}s"
            )

        except Exception as e:
            error_msg = f"Error in match completion detection: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            stats["errors"].append(error_msg)
            stats["duration_seconds"] = time.time() - start_time
            raise

        return stats

    async def check_single_game(self, squiggle_id: int) -> Optional[Dict[str, Any]]:
        """Check a single game for completion.

        Args:
            squiggle_id: Squiggle game ID

        Returns:
            Dictionary with game completion status or None if game not found
        """
        try:
            # Get game from database
            game = await GameCRUD.get_by_squiggle_id(self.db, squiggle_id)

            if not game:
                self.logger.warning(f"Game {squiggle_id} not found in database")
                return None

            # Check if buffer has elapsed
            if game.date:
                buffer_elapsed = datetime.now(timezone.utc) >= game.date + timedelta(
                    minutes=self.buffer_minutes
                )
            else:
                buffer_elapsed = False

            if not buffer_elapsed:
                return {
                    "squiggle_id": squiggle_id,
                    "status": "not_ready",
                    "reason": f"Buffer of {self.buffer_minutes} minutes not elapsed",
                    "game_date": game.date.isoformat() if game.date else None,
                }

            # Fetch game data from Squiggle
            squiggle_data = await self.client.get_game(squiggle_id)

            # Check completion status
            is_complete = parse_squiggle_complete(squiggle_data.get("complete", False))

            if is_complete:
                # Update game
                updated_game = await GameCRUD.update_game_completion(
                    self.db, game_id=game.id, squiggle_data=squiggle_data
                )

                return {
                    "squiggle_id": squiggle_id,
                    "status": "completed",
                    "home_team": game.home_team,
                    "away_team": game.away_team,
                    "home_score": updated_game.home_score if updated_game else game.home_score,
                    "away_score": updated_game.away_score if updated_game else game.away_score,
                }
            else:
                return {
                    "squiggle_id": squiggle_id,
                    "status": "not_complete",
                    "reason": "Game not complete in Squiggle API",
                }

        except Exception as e:
            self.logger.error(f"Error checking game {squiggle_id}: {str(e)}", exc_info=True)
            return {"squiggle_id": squiggle_id, "status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# Reusable cron-job core (extracted from FaaS handler in Phase 3)
# ---------------------------------------------------------------------------


async def run_match_completion(session: AsyncSession) -> Dict[str, Any]:
    """Run a single match-completion pass.

    Detects recently completed matches via Squiggle, updates final
    scores, refreshes the Elo ratings cache if any game was newly
    completed, and invalidates related cache entries.

    Returns:
        A result dict with:
        - ``status``: ``"success"`` (this job has no skip branch).
        - ``message``: human-readable summary.
        - ``games_checked``, ``games_completed``, ``games_already_completed``,
          ``games_not_ready``, ``errors``: detector stats.
        - ``elo_cache_updated``: ``True`` if Elo cache was refreshed.
    """
    buffer_minutes = settings.match_completion_buffer_minutes

    squiggle_client = SquiggleClient()
    try:
        detector = MatchCompletionDetectorService(
            squiggle_client=squiggle_client,
            db_session=session,
            buffer_minutes=buffer_minutes,
        )

        completion_stats = await detector.detect_and_process_completed_matches()

        games_checked = completion_stats.get("games_checked", 0)
        games_completed = completion_stats.get("games_completed", 0)
        games_already_completed = completion_stats.get("games_already_completed", 0)
        games_not_ready = completion_stats.get("games_not_ready", 0)
        error_count = len(completion_stats.get("errors", []))

        elo_cache_updated = False
        if games_completed > 0:
            try:
                await EloModel.update_cache(session)
                elo_cache_updated = True
            except Exception:  # noqa: BLE001
                logger.exception("Elo cache update failed; continuing")

        summary_parts = [
            f"Checked {games_checked} games for completion",
            f"Marked {games_completed} games as complete",
            f"{games_not_ready} games not ready",
            f"{games_already_completed} already complete",
        ]
        if elo_cache_updated:
            summary_parts.append("Elo cache updated")
        if error_count > 0:
            summary_parts.append(f"Failed: {error_count}")
        summary = "; ".join(summary_parts)
        logger.info("match-completion completed: %s", summary)

        # Invalidate stale cache entries (best-effort)
        try:
            await invalidate_cache_pattern(medium_cache, "games")
            await invalidate_cache_pattern(medium_cache, "tips")
        except Exception:  # noqa: BLE001
            pass

        return {
            "status": "success",
            "message": summary,
            "games_checked": games_checked,
            "games_completed": games_completed,
            "games_already_completed": games_already_completed,
            "games_not_ready": games_not_ready,
            "errors": error_count,
            "elo_cache_updated": elo_cache_updated,
        }
    finally:
        await squiggle_client.close()
