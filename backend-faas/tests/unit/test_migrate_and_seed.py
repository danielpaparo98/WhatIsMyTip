"""Tests for the migrate_and_seed script."""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# Ensure backend-faas is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.migrate_and_seed import (
    _CLEAR_TABLES,
    _SEED_TABLES,
    _build_alembic_config,
    _parse_value,
    _resolve_database_url,
    _row_to_orm,
    _to_async_url,
    _to_sync_url,
    clear_database,
    run_migrations,
    seed_from_csv,
)
from packages.shared.models import (
    BacktestResult,
    EloCache,
    Game,
    GenerationProgress,
    JobExecution,
    MatchAnalysis,
    ModelPrediction,
    Tip,
)

from sqlalchemy import Boolean, Float, Integer, String, Text


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


class TestUrlConversion:
    """Test URL conversion helpers."""

    def test_to_async_url_plain(self):
        result = _to_async_url("postgresql://user:pass@localhost/db")
        assert result == "postgresql+asyncpg://user:pass@localhost/db"

    def test_to_async_url_already_async(self):
        url = "postgresql+asyncpg://user:pass@localhost/db"
        assert _to_async_url(url) == url

    def test_to_sync_url_strips_asyncpg(self):
        result = _to_sync_url("postgresql+asyncpg://user:pass@localhost/db")
        assert result == "postgresql://user:pass@localhost/db"

    def test_to_sync_url_already_sync(self):
        url = "postgresql://user:pass@localhost/db"
        assert _to_sync_url(url) == url

    def test_roundtrip(self):
        original = "postgresql://user:pass@localhost/db"
        assert _to_sync_url(_to_async_url(original)) == original


# ---------------------------------------------------------------------------
# Alembic config
# ---------------------------------------------------------------------------


class TestBuildAlembicConfig:
    """Test Alembic config construction."""

    def test_sets_sqlalchemy_url(self):
        cfg = _build_alembic_config("postgresql://user:pass@localhost:5432/mydb")
        assert cfg.get_main_option("sqlalchemy.url") == "postgresql://user:pass@localhost:5432/mydb"

    def test_sets_script_location(self):
        cfg = _build_alembic_config("postgresql://localhost/db")
        script_loc = cfg.get_main_option("script_location")
        assert script_loc is not None
        assert script_loc.endswith("alembic")


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------


class TestParseValue:
    """Test CSV value → Python type conversion."""

    def test_empty_string_returns_none(self):
        assert _parse_value(Integer(), "") is None

    def test_none_returns_none(self):
        assert _parse_value(Integer(), None) is None

    def test_integer(self):
        assert _parse_value(Integer(), "42") == 42

    def test_float(self):
        assert _parse_value(Float(), "3.14") == 3.14

    def test_boolean_true(self):
        assert _parse_value(Boolean(), "True") is True
        assert _parse_value(Boolean(), "true") is True
        assert _parse_value(Boolean(), "1") is True

    def test_boolean_false(self):
        assert _parse_value(Boolean(), "False") is False
        assert _parse_value(Boolean(), "false") is False
        assert _parse_value(Boolean(), "0") is False

    def test_string_passthrough(self):
        assert _parse_value(String(), "hello") == "hello"

    def test_text_passthrough(self):
        assert _parse_value(Text(), "some text") == "some text"


# ---------------------------------------------------------------------------
# ORM row conversion
# ---------------------------------------------------------------------------


class TestRowToOrm:
    """Test CSV row dict → ORM instance conversion."""

    def test_game_from_row(self):
        row = {
            "id": "1",
            "slug": "abc123",
            "squiggle_id": "10001",
            "round_id": "5",
            "season": "2025",
            "home_team": "Brisbane",
            "away_team": "Collingwood",
            "home_score": "85",
            "away_score": "72",
            "venue": "Gabba",
            "date": "2025-04-15T07:30:00+00:00",
            "completed": "True",
            "predictions_generated": "True",
            "tips_generated": "True",
            "sync_version": "1",
        }
        game = _row_to_orm(Game, row)
        assert isinstance(game, Game)
        assert game.id == 1
        assert game.slug == "abc123"
        assert game.squiggle_id == 10001
        assert game.home_team == "Brisbane"
        assert game.home_score == 85
        assert game.completed is True

    def test_tip_from_row(self):
        row = {
            "id": "1",
            "game_id": "1",
            "heuristic": "best_bet",
            "selected_team": "Brisbane",
            "margin": "15",
            "confidence": "0.78",
            "explanation": "Strong pick.",
        }
        tip = _row_to_orm(Tip, row)
        assert isinstance(tip, Tip)
        assert tip.game_id == 1
        assert tip.heuristic == "best_bet"
        assert tip.confidence == 0.78

    def test_empty_values_skipped(self):
        row = {
            "id": "1",
            "slug": "abc",
            "home_score": "",
            "away_score": "",
        }
        game = _row_to_orm(Game, row)
        assert game.id == 1
        assert game.home_score is None
        assert game.away_score is None

    def test_unknown_keys_ignored(self):
        row = {
            "id": "1",
            "slug": "abc",
            "unknown_column": "whoops",
        }
        game = _row_to_orm(Game, row)
        assert game.id == 1
        assert not hasattr(game, "unknown_column") or getattr(game, "unknown_column", None) is None


