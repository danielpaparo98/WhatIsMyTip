import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..cache import _get_client
from ..logger import get_logger
from ..models import Game
from ..utils import ensure_datetime
from .base import BaseModel

logger = get_logger(__name__)

# Redis key for storing computed Elo ratings
_ELO_RATINGS_REDIS_KEY = "wimt:elo_ratings"
# TTL for Elo ratings in Redis (1 hour — recomputed on update_cache)
_ELO_RATINGS_TTL = 3600


class EloModel(BaseModel):
    """Elo rating model for predicting game outcomes.

    Uses a simplified Elo rating system adapted for AFL.

    In the FaaS environment, computed ratings are stored in Redis rather than
    an in-memory class-level dict. On cold starts, ratings are loaded from Redis
    first, falling back to DB computation if Redis cache misses.
    """

    # Default parameters for rating operations
    _DEFAULT_K_FACTOR = 32.0
    _DEFAULT_HOME_ADVANTAGE = 50.0

    # GF-HA LEARNED (2026-09-21): the home advantage is MEASURED from
    # completed games (avg home margin x 10 Elo-per-point, grand finals
    # excluded) instead of assumed at a fixed 50.  Set whenever ratings
    # are computed from a game list; predict prefers it over the
    # hard-coded default and DROPS it entirely at neutral venues.
    _LEARNED_HOME_ADVANTAGE: Optional[float] = None

    # Lock for coordinating cache initialisation across concurrent coroutines
    # within a single function invocation
    _cache_lock = asyncio.Lock()

    def __init__(self, k_factor: float = 32.0, home_advantage: float = 50.0):
        self.k_factor = k_factor
        self.home_advantage = home_advantage
        # Instance-level ratings for backward compatibility (backtesting)
        self.ratings: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    def get_name(self) -> str:
        return "elo"

    # ------------------------------------------------------------------
    # Shared computation (pure function — no I/O)
    # ------------------------------------------------------------------

    # GF-NEUTRAL: Elo points per score-point (mirrors the predict margin
    # convention margin = elo_diff / 10).
    _ELO_PER_POINT = 10.0

    @staticmethod
    def learn_home_advantage(
        games: Sequence[Game],
    ) -> Tuple[Optional[float], set]:
        """MEASURE the home advantage from completed games.

        GF-HA (2026-09-21, user request: "does the 50 Elo bump ever make
        sense?"): the old code ASSUMED 50 Elo.  This estimates it from
        the data: average home margin over completed games, EXCLUDING
        each season's grand final (neutral — no home side), converted to
        Elo points via the model's own margin convention (1 pt = 10 Elo)
        and clamped to a sane 2-10 point band.

        Returns:
            (learned_home_advantage_or_None, neutral_keys) where
            neutral_keys is the set of (season, round_id) pairs that are
            grand finals — pass it to ``_compute_ratings_from_games`` so
            the rating walk skips the HA bonus on those games too.
        """
        neutral_keys: set = set()
        margins: list = []

        if not games:
            return None, neutral_keys

        # Season -> max round (the grand-final round), from THIS sample.
        max_rounds: dict = {}
        for g in games:
            if g.season is not None and g.round_id is not None:
                if g.season not in max_rounds or g.round_id > max_rounds[g.season]:
                    max_rounds[g.season] = g.round_id

        for g in games:
            if g.season is None or g.round_id is None:
                continue
            if g.round_id == max_rounds.get(g.season):
                neutral_keys.add((g.season, g.round_id))
                continue
            if g.home_score is not None and g.away_score is not None:
                margins.append(g.home_score - g.away_score)

        if len(margins) < 50:
            # Too thin a sample to trust — caller falls back to default.
            return None, neutral_keys

        avg_margin = sum(margins) / len(margins)
        learned = max(20.0, min(100.0, avg_margin * EloModel._ELO_PER_POINT))
        return learned, neutral_keys

    @staticmethod
    def _compute_ratings_from_games(
        games: Sequence[Game],
        initial_ratings: Dict[str, float],
        k_factor: float = 32.0,
        home_advantage: float = 50.0,
        neutral_keys: Optional[set] = None,
    ) -> Dict[str, float]:
        """Compute Elo ratings by processing a list of games.

        This is the single source of truth for the Elo rating computation loop.
        All methods that compute ratings should delegate to this method.

        Args:
            games: List of completed Game objects, ordered chronologically
            initial_ratings: Starting ratings dict (modified in-place and returned)
            k_factor: Elo K-factor for rating updates
            home_advantage: Home advantage bonus in Elo points
            neutral_keys: Optional set of (season, round_id) pairs that are
                NEUTRAL-venue games (grand finals) — the HA bonus is skipped
                for those games (GF-NEUTRAL).

        Returns:
            Updated ratings dictionary (same object as initial_ratings)
        """
        neutral_keys = neutral_keys or set()
        for game in games:
            # getattr: the pure function also accepts lightweight
            # game-like fakes in unit tests (no ORM machinery).
            game_key = (getattr(game, "season", None), getattr(game, "round_id", None))
            is_neutral = game_key in neutral_keys

            home_rating = initial_ratings.get(game.home_team, 1500.0)
            away_rating = initial_ratings.get(game.away_team, 1500.0)

            # Expected scores — HA bonus skipped at neutral venues.
            ha = 0.0 if is_neutral else home_advantage
            expected_home = 1.0 / (
                1.0 + 10.0 ** ((away_rating - home_rating - ha) / 400.0)
            )
            expected_away = 1.0 - expected_home

            # Actual scores (a draw is the standard 0.5 for each side)
            if game.home_score is not None and game.away_score is not None:
                if game.home_score > game.away_score:
                    actual_home = 1.0
                elif game.away_score > game.home_score:
                    actual_home = 0.0
                else:
                    actual_home = 0.5
                actual_away = 1.0 - actual_home

                # Update ratings
                initial_ratings[game.home_team] = home_rating + k_factor * (
                    actual_home - expected_home
                )
                initial_ratings[game.away_team] = away_rating + k_factor * (
                    actual_away - expected_away
                )

        return initial_ratings

    # ------------------------------------------------------------------
    # Redis-backed class-level cache
    # ------------------------------------------------------------------

    @classmethod
    async def _load_ratings_from_redis(cls) -> Optional[Dict[str, float]]:
        """Load Elo ratings from Redis.

        Returns:
            Dict of team -> rating, or None if not cached in Redis.
        """
        try:
            client = _get_client()
            raw = await client.get(_ELO_RATINGS_REDIS_KEY)
            if raw is not None:
                ratings = json.loads(raw)
                logger.info(f"EloModel: Loaded {len(ratings)} ratings from Redis")
                return ratings
        except Exception as e:
            logger.warning(f"EloModel: Failed to load ratings from Redis: {e}")
        return None

    @classmethod
    async def _save_ratings_to_redis(cls, ratings: Dict[str, float]) -> None:
        """Save Elo ratings to Redis with TTL."""
        try:
            client = _get_client()
            await client.set(
                _ELO_RATINGS_REDIS_KEY,
                json.dumps(ratings),
                ex=_ELO_RATINGS_TTL,
            )
            logger.info(f"EloModel: Saved {len(ratings)} ratings to Redis")
        except Exception as e:
            logger.warning(f"EloModel: Failed to save ratings to Redis: {e}")

    @classmethod
    async def _initialize_cache(cls, db: AsyncSession):
        """Initialize the ratings cache — tries Redis first, then DB."""
        async with cls._cache_lock:
            # Try Redis first
            try:
                ratings = await cls._load_ratings_from_redis()
            except Exception as exc:  # noqa: BLE001 - best-effort
                # Redis load failure must not block cache init (LO-001).
                # Fall through to DB recompute so the application still
                # has ratings to use.
                logger.warning(
                    "EloModel._initialize_cache: Redis load failed, "
                    "falling back to DB recompute: %s",
                    exc,
                )
                ratings = None
            if ratings is not None:
                return

            # Redis miss — compute from DB
            start_time = time.time()
            logger.info("EloModel._initialize_cache: Computing ratings from database")

            ratings = await cls._compute_ratings_from_db(db)

            # Save to Redis for future invocations
            await cls._save_ratings_to_redis(ratings)

            total_time = time.time() - start_time
            logger.info(
                f"EloModel._initialize_cache: Computed {len(ratings)} ratings "
                f"from database in {total_time:.4f}s"
            )

    @classmethod
    async def _compute_ratings_from_db(cls, db: AsyncSession) -> Dict[str, float]:
        """Compute Elo ratings from the database (full scan of completed games)."""
        # Get all teams
        result = await db.execute(
            select(Game.home_team)
            .distinct()
            .where(Game.home_team.is_not(None))
        )
        home_teams = set(r[0] for r in result.all())
        result = await db.execute(
            select(Game.away_team)
            .distinct()
            .where(Game.away_team.is_not(None))
        )
        away_teams = set(r[0] for r in result.all())
        all_teams = home_teams.union(away_teams)

        # Initialize all teams with 1500 rating
        ratings = {team: 1500.0 for team in all_teams}

        # Load all completed games
        result = await db.execute(select(Game).where(Game.completed).order_by(Game.date))
        games = result.scalars().all()

        logger.info(f"EloModel: Loaded {len(games)} completed games from database")

        # GF-HA: measure home advantage from the sample; skip HA on the
        # grand-final rounds (neutral venues) in the rating walk.
        learned, neutral_keys = cls.learn_home_advantage(games)
        if learned is not None:
            cls._LEARNED_HOME_ADVANTAGE = learned

        # Process games in chronological order
        cls._compute_ratings_from_games(
            games,
            ratings,
            k_factor=cls._DEFAULT_K_FACTOR,
            home_advantage=learned if learned is not None else cls._DEFAULT_HOME_ADVANTAGE,
            neutral_keys=neutral_keys,
        )

        return ratings

    @classmethod
    async def update_cache(cls, db: AsyncSession):
        """Update the ratings cache from database.

        This should be called after new games are completed or synced.
        It recomputes ratings from DB and stores in Redis.

        Performance trade-off (ME-008)
        -----------------------------
        The implementation deliberately recomputes the *entire* rating
        history from scratch on every invocation.  A partial update
        (e.g. "apply only the new games since the last call") would
        be much faster but it would mis-rate end-of-season games:
        Elo is order-sensitive because each new game depends on the
        cumulative rating that came out of every previous game, so
        any change in the ordering of past results propagates into
        every subsequent rating.

        We accept the O(N) cost (where N is the count of completed
        games for the season) so the cache is always mathematically
        consistent with the database.  This is fine for the current
        data scale (a single season is ~200 games).
        """
        async with cls._cache_lock:
            start_time = time.time()
            logger.info("EloModel.update_cache: Updating Elo ratings")

            ratings = await cls._compute_ratings_from_db(db)

            # Save to Redis
            await cls._save_ratings_to_redis(ratings)

            # Also persist to DB for durability
            await cls.save_to_cache(db, ratings)

            total_time = time.time() - start_time
            logger.info(
                f"EloModel.update_cache: Updated {len(ratings)} ratings in {total_time:.4f}s"
            )

    @classmethod
    async def save_to_cache(
        cls, db: AsyncSession, ratings: Dict[str, float], season: Optional[int] = None
    ):
        """Save Elo ratings to database cache.

        Args:
            db: Database session
            ratings: Dictionary of team ratings to save
            season: Optional season (defaults to current year)
        """
        from datetime import datetime

        from ..crud.elo_cache import EloCacheCRUD

        if season is None:
            season = datetime.now().year

        try:
            await EloCacheCRUD.save_ratings(db, ratings, season)
            logger.info(f"EloModel.save_to_cache: Saved {len(ratings)} ratings for season {season}")
        except Exception as e:
            logger.error(f"EloModel.save_to_cache: Failed to save ratings: {e}", exc_info=True)

    @classmethod
    async def load_from_cache(cls, db: AsyncSession, season: Optional[int] = None) -> bool:
        """Load Elo ratings from database cache into Redis.

        Args:
            db: Database session
            season: Optional season to load (defaults to current year)

        Returns:
            True if ratings were loaded successfully, False otherwise
        """
        from datetime import datetime

        from ..crud.elo_cache import EloCacheCRUD

        if season is None:
            season = datetime.now().year

        try:
            ratings = await EloCacheCRUD.load_ratings(db, season)

            if not ratings:
                logger.info(
                    f"EloModel.load_from_cache: No cached ratings found for season {season}"
                )
                return False

            # Store in Redis
            await cls._save_ratings_to_redis(ratings)

            logger.info(
                f"EloModel.load_from_cache: Loaded {len(ratings)} ratings for season {season}"
            )
            return True
        except Exception as e:
            logger.error(f"EloModel.load_from_cache: Failed to load ratings: {e}", exc_info=True)
            return False

    @classmethod
    async def get_cached_ratings(cls) -> Dict[str, float]:
        """Get ratings from Redis cache.

        Returns:
            Dict of team -> rating, empty dict if not cached.
        """
        ratings = await cls._load_ratings_from_redis()
        return ratings if ratings is not None else {}

    # ------------------------------------------------------------------
    # Instance-level state
    # ------------------------------------------------------------------
    #
    # ``self.ratings`` is retained as an empty dict on each instance
    # for backward compatibility with downstream consumers that
    # inspect it (e.g. ``test_cron_utils.py::test_elo_instance_ratings_empty``).
    # The legacy ``_update_ratings`` / ``_get_team_ratings`` helpers
    # that used to populate this dict were dead code in the happy
    # path — the live prediction flow goes through the class-level
    # Redis-backed cache (see ``_load_ratings_from_redis`` /
    # ``_compute_point_in_time_ratings``) and never touches the
    # instance dict.  They've been removed (HI-001).

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    async def predict(self, game: Game, db: AsyncSession) -> Tuple[str, float, int]:
        """Predict winner using Elo ratings with point-in-time data.

        For current predictions (non-backtesting), uses the Redis-backed cache
        to avoid recomputing all historical games from scratch every time.
        For backtesting, falls back to point-in-time computation.
        """
        start_time = time.time()
        logger.info(
            f"EloModel.predict: STARTING PREDICTION for game "
            f"{game.id} ({game.home_team} vs {game.away_team}) on {game.date}"
        )

        # Determine if we can use cached ratings (current predictions).
        # ``game.date`` may be tz-aware or naive depending on how the
        # row was written; normalise both sides to naive UTC before
        # comparing so the 7-day cache window works in either case.
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        # Defensively coerce a cache-round-tripped string date back to a
        # real datetime before touching datetime-only attributes.
        game_date = ensure_datetime(game.date)
        game_date_naive = (
            game_date.replace(tzinfo=None)
            if game_date is not None and game_date.tzinfo is not None
            else game_date
        )
        use_cache = (
            game_date_naive is not None
            and game_date_naive >= now_utc - timedelta(days=7)
        )

        if use_cache:
            ratings = await self.__class__._load_ratings_from_redis()
            if ratings:
                logger.info(f"EloModel.predict: Using Redis-cached ratings ({len(ratings)} teams)")
            else:
                # Redis miss — try DB cache, then compute
                loaded = await self.__class__.load_from_cache(db)
                if not loaded:
                    await self.__class__._initialize_cache(db)
                ratings = await self.__class__._load_ratings_from_redis()

                if not ratings:
                    # Final fallback: point-in-time computation
                    logger.info(
                        "EloModel.predict: Computing point-in-time ratings (all caches missed)"
                    )
                    ratings = await self._compute_point_in_time_ratings(db, game)
        else:
            # Point-in-time computation for backtesting
            logger.info("EloModel.predict: Computing point-in-time ratings for backtesting")
            ratings = await self._compute_point_in_time_ratings(db, game)

        # Get ratings for the prediction game
        home_rating = ratings.get(game.home_team, 1500.0)
        away_rating = ratings.get(game.away_team, 1500.0)

        # Apply home advantage — MEASURED where available (GF-HA: learned
        # from the historical sample during rating computation), falling
        # back to the hard-coded default only when no sample exists —
        # and DROPPED entirely at neutral venues (GF-NEUTRAL,
        # 2026-09-21): the grand final's "home" side is a fixture
        # designation, and gifting it +50 Elo flipped neutral-venue picks.
        from .neutral import is_grand_final

        if await is_grand_final(db, game):
            ha = 0.0
        else:
            # GF-HA: prefer the MEASURED advantage over the hard-coded
            # default (falls back to 50 when no sample has been seen).
            ha = type(self)._LEARNED_HOME_ADVANTAGE
            if ha is None:
                ha = self.home_advantage
        effective_home = home_rating + ha

        # Calculate expected probability
        expected_home = 1.0 / (1.0 + 10.0 ** ((away_rating - effective_home) / 400.0))
        expected_away = 1.0 - expected_home

        # Predict winner and margin
        if expected_home > expected_away:
            winner = game.home_team
            confidence = expected_home
            margin = int((effective_home - away_rating) / 10)
        else:
            winner = game.away_team
            confidence = expected_away
            margin = int((away_rating - effective_home) / 10)

        # Clamp margin to reasonable range
        margin = max(1, min(100, margin))

        total_time = time.time() - start_time
        logger.info(
            f"EloModel.predict: COMPLETED in {total_time:.4f}s "
            f"| winner={winner}, confidence={confidence:.2f}, margin={margin}"
        )

        return winner, confidence, margin

    async def _compute_point_in_time_ratings(
        self, db: AsyncSession, game: Game
    ) -> Dict[str, float]:
        """Compute Elo ratings using only games before the given game's date.

        Used for backtesting to ensure no data leakage.
        """
        # Get all teams
        result = await db.execute(select(Game.home_team).distinct())
        home_teams = set(r[0] for r in result.all())
        result = await db.execute(select(Game.away_team).distinct())
        away_teams = set(r[0] for r in result.all())
        all_teams = home_teams.union(away_teams)

        # Initialize all teams with 1500 rating
        ratings = {team: 1500.0 for team in all_teams}

        # Load only games that occurred BEFORE the prediction game's date
        query_start = time.time()
        result = await db.execute(
            select(Game).where(Game.completed, Game.date < game.date).order_by(Game.date)
        )
        games = result.scalars().all()
        query_time = time.time() - query_start

        logger.info(
            f"EloModel._compute_point_in_time_ratings: Loaded "
            f"{len(games)} historical games before {game.date} "
            f"(query took {query_time:.4f}s)"
        )

        # GF-HA: MEASURE the home advantage from the historical sample
        # (grand finals excluded) instead of assuming a fixed +50.
        learned, neutral_keys = self.learn_home_advantage(games)
        if learned is not None:
            type(self)._LEARNED_HOME_ADVANTAGE = learned
            ha_to_use = learned
        else:
            ha_to_use = self.home_advantage

        # Process games using shared computation
        self._compute_ratings_from_games(
            games,
            ratings,
            k_factor=self._DEFAULT_K_FACTOR,
            home_advantage=ha_to_use,
            neutral_keys=neutral_keys,
        )

        return ratings
