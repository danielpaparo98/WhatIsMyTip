"""Unit tests for backtest profit settlement.

BT-ODDS (2026-10 review): profit must be settled against real bookmaker
odds where available (via ``game_odds``), fall back to a representative
decimal price when a game has no odds snapshot, and refund (push) drawn
games instead of counting them as losses.

Covers:

* :func:`~packages.shared.services.settlement.settle_stake` — the pure
  settlement kernel.
* :meth:`BacktestService.calculate_backtest_from_tips` — heuristic
  settlement including ``odds_coverage``.
* :meth:`BacktestService.calculate_backtest_from_model_predictions` —
  model-level settlement.
* ``get_current_season_performance`` — fixture-derived ``total_rounds``
  and fully-completed ``rounds_completed``.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.shared.services.settlement import (
    FALLBACK_DECIMAL_ODDS,
    STAKE_PER_GAME,
    settle_stake,
)

# Convenience: expected fallback win payout for a $10 stake.
_FALLBACK_WIN = STAKE_PER_GAME * (FALLBACK_DECIMAL_ODDS - 1.0)


# ---------------------------------------------------------------------------
# Helpers – lightweight stand-ins for ORM rows
# ---------------------------------------------------------------------------


class _FakeOdds:
    """Mimics a GameOdds ORM object."""

    def __init__(self, game_id: int, home_odds: float, away_odds: float):
        self.id = 1
        self.game_id = game_id
        self.source = "the-odds-api"
        self.home_odds = home_odds
        self.away_odds = away_odds
        self.captured_at = datetime(2026, 3, 20)


def _make_game(game_id, home, away, home_score, away_score):
    g = MagicMock()
    g.id = game_id
    g.home_team = home
    g.away_team = away
    g.home_score = home_score
    g.away_score = away_score
    return g


def _make_tip(team):
    t = MagicMock()
    t.selected_team = team
    return t


# ---------------------------------------------------------------------------
# settle_stake — the pure settlement kernel
# ---------------------------------------------------------------------------


class TestSettleStake:
    """BT-ODDS: the settlement kernel must reflect real betting economics."""

    def test_correct_tip_with_real_odds_pays_price_minus_one(self):
        # $10 at decimal odds 2.50 → profit = 10 × (2.50 − 1) = $15
        assert settle_stake(is_correct=True, is_draw=False, decimal_odds=2.50) == 15.0

    def test_favourite_win_pays_less_than_stake(self):
        # $10 at 1.20 → $2 profit — favourites do NOT pay even money
        assert settle_stake(is_correct=True, is_draw=False, decimal_odds=1.20) == pytest.approx(2.0)

    def test_loss_always_loses_full_stake(self):
        assert settle_stake(is_correct=False, is_draw=False, decimal_odds=2.50) == -10.0
        assert settle_stake(is_correct=False, is_draw=False, decimal_odds=1.20) == -10.0

    def test_draw_is_a_push_regardless_of_odds(self):
        # BT-ODDS: two-way H2H markets refund on a draw — profit is $0.
        assert settle_stake(is_correct=False, is_draw=True, decimal_odds=2.50) == 0.0
        assert settle_stake(is_correct=True, is_draw=True, decimal_odds=1.10) == 0.0
        assert settle_stake(is_correct=False, is_draw=True, decimal_odds=None) == 0.0

    def test_missing_odds_fall_back_to_representative_price(self):
        # BT-ODDS: no odds snapshot → representative $1.90 price
        # (typical ~5% overround), NOT even money.
        assert settle_stake(is_correct=True, is_draw=False, decimal_odds=None) == pytest.approx(
            _FALLBACK_WIN
        )

    def test_invalid_odds_fall_back_to_representative_price(self):
        # Prices ≤ 1.0 are corrupt (impossible decimal odds) — never settle
        # a winner at a negative/zero price.
        assert settle_stake(is_correct=True, is_draw=False, decimal_odds=0.0) == pytest.approx(
            _FALLBACK_WIN
        )
        assert settle_stake(is_correct=True, is_draw=False, decimal_odds=-1.5) == pytest.approx(
            _FALLBACK_WIN
        )

    def test_module_defaults_are_sane(self):
        assert STAKE_PER_GAME == 10.0
        assert 1.0 < FALLBACK_DECIMAL_ODDS < 2.0


# ---------------------------------------------------------------------------
# calculate_backtest_from_tips — heuristic settlement with odds coverage
# ---------------------------------------------------------------------------


class TestCalculateBacktestFromTipsOdds:
    """Heuristic-level settlement: real odds, fallback, draws, coverage."""

    @pytest.fixture
    def service(self):
        from packages.shared.services.backtest import BacktestService

        with patch.object(BacktestService, "__init__", lambda self: None):
            svc = BacktestService()
        svc.orchestrator = MagicMock()
        svc.orchestrator.get_available_heuristics.return_value = ["best_bet"]
        return svc

    @pytest.mark.asyncio
    async def test_settles_with_real_odds_and_reports_coverage(self, service):
        """Correct favourite tips pay (odds−1)×stake; coverage = 1.0."""
        rows = [
            # Correct pick of a $1.20 favourite → +$2
            (
                _make_tip("Brisbane"),
                _make_game(1, "Brisbane", "Carlton", 90, 60),
                _FakeOdds(1, 1.20, 4.50),
            ),
            # Wrong underdog pick → −$10
            (
                _make_tip("Carlton"),
                _make_game(2, "Sydney", "Carlton", 80, 70),
                _FakeOdds(2, 1.50, 2.60),
            ),
        ]
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = rows
        mock_db.execute.return_value = mock_result
        service._get_round_accuracies = AsyncMock(return_value=[0.5, 0.0])

        result = await service.calculate_backtest_from_tips(mock_db, 2026, "best_bet")

        assert result["total_profit"] == pytest.approx(2.0 - 10.0)
        assert result["odds_coverage"] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_draw_settles_as_push(self, service):
        """BT-ODDS: a drawn game refunds the stake — $0, not −$10."""
        rows = [
            (
                _make_tip("Brisbane"),
                _make_game(1, "Brisbane", "Carlton", 80, 80),
                _FakeOdds(1, 1.55, 2.40),
            ),
        ]
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = rows
        mock_db.execute.return_value = mock_result
        service._get_round_accuracies = AsyncMock(return_value=[0.0])

        result = await service.calculate_backtest_from_tips(mock_db, 2026, "best_bet")

        assert result["total_profit"] == pytest.approx(0.0)
        # A draw is not a "correct" tip for accuracy purposes
        assert result["total_correct"] == 0
        assert result["overall_accuracy"] == 0.0

    @pytest.mark.asyncio
    async def test_games_without_odds_use_fallback_price(self, service):
        """No odds row → settle at the representative price; coverage < 1."""
        rows = [
            (_make_tip("Brisbane"), _make_game(1, "Brisbane", "Carlton", 90, 60), None),
        ]
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = rows
        mock_db.execute.return_value = mock_result
        service._get_round_accuracies = AsyncMock(return_value=[1.0])

        result = await service.calculate_backtest_from_tips(mock_db, 2026, "best_bet")

        assert result["total_profit"] == pytest.approx(_FALLBACK_WIN)
        assert result["odds_coverage"] == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_partial_coverage_mixed_settlement(self, service):
        """Mixed real-odds / fallback settlement with coverage 0.5."""
        rows = [
            # Real odds favourite, correct → +$4 (at 1.40)
            (
                _make_tip("Geelong"),
                _make_game(1, "Geelong", "Hawthorn", 95, 70),
                _FakeOdds(1, 1.40, 2.90),
            ),
            # No odds, correct → +$9 (at fallback 1.90)
            (_make_tip("Sydney"), _make_game(2, "Sydney", "Richmond", 85, 60), None),
        ]
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = rows
        mock_db.execute.return_value = mock_result
        service._get_round_accuracies = AsyncMock(return_value=[1.0, 1.0])

        result = await service.calculate_backtest_from_tips(mock_db, 2026, "best_bet")

        assert result["total_profit"] == pytest.approx(4.0 + 9.0)
        assert result["odds_coverage"] == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_no_tips_returns_zero_coverage(self, service):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_db.execute.return_value = mock_result

        result = await service.calculate_backtest_from_tips(mock_db, 2026, "best_bet")

        assert result["total_profit"] == 0.0
        assert result["odds_coverage"] == 0.0

    @pytest.mark.asyncio
    async def test_null_price_side_falls_back_per_side(self, service):
        """An odds row with a NULL price on the tipped side falls back for
        that tip only."""
        odds_row = _FakeOdds(1, 1.40, None)  # away price missing
        rows = [
            (_make_tip("Hawthorn"), _make_game(1, "Geelong", "Hawthorn", 95, 99), odds_row),
        ]
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = rows
        mock_db.execute.return_value = mock_result
        service._get_round_accuracies = AsyncMock(return_value=[1.0])

        result = await service.calculate_backtest_from_tips(mock_db, 2026, "best_bet")

        # Away tip with missing price → fallback 1.90 → +$9; coverage 0.0
        assert result["total_profit"] == pytest.approx(_FALLBACK_WIN)
        assert result["odds_coverage"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# calculate_backtest_from_model_predictions — model settlement
# ---------------------------------------------------------------------------


class TestModelPredictionSettlement:
    """Model-level settlement mirrors heuristic settlement (BT-ODDS)."""

    @pytest.fixture
    def service(self):
        from packages.shared.services.backtest import BacktestService

        with patch.object(BacktestService, "__init__", lambda self: None):
            svc = BacktestService()
        svc.orchestrator = MagicMock()
        return svc

    @pytest.mark.asyncio
    async def test_model_profit_uses_real_odds(self, service):
        pred = MagicMock()
        pred.winner = "Brisbane"
        pred.margin = 15
        game = _make_game(1, "Brisbane", "Carlton", 90, 60)
        odds = _FakeOdds(1, 1.25, 3.80)

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [(pred, game, odds)]
        mock_db.execute.return_value = mock_result

        result = await service.calculate_backtest_from_model_predictions(mock_db, 2026, "elo")

        assert result["total_profit"] == pytest.approx(2.5)  # 10 × (1.25 − 1)
        assert result["odds_coverage"] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_model_draw_is_a_push(self, service):
        pred = MagicMock()
        pred.winner = "Brisbane"
        pred.margin = 5
        game = _make_game(1, "Brisbane", "Carlton", 70, 70)
        odds = _FakeOdds(1, 1.50, 2.50)

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [(pred, game, odds)]
        mock_db.execute.return_value = mock_result

        result = await service.calculate_backtest_from_model_predictions(mock_db, 2026, "elo")

        assert result["total_profit"] == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_model_no_odds_uses_fallback(self, service):
        pred = MagicMock()
        pred.winner = "Sydney"
        pred.margin = 10
        game = _make_game(1, "Sydney", "Richmond", 85, 60)

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [(pred, game, None)]
        mock_db.execute.return_value = mock_result

        result = await service.calculate_backtest_from_model_predictions(mock_db, 2026, "elo")

        assert result["total_profit"] == pytest.approx(_FALLBACK_WIN)
        assert result["odds_coverage"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Current-season fixtures: derived totals + fully-completed rounds
# ---------------------------------------------------------------------------


class TestCurrentSeasonFixtureDerivedRounds:
    """BT-ROUND: total_rounds comes from the fixture, rounds_completed
    counts rounds where every game is completed, and 'now' is UTC."""

    @pytest.fixture
    def service(self):
        from packages.shared.services.backtest import BacktestService

        with patch.object(BacktestService, "__init__", lambda self: None):
            svc = BacktestService()
        svc.orchestrator = MagicMock()
        svc.orchestrator.get_available_heuristics.return_value = []
        return svc

    @pytest.mark.asyncio
    async def test_total_rounds_derived_from_fixture_not_hardcoded(self, service):
        mock_db = AsyncMock()

        # execute call order:
        # 1. total fixture rounds → 23
        # 2. fully-completed rounds → 10
        fixture_rounds = MagicMock()
        fixture_rounds.scalar.return_value = 23
        completed_rounds = MagicMock()
        completed_rounds.scalar.return_value = 10

        calls = {"n": 0}

        async def fake_execute(stmt):
            calls["n"] += 1
            return fixture_rounds if calls["n"] == 1 else completed_rounds

        mock_db.execute = AsyncMock(side_effect=fake_execute)

        result = await service.get_current_season_performance(mock_db)

        assert result.total_rounds == 23
        assert result.rounds_completed == 10

    @pytest.mark.asyncio
    async def test_falls_back_to_24_when_fixture_empty(self, service):
        mock_db = AsyncMock()
        empty = MagicMock()
        empty.scalar.return_value = 0

        calls = {"n": 0}

        async def fake_execute(stmt):
            calls["n"] += 1
            return empty

        mock_db.execute = AsyncMock(side_effect=fake_execute)

        result = await service.get_current_season_performance(mock_db)

        assert result.total_rounds == 24
        assert result.rounds_completed == 0


# ---------------------------------------------------------------------------
# SQL settlement round-trip (m-7): the case expressions in
# get_round_by_round_data / get_model_round_by_round are only exercised
# against a real engine here — draw-push ordering, the >1.0 price guard,
# fallback coalescing, and tipped-side coverage are all invisible to the
# mock-based suites.
# ---------------------------------------------------------------------------


class TestSqlSettlementRoundTrip:
    """Run the real settlement SQL against in-memory SQLite."""

    @pytest.fixture
    async def db_session(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from packages.shared.models import Game, GameOdds, ModelPrediction, Tip

        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            for table in (
                Game.__table__,
                Tip.__table__,
                ModelPrediction.__table__,
                GameOdds.__table__,
            ):
                await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn))

        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            yield session

        await engine.dispose()

    async def _seed(self, session):
        from packages.shared.models import Game, GameOdds, ModelPrediction, Tip

        # Round 1 — real odds favourite (correct, +$2), drawn game (push).
        g1 = Game(
            id=1,
            slug="g1",
            round_id=1,
            season=2026,
            home_team="Brisbane",
            away_team="Carlton",
            home_score=90,
            away_score=60,
            completed=True,
        )
        g2 = Game(
            id=2,
            slug="g2",
            round_id=1,
            season=2026,
            home_team="Sydney",
            away_team="Richmond",
            home_score=80,
            away_score=80,
            completed=True,
        )
        # Round 2 — no odds (fallback win), corrupt favourite price
        # (guard must fall back, coverage must NOT count it).
        g3 = Game(
            id=3,
            slug="g3",
            round_id=2,
            season=2026,
            home_team="Geelong",
            away_team="Hawthorn",
            home_score=95,
            away_score=70,
            completed=True,
        )
        g4 = Game(
            id=4,
            slug="g4",
            round_id=2,
            season=2026,
            home_team="Melbourne",
            away_team="Essendon",
            home_score=100,
            away_score=30,
            completed=True,
        )
        session.add_all([g1, g2, g3, g4])
        await session.flush()

        session.add_all(
            [
                Tip(game_id=1, heuristic="best_bet", selected_team="Brisbane"),
                Tip(game_id=2, heuristic="best_bet", selected_team="Sydney"),
                Tip(game_id=3, heuristic="best_bet", selected_team="Geelong"),
                Tip(game_id=4, heuristic="best_bet", selected_team="Melbourne"),
                # Model predictions mirror the same settlement on the model path.
                ModelPrediction(game_id=1, model_name="elo", winner="Brisbane"),
                ModelPrediction(game_id=2, model_name="elo", winner="Sydney"),
                ModelPrediction(game_id=3, model_name="elo", winner="Geelong"),
                ModelPrediction(game_id=4, model_name="elo", winner="Melbourne"),
                GameOdds(
                    game_id=1,
                    source="the-odds-api",
                    home_odds=1.20,
                    away_odds=4.50,
                    captured_at=datetime(2026, 4, 1),
                ),
                GameOdds(
                    game_id=2,
                    source="the-odds-api",
                    home_odds=1.55,
                    away_odds=2.40,
                    captured_at=datetime(2026, 4, 1),
                ),
                # m-1: corrupt price (≤ 1.0) must never settle a winner at ≤ $0.
                GameOdds(
                    game_id=4,
                    source="the-odds-api",
                    home_odds=0.5,
                    away_odds=3.0,
                    captured_at=datetime(2026, 4, 1),
                ),
            ]
        )
        await session.commit()

    @pytest.mark.asyncio
    async def test_heuristic_round_by_round_settlement(self, db_session):
        from packages.shared.services.backtest import BacktestService

        with patch.object(BacktestService, "__init__", lambda self: None):
            svc = BacktestService()

        await self._seed(db_session)
        rounds = await svc.get_round_by_round_data(db_session, 2026, "best_bet")
        by_round = {r["round_id"]: r for r in rounds}

        # Round 1: favourite win at real odds (+$2), draw push ($0).
        assert by_round[1]["tips_made"] == 2
        assert by_round[1]["tips_correct"] == 1
        assert by_round[1]["profit"] == pytest.approx(2.0)
        assert by_round[1]["odds_coverage"] == pytest.approx(1.0)

        # Round 2: both winners settle at the fallback (+$9 each);
        # the corrupt 0.5 favourite price is guarded away.
        assert by_round[2]["tips_correct"] == 2
        assert by_round[2]["profit"] == pytest.approx(2 * _FALLBACK_WIN)
        assert by_round[2]["odds_coverage"] == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_model_round_by_round_settlement(self, db_session):
        from packages.shared.services.backtest import BacktestService

        with patch.object(BacktestService, "__init__", lambda self: None):
            svc = BacktestService()

        await self._seed(db_session)
        rounds = await svc.get_model_round_by_round(db_session, 2026, "elo")
        by_round = {r["round_id"]: r for r in rounds}

        assert by_round[1]["profit"] == pytest.approx(2.0)  # real odds + push
        assert by_round[2]["profit"] == pytest.approx(2 * _FALLBACK_WIN)
        assert by_round[2]["odds_coverage"] == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_python_and_sql_paths_agree(self, db_session):
        """The Python-settled season metrics and the SQL round-by-round
        aggregate must produce the same profit for the same data."""
        from packages.shared.services.backtest import BacktestService

        with patch.object(BacktestService, "__init__", lambda self: None):
            svc = BacktestService()

        await self._seed(db_session)
        season = await svc.calculate_backtest_from_tips(db_session, 2026, "best_bet")
        rounds = await svc.get_round_by_round_data(db_session, 2026, "best_bet")

        assert season["total_profit"] == pytest.approx(sum(r["profit"] for r in rounds))
        assert season["total_tips"] == sum(r["tips_made"] for r in rounds)