# ---------------------------------------------------------------------------
# Seed table ordering
# ---------------------------------------------------------------------------


class TestSeedTableOrdering:
    """Verify seed tables are in correct FK-safe order."""

    def test_games_first(self):
        assert _SEED_TABLES[0]["csv"] == "games.csv"

    def test_match_analyses_after_games(self):
        csv_names = [e["csv"] for e in _SEED_TABLES]
        games_idx = csv_names.index("games.csv")
        ma_idx = csv_names.index("match_analyses.csv")
        assert ma_idx > games_idx

    def test_tips_after_games(self):
        csv_names = [e["csv"] for e in _SEED_TABLES]
        games_idx = csv_names.index("games.csv")
        tips_idx = csv_names.index("tips.csv")
        assert tips_idx > games_idx

    def test_model_predictions_after_games(self):
        csv_names = [e["csv"] for e in _SEED_TABLES]
        games_idx = csv_names.index("games.csv")
        mp_idx = csv_names.index("model_predictions.csv")
        assert mp_idx > games_idx

    def test_all_thirteen_tables_present(self):
        csv_names = {e["csv"] for e in _SEED_TABLES}
        expected = {
            "games.csv",
            "players.csv",
            "model_predictions.csv",
            "tips.csv",
            "elo_cache.csv",
            "backtest_results.csv",
            "match_analyses.csv",
            "match_weather.csv",
            "player_match_stats.csv",
            "player_advanced_stats.csv",
            "injuries.csv",
            "generation_progress.csv",
            "job_executions.csv",
        }
        assert csv_names == expected


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------


class TestRunMigrations:
    """Test migration execution."""

    @patch("scripts.migrate_and_seed.command")
    @patch("scripts.migrate_and_seed._build_alembic_config")
    def test_calls_upgrade_head(self, mock_config, mock_command):
        mock_cfg = MagicMock()
        mock_config.return_value = mock_cfg

        run_migrations("postgresql://localhost/db", verbose=False)

        mock_command.upgrade.assert_called_once_with(mock_cfg, "head")

    @patch("scripts.migrate_and_seed.command")
    @patch("scripts.migrate_and_seed._build_alembic_config")
    def test_verbose_prints(self, mock_config, mock_command, capsys):
        mock_config.return_value = MagicMock()

        run_migrations("postgresql://user:pass@localhost:5432/db", verbose=True)

        output = capsys.readouterr().out
        assert "upgrade head" in output
        assert "Migrations complete" in output


# ---------------------------------------------------------------------------
# Clear database
# ---------------------------------------------------------------------------


class TestClearDatabase:
    """Test database clearing."""

    @pytest.mark.asyncio
    async def test_deletes_from_all_tables(self):
        mock_conn = AsyncMock()

        # Build a mock engine whose begin() returns an async context manager
        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()
        begin_ctx = MagicMock()
        begin_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        begin_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_engine.begin.return_value = begin_ctx

        with patch("scripts.migrate_and_seed.create_async_engine", return_value=mock_engine):
            await clear_database("postgresql+asyncpg://localhost/db", verbose=False)

        # Should have called execute for each table
        assert mock_conn.execute.call_count == len(_CLEAR_TABLES)

    @pytest.mark.asyncio
    async def test_clear_order(self):
        mock_conn = AsyncMock()

        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()
        begin_ctx = MagicMock()
        begin_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        begin_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_engine.begin.return_value = begin_ctx

        with patch("scripts.migrate_and_seed.create_async_engine", return_value=mock_engine):
            await clear_database("postgresql+asyncpg://localhost/db", verbose=False)

        # Verify tables are cleared in FK-safe reverse order
        executed_tables = [
            c.args[0].text for c in mock_conn.execute.call_args_list
        ]
        for actual, expected in zip(executed_tables, _CLEAR_TABLES):
            assert f"DELETE FROM {expected}" in actual


# ---------------------------------------------------------------------------
# Seed from CSV
# ---------------------------------------------------------------------------


class TestSeedFromCsv:
    """Test CSV seeding functionality."""

    def _make_mock_session(self) -> AsyncMock:
        """Build a mock AsyncSession with all needed methods."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.close = AsyncMock()
        mock_session.flush = AsyncMock()
        mock_session.add_all = MagicMock()
        # async with mock_session as s: → s == mock_session
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        return mock_session

    def _make_mock_engine(self, *, with_begin: bool = False) -> MagicMock:
        """Build a mock async engine.

        If *with_begin* is True, ``engine.begin()`` returns an async context
        manager yielding a mock connection (used by ``clear_database``).
        """
        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()
        if with_begin:
            mock_conn = AsyncMock()
            begin_ctx = MagicMock()
            begin_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
            begin_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_engine.begin.return_value = begin_ctx
        return mock_engine

    @pytest.mark.asyncio
    async def test_loads_games_csv(self, tmp_path):
        """Test that games.csv is correctly loaded and inserted."""
        csv_path = tmp_path / "games.csv"
        csv_path.write_text(
            "id,slug,squiggle_id,round_id,season,home_team,away_team,"
            "home_score,away_score,venue,date,completed,predictions_generated,"
            "tips_generated,last_synced_at,sync_version\n"
            "1,abc123,10001,1,2025,Brisbane,Collingwood,85,72,Gabba,"
            "2025-04-15T07:30:00+00:00,True,True,True,"
            "2025-06-01T00:00:00+00:00,1\n"
        )

        mock_engine = self._make_mock_engine()
        mock_session = self._make_mock_session()

        # session_factory() must return the async context manager (mock_session)
        mock_sf = MagicMock(return_value=mock_session)

        with (
            patch("scripts.migrate_and_seed.create_async_engine", return_value=mock_engine),
            patch("scripts.migrate_and_seed.async_sessionmaker", return_value=mock_sf),
        ):
            counts = await seed_from_csv(
                "postgresql+asyncpg://localhost/db",
                seed_dir=tmp_path,
                verbose=False,
            )

        assert "games" in counts
        assert counts["games"] == 1
        mock_session.add_all.assert_called()
        # Verify the object passed is a Game instance
        added_objects = mock_session.add_all.call_args[0][0]
        assert isinstance(added_objects[0], Game)
        assert added_objects[0].home_team == "Brisbane"

    @pytest.mark.asyncio
    async def test_skips_missing_csv(self, tmp_path):
        """Test that missing CSV files are gracefully skipped."""
        mock_engine = self._make_mock_engine()
        mock_session = self._make_mock_session()
        mock_sf = MagicMock(return_value=mock_session)

        with (
            patch("scripts.migrate_and_seed.create_async_engine", return_value=mock_engine),
            patch("scripts.migrate_and_seed.async_sessionmaker", return_value=mock_sf),
        ):
            counts = await seed_from_csv(
                "postgresql+asyncpg://localhost/db",
                seed_dir=tmp_path,  # Empty dir, no CSV files
                verbose=True,
            )

        assert counts == {}

    @pytest.mark.asyncio
    async def test_clears_before_seeding(self, tmp_path):
        """Test that --clear wipes data before seeding."""
        csv_path = tmp_path / "games.csv"
        csv_path.write_text(
            "id,slug,round_id,season,home_team,away_team,venue\n"
            "1,abc,1,2025,Brisbane,Collingwood,Gabba\n"
        )

        # Engine with begin() support for clear_database
        mock_engine = self._make_mock_engine(with_begin=True)
        mock_session = self._make_mock_session()
        mock_sf = MagicMock(return_value=mock_session)

        with (
            patch("scripts.migrate_and_seed.create_async_engine", return_value=mock_engine),
            patch("scripts.migrate_and_seed.async_sessionmaker", return_value=mock_sf),
        ):
            counts = await seed_from_csv(
                "postgresql+asyncpg://localhost/db",
                seed_dir=tmp_path,
                clear=True,
                verbose=False,
            )

        # Should have deleted from all clear tables
        mock_conn = mock_engine.begin.return_value.__aenter__.return_value
        assert mock_conn.execute.call_count == len(_CLEAR_TABLES)
        assert "games" in counts

    @pytest.mark.asyncio
    async def test_skips_empty_csv(self, tmp_path):
        """Test that empty CSV files (headers only) are skipped."""
        csv_path = tmp_path / "games.csv"
        csv_path.write_text("id,slug,round_id,season,home_team,away_team,venue\n")

        mock_engine = self._make_mock_engine()
        mock_session = self._make_mock_session()
        mock_sf = MagicMock(return_value=mock_session)

        with (
            patch("scripts.migrate_and_seed.create_async_engine", return_value=mock_engine),
            patch("scripts.migrate_and_seed.async_sessionmaker", return_value=mock_sf),
        ):
            counts = await seed_from_csv(
                "postgresql+asyncpg://localhost/db",
                seed_dir=tmp_path,
                verbose=False,
            )

        # Empty CSV → 0 data rows → no records inserted
        assert "games" not in counts

    @pytest.mark.asyncio
    async def test_multiple_tables(self, tmp_path):
        """Test loading multiple CSV files."""
        # Games
        (tmp_path / "games.csv").write_text(
            "id,slug,round_id,season,home_team,away_team,venue\n"
            "1,abc,1,2025,Brisbane,Collingwood,Gabba\n"
        )
        # Tips
        (tmp_path / "tips.csv").write_text(
            "id,game_id,heuristic,selected_team,margin,confidence,explanation\n"
            "1,1,best_bet,Brisbane,15,0.78,Strong pick.\n"
        )

        mock_engine = self._make_mock_engine()
        mock_session = self._make_mock_session()
        mock_sf = MagicMock(return_value=mock_session)

        with (
            patch("scripts.migrate_and_seed.create_async_engine", return_value=mock_engine),
            patch("scripts.migrate_and_seed.async_sessionmaker", return_value=mock_sf),
        ):
            counts = await seed_from_csv(
                "postgresql+asyncpg://localhost/db",
                seed_dir=tmp_path,
                verbose=False,
            )

        assert "games" in counts
        assert "tips" in counts
        assert counts["games"] == 1
        assert counts["tips"] == 1

        # Verify add_all was called with correct types
        all_calls = mock_session.add_all.call_args_list
        # games.csv and tips.csv both present → 2 add_all calls
        assert len(all_calls) == 2


# ---------------------------------------------------------------------------
# CLI: resolve database URL
# ---------------------------------------------------------------------------


class TestResolveDatabaseUrl:
    """Test URL resolution from args / env / .env."""

    def test_from_args(self):
        args = argparse.Namespace(database_url="postgresql://from:args@localhost/db")
        result = _resolve_database_url(args)
        assert result == "postgresql://from:args@localhost/db"

    def test_from_env_var(self):
        args = argparse.Namespace(database_url=None)
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://env:var@localhost/db"}):
            result = _resolve_database_url(args)
        assert result == "postgresql://env:var@localhost/db"

    def test_from_dotenv_file(self, tmp_path):
        args = argparse.Namespace(database_url=None)
        env_file = tmp_path / ".env"
        env_file.write_text('DATABASE_URL="postgresql://dotenv:file@localhost/db"\n')

        # Patch the _BACKEND_FAAS_DIR to use tmp_path
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("scripts.migrate_and_seed._BACKEND_FAAS_DIR", tmp_path),
        ):
            # Remove DATABASE_URL from env if present
            os.environ.pop("DATABASE_URL", None)
            result = _resolve_database_url(args)
        assert result == "postgresql://dotenv:file@localhost/db"

    def test_exits_if_no_url(self):
        args = argparse.Namespace(database_url=None)
        with (
            pytest.raises(SystemExit) as exc_info,
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("DATABASE_URL", None)
            # Ensure no .env file is found
            with patch("scripts.migrate_and_seed._BACKEND_FAAS_DIR", Path("/nonexistent")):
                _resolve_database_url(args)
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Integration-style: verify actual CSV files parse
# ---------------------------------------------------------------------------


class TestRealCsvParsing:
    """Verify that the actual seed CSV files in seed_data/ can be parsed."""

    SEED_DIR = Path(__file__).resolve().parent.parent.parent / "seed_data"

    @pytest.mark.skipif(
        not SEED_DIR.exists(),
        reason="seed_data directory not found",
    )
    def test_games_csv_parses(self):
        csv_path = self.SEED_DIR / "games.csv"
        if not csv_path.exists():
            pytest.skip("games.csv not found")

        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) > 0, "games.csv should have data rows"
        # First row should convert to a Game
        game = _row_to_orm(Game, rows[0])
        assert isinstance(game, Game)
        assert game.id is not None
        assert game.home_team is not None

    @pytest.mark.skipif(
        not SEED_DIR.exists(),
        reason="seed_data directory not found",
    )
    def test_tips_csv_parses(self):
        csv_path = self.SEED_DIR / "tips.csv"
        if not csv_path.exists():
            pytest.skip("tips.csv not found")

        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) > 0
        tip = _row_to_orm(Tip, rows[0])
        assert isinstance(tip, Tip)
        assert tip.heuristic is not None

    @pytest.mark.skipif(
        not SEED_DIR.exists(),
        reason="seed_data directory not found",
    )
    def test_elo_cache_csv_parses(self):
        csv_path = self.SEED_DIR / "elo_cache.csv"
        if not csv_path.exists():
            pytest.skip("elo_cache.csv not found")

        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) > 0
        elo = _row_to_orm(EloCache, rows[0])
        assert isinstance(elo, EloCache)
        assert elo.team_name is not None
        assert elo.rating is not None
