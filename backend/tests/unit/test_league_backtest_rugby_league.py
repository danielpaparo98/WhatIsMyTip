"""Rugby-league venue-normalized backtest (Phase 5.2, subtask 11).

The national counterpart of ``test_league_backtest_service.py``: the
league backtest surface (``LeagueBacktestService`` + the
``/api/backtest`` league dispatch) graded over the venue-alias-
normalized 2017+ rugby-league history.

Contracts under test:

* **Reduced model set only** — grading consumes
  ``league_model_set_for_sport('rugby-league')`` = (``elo``, ``form``,
  ``home_advantage``, ``matchup``); ``ladder`` never appears in a
  rugby-league payload and its tips are never fetched.
* **API league dispatch** — ``?league=nrl|nrlw|origin`` resolves through
  the cross-registry ``get_league`` facade (the guardrail flip: these
  keys used to 404 as state-registry unknowns) and passes the reduced
  set even before the competition is first synced; unknown keys stay a
  404 in the repo-standard shape; absent/``afl``/state keys keep the
  legacy payloads byte-identical (``models=None`` → the D3 default).
* **Draws honored in grading** (``has_draws=True``) — a drawn event has
  no winner, so every tip on it grades incorrect but settles as a PUSH
  ($0, stake refunded) per the ``settlement.py`` kernel — never a loss.
* **Odds-free profit honesty** — rugby-league has no odds source, so
  every tip settles at ``FALLBACK_DECIMAL_ODDS`` ($1.90) and
  ``odds_coverage`` reports 0.0; the profit columns keep the AFL payload
  shape (additive-only frozen surface) while honestly surfacing that no
  real prices were used.
* **End-to-end (``@pytest.mark.postgres``)** — a seeded rugby-league
  competition whose events carry CANONICAL venue names (the
  ``nrl_historic_load`` venue-normalization invariant), tips generated
  by the real pipeline, backtested through the real service.  Skips
  when podman is unavailable.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Iterator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from packages.shared.ingestion.league_seeding import (
    SPORT_ID as RUGBY_LEAGUE_SPORT_ID,
)
from packages.shared.ingestion.national_leagues import NATIONAL_LEAGUES
from packages.shared.ingestion.venue_aliases import CANONICAL_VENUES
from packages.shared.services.league_backtest import (
    GradedTip,
    LeagueBacktestService,
    grade_tip,
)
from packages.shared.services.league_heuristics import (
    HEURISTIC_ELO,
    HEURISTIC_FORM,
    HEURISTIC_HOME_ADVANTAGE,
    HEURISTIC_MATCHUP,
    LEAGUE_HEURISTICS,
    RUGBY_LEAGUE_MODELS,
    LeagueHeuristicsService,
    league_model_set_for_sport,
)

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Odds-free settlement: $10 stake at the representative $1.90 price.
_FALLBACK_WIN = 10.0 * (1.90 - 1.0)  # +$9

_NATIONAL_KEYS = ("nrl", "nrlw", "origin")


# ---------------------------------------------------------------------------
# Registry → grading consumption (no database).
# ---------------------------------------------------------------------------


class TestGradingConsumesTheRugbyLeagueSet:
    """The backtest grades exactly the registered rugby-league models."""

    def test_rugby_league_resolves_to_the_reduced_set(self) -> None:
        assert league_model_set_for_sport(RUGBY_LEAGUE_SPORT_ID) == (
            RUGBY_LEAGUE_MODELS
        )

    def test_reduced_set_is_exactly_the_four_db_only_models(self) -> None:
        assert RUGBY_LEAGUE_MODELS == (
            HEURISTIC_ELO,
            HEURISTIC_FORM,
            HEURISTIC_HOME_ADVANTAGE,
            HEURISTIC_MATCHUP,
        )

    def test_ladder_is_not_a_rugby_league_model(self) -> None:
        assert "ladder" not in RUGBY_LEAGUE_MODELS

    def test_national_keys_are_exactly_the_three_competitions(self) -> None:
        assert set(NATIONAL_LEAGUES) == set(_NATIONAL_KEYS)


# ---------------------------------------------------------------------------
# Service orchestration — patched fetchers, no database.
# ---------------------------------------------------------------------------

_DB = AsyncMock()  # the service never touches it directly here


@contextmanager
def _patched_fetches(
    service: LeagueBacktestService,
    *,
    seasons: list,
    tipped_labels: list[str],
    season_ids: dict[str, int],
    tips_by_heuristic: dict[str, list[GradedTip]],
    fixture_rounds: tuple[int, int],
) -> Iterator[dict[str, AsyncMock]]:
    """Patch the instance fetchers, exposing the mocks for assertions."""
    mocks: dict[str, AsyncMock] = {
        "seasons": AsyncMock(return_value=seasons),
        "tipped": AsyncMock(return_value=tipped_labels),
        "season_id": AsyncMock(
            side_effect=lambda db, competition_id, label: season_ids.get(label)
        ),
        "tips": AsyncMock(
            side_effect=lambda db, season_id, heuristic: list(
                tips_by_heuristic.get(heuristic, [])
            )
        ),
        "rounds": AsyncMock(return_value=fixture_rounds),
    }
    with patch.object(service, "_fetch_seasons", mocks["seasons"]), patch.object(
        service, "_fetch_tipped_season_labels", mocks["tipped"]
    ), patch.object(service, "_fetch_season_id", mocks["season_id"]), patch.object(
        service, "_fetch_graded_tips", mocks["tips"]
    ), patch.object(service, "_fetch_fixture_rounds", mocks["rounds"]):
        yield mocks


def _tip(
    round_id: int | None = 1,
    *,
    selected: int | None = 101,
    home_side: int = 101,
    away_side: int = 102,
    home_score: int | None = 90,
    away_score: int | None = 70,
) -> GradedTip:
    """A graded tip; home 101 beats away 102 unless scores say otherwise."""
    return grade_tip(
        round_id=round_id,
        home_side_id=home_side,
        away_side_id=away_side,
        home_score=home_score,
        away_score=away_score,
        selected_side_id=selected,
        decimal_odds=None,
    )


class TestCompareSeasonReducedSet:
    @pytest.mark.asyncio
    async def test_comparison_covers_exactly_the_reduced_set(self) -> None:
        """Every rugby-league model is graded; ladder never appears and
        its tips are never fetched (the reduced-set-only contract)."""
        tips = {name: [_tip(1)] for name in RUGBY_LEAGUE_MODELS}
        service = LeagueBacktestService()
        with _patched_fetches(
            service,
            seasons=[],
            tipped_labels=["2025"],
            season_ids={"2025": 11},
            tips_by_heuristic=tips,
            fixture_rounds=(0, 0),
        ) as mocks:
            payload = await service.compare_season(
                _DB,
                competition_id=42,
                season_label="2025",
                models=RUGBY_LEAGUE_MODELS,
            )

        assert set(payload["comparison"]) == set(RUGBY_LEAGUE_MODELS)
        assert "ladder" not in payload["comparison"]
        # One graded-tips fetch PER REDUCED MODEL — never a ladder fetch.
        assert mocks["tips"].await_count == len(RUGBY_LEAGUE_MODELS)

    @pytest.mark.asyncio
    async def test_draw_pushes_and_counts_as_an_incorrect_tip(self) -> None:
        """Draws honored in grading (has_draws=True): the draw round is
        in the accuracy denominator as incorrect, but settles $0."""
        tips = {
            HEURISTIC_HOME_ADVANTAGE: [
                _tip(1, home_score=90, away_score=70),  # correct, +$9
                _tip(2, selected=None, home_score=80, away_score=80),  # draw: push
                _tip(3, home_score=30, away_score=10),  # correct, +$9
            ],
        }
        service = LeagueBacktestService()
        with _patched_fetches(
            service,
            seasons=[],
            tipped_labels=["2025"],
            season_ids={"2025": 11},
            tips_by_heuristic=tips,
            fixture_rounds=(0, 0),
        ):
            payload = await service.compare_season(
                _DB,
                competition_id=42,
                season_label="2025",
                models=RUGBY_LEAGUE_MODELS,
            )

        stats = payload["comparison"][HEURISTIC_HOME_ADVANTAGE]
        assert stats["total_tips"] == 3  # the draw tip stays a graded tip
        assert stats["total_correct"] == 2
        assert stats["overall_accuracy"] == pytest.approx(2 / 3)
        # Both decided tips won at the fallback price; the draw round
        # contributed $0 — NOT −$10 (draws push, never a loss).
        assert stats["total_profit"] == pytest.approx(2 * _FALLBACK_WIN)
        assert stats["total_rounds"] == 3

    @pytest.mark.asyncio
    async def test_odds_free_profit_at_the_fallback_price_and_zero_coverage(
        self,
    ) -> None:
        """Rugby-league has no odds source: every tip settles at the
        representative $1.90 fallback and odds_coverage reports 0.0 —
        the honest no-odds shape, profit columns kept for payload parity."""
        tips = {
            HEURISTIC_ELO: [
                _tip(1, home_score=90, away_score=70),  # correct → +$9
                _tip(2, selected=102, home_score=50, away_score=5),  # wrong → −$10
            ],
        }
        service = LeagueBacktestService()
        with _patched_fetches(
            service,
            seasons=[],
            tipped_labels=["2025"],
            season_ids={"2025": 11},
            tips_by_heuristic=tips,
            fixture_rounds=(0, 0),
        ):
            payload = await service.compare_season(
                _DB,
                competition_id=42,
                season_label="2025",
                models=RUGBY_LEAGUE_MODELS,
            )

        stats = payload["comparison"][HEURISTIC_ELO]
        assert stats["total_profit"] == pytest.approx(_FALLBACK_WIN - 10.0)
        assert stats["odds_coverage"] == 0.0

    @pytest.mark.asyncio
    async def test_unknown_label_zero_structure_uses_the_reduced_set(self) -> None:
        service = LeagueBacktestService()
        with _patched_fetches(
            service,
            seasons=[],
            tipped_labels=[],
            season_ids={},
            tips_by_heuristic={},
            fixture_rounds=(0, 0),
        ):
            payload = await service.compare_season(
                _DB,
                competition_id=42,
                season_label="1999",
                models=RUGBY_LEAGUE_MODELS,
            )

        assert payload["season"] == "1999"
        assert set(payload["comparison"]) == set(RUGBY_LEAGUE_MODELS)
        assert payload["best_overall"] == {
            "heuristic": None,
            "accuracy": 0.0,
            "profit": 0.0,
        }

    @pytest.mark.asyncio
    async def test_default_models_keep_the_d3_trio(self) -> None:
        """No ``models`` argument → the historical D3 default, so every
        pre-existing state-league payload stays byte-identical."""
        service = LeagueBacktestService()
        with _patched_fetches(
            service,
            seasons=[],
            tipped_labels=[],
            season_ids={},
            tips_by_heuristic={},
            fixture_rounds=(0, 0),
        ):
            payload = await service.compare_season(
                _DB, competition_id=42, season_label="1999"
            )

        assert set(payload["comparison"]) == set(LEAGUE_HEURISTICS)


class TestCurrentSeasonReducedSet:
    @pytest.mark.asyncio
    async def test_heuristics_are_exactly_the_reduced_set(self) -> None:
        service = LeagueBacktestService()
        with _patched_fetches(
            service,
            seasons=[],
            tipped_labels=[],
            season_ids={},
            tips_by_heuristic={},
            fixture_rounds=(0, 0),
        ):
            payload = await service.get_current_season_performance(
                _DB, competition_id=42, models=RUGBY_LEAGUE_MODELS
            )

        assert payload["season"] is None
        assert payload["rounds_completed"] == 0
        assert payload["total_rounds"] == 0
        assert [h["heuristic"] for h in payload["heuristics"]] == list(
            RUGBY_LEAGUE_MODELS
        )
        assert all(h["total_profit"] == 0.0 for h in payload["heuristics"])

    @pytest.mark.asyncio
    async def test_default_models_keep_the_d3_trio(self) -> None:
        service = LeagueBacktestService()
        with _patched_fetches(
            service,
            seasons=[],
            tipped_labels=[],
            season_ids={},
            tips_by_heuristic={},
            fixture_rounds=(0, 0),
        ):
            payload = await service.get_current_season_performance(
                _DB, competition_id=42
            )

        assert [h["heuristic"] for h in payload["heuristics"]] == list(
            LEAGUE_HEURISTICS
        )


# ---------------------------------------------------------------------------
# API dispatch — ?league=nrl|nrlw|origin resolves through the facade.
# ---------------------------------------------------------------------------


def _build_app_with_backtest_router(monkeypatch=None):
    """Minimal FastAPI app with the backtest router + error handlers."""
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    from app.api.backtest import router
    from app.core.exceptions import BackendServiceError

    app = FastAPI()
    app.include_router(router, prefix="/api/backtest")

    @app.exception_handler(BackendServiceError)
    async def _backend_error_handler(_request, exc: BackendServiceError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "request_id": "test-request-id",
            },
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(_request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": "Invalid request",
                "errors": exc.errors(),
                "request_id": "test-request-id",
            },
        )

    if monkeypatch is not None:
        from packages.shared.config import settings
        monkeypatch.setattr(settings, "admin_api_key", "the-secret-key")

    return app


def _override_db(app, mock_session: AsyncSession) -> None:
    from app.core import db_deps

    async def _override():
        yield mock_session

    app.dependency_overrides[db_deps.get_db] = _override


class _ScalarResult:
    """``execute()`` result resolving one scalar (the competition lookup)."""

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _EmptyResult:
    """``execute()`` result with no rows — the unsynced-competition case."""

    def scalar_one_or_none(self):
        return None

    def scalar(self):
        return 0

    def all(self):
        return []


def _session_returning(value) -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=value)
    return session


class TestNationalLeagueDispatch:
    """``?league=nrl|nrlw|origin`` — valid keys via the get_league facade
    (the GUARDRAIL FLIP: these used to 404 as state-registry unknowns)."""

    @pytest.mark.parametrize("league", _NATIONAL_KEYS)
    def test_seasons_for_national_leagues(self, league: str):
        session = _session_returning(_ScalarResult(42))
        app = _build_app_with_backtest_router()
        _override_db(app, session)

        with patch("app.api.backtest.LeagueBacktestService") as league_cls:
            league_cls.return_value.get_available_seasons = AsyncMock(
                return_value={"available_years": ["2026"], "current_year": "2026"}
            )
            client = TestClient(app)
            resp = client.get(f"/api/backtest/seasons?league={league}")

        assert resp.status_code == 200
        assert resp.json() == {
            "available_years": ["2026"],
            "current_year": "2026",
        }
        # The seasons payload is heuristic-free — no models kwarg.
        league_cls.return_value.get_available_seasons.assert_awaited_once_with(
            session, competition_id=42
        )

    def test_compare_for_nrl_passes_the_reduced_set(self):
        session = _session_returning(_ScalarResult(42))
        app = _build_app_with_backtest_router()
        _override_db(app, session)

        with patch("app.api.backtest.LeagueBacktestService") as league_cls:
            league_cls.return_value.compare_season = AsyncMock(
                return_value={"season": "2024", "comparison": {}, "best_overall": {}}
            )
            client = TestClient(app)
            resp = client.get("/api/backtest/compare?league=nrl&season=2024")

        assert resp.status_code == 200
        league_cls.return_value.compare_season.assert_awaited_once_with(
            session,
            competition_id=42,
            season_label="2024",
            models=RUGBY_LEAGUE_MODELS,
        )

    def test_current_season_for_origin_passes_the_reduced_set(self):
        session = _session_returning(_ScalarResult(7))
        app = _build_app_with_backtest_router()
        _override_db(app, session)

        with patch("app.api.backtest.LeagueBacktestService") as league_cls:
            league_cls.return_value.get_current_season_performance = AsyncMock(
                return_value={"season": "2026", "heuristics": []}
            )
            client = TestClient(app)
            resp = client.get("/api/backtest/current-season?league=origin")

        assert resp.status_code == 200
        league_cls.return_value.get_current_season_performance.assert_awaited_once_with(
            session, competition_id=7, models=RUGBY_LEAGUE_MODELS
        )

    @pytest.mark.parametrize("league", _NATIONAL_KEYS)
    def test_unsynced_national_league_renders_the_reduced_zero_payload(
        self, league: str
    ):
        """Registered but never synced: the REAL service runs against an
        empty DB — and the zero payload already renders the rugby-league
        model list (the set is registry-derived, not data-derived)."""
        session = _session_returning(_EmptyResult())
        app = _build_app_with_backtest_router()
        _override_db(app, session)

        client = TestClient(app)
        resp = client.get(f"/api/backtest/current-season?league={league}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] is None
        assert [h["heuristic"] for h in body["heuristics"]] == list(
            RUGBY_LEAGUE_MODELS
        )
        assert all(
            h["rounds_played"] == 0 and h["total_profit"] == 0.0
            for h in body["heuristics"]
        )

    def test_unsynced_nrl_compare_zero_comparison(self):
        session = _session_returning(_EmptyResult())
        app = _build_app_with_backtest_router()
        _override_db(app, session)

        client = TestClient(app)
        resp = client.get("/api/backtest/compare?league=nrl&season=2017")

        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] == "2017"
        assert set(body["comparison"]) == set(RUGBY_LEAGUE_MODELS)
        assert body["best_overall"] == {
            "heuristic": None,
            "accuracy": 0.0,
            "profit": 0.0,
        }

    def test_unsynced_nrl_seasons_empty_payload(self):
        session = _session_returning(_EmptyResult())
        app = _build_app_with_backtest_router()
        _override_db(app, session)

        client = TestClient(app)
        resp = client.get("/api/backtest/seasons?league=nrl")

        assert resp.status_code == 200
        assert resp.json() == {"available_years": [], "current_year": None}

    def test_unknown_league_still_returns_404(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch("app.api.backtest.LeagueBacktestService") as league_cls:
            client = TestClient(app)
            resp = client.get("/api/backtest/seasons?league=not-a-league")

        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"
        assert "not-a-league" in body["message"].lower()
        league_cls.return_value.get_available_seasons.assert_not_called()
        mock_session.execute.assert_not_called()

    def test_league_keys_stay_case_sensitive(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch("app.api.backtest.LeagueBacktestService"):
            client = TestClient(app)
            resp = client.get("/api/backtest/seasons?league=NRL")

        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"

    def test_legacy_afl_path_never_receives_models(self):
        """Absent league → the AFL path; the league service is untouched."""
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch("app.api.backtest.BacktestService") as legacy_cls, patch(
            "app.api.backtest.LeagueBacktestService"
        ) as league_cls:
            legacy_cls.return_value.get_available_seasons = AsyncMock(
                return_value=[2024, 2025]
            )
            client = TestClient(app)
            resp = client.get("/api/backtest/seasons")

        assert resp.status_code == 200
        assert resp.json()["available_years"] == [2024, 2025]
        league_cls.return_value.get_available_seasons.assert_not_called()
        mock_session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# End-to-end on real Postgres (podman testcontainer): venue-normalized
# rugby-league history → tips → backtest.  Skips without podman.
# ---------------------------------------------------------------------------

_POSTGRES_IMAGE = "docker.io/library/postgres:16-alpine"
_CONTAINER_NAME_PREFIX = "wimt-pg-lb11-"
_STARTUP_TIMEOUT_S = 60.0
_POLL_INTERVAL_S = 0.25


def _podman_unavailable_reason() -> str | None:
    if shutil.which("podman") is None:
        return "podman CLI not available"
    probe = subprocess.run(
        ["podman", "info", "--format", "{{.Host.Arch}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if probe.returncode != 0:
        return f"podman daemon unavailable: {probe.stderr.strip()[:200]}"
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_postgres_ready(container_name: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        proc = subprocess.run(
            ["podman", "exec", container_name, "pg_isready", "-U", "postgres"],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return
        last_err = RuntimeError(f"pg_isready rc={proc.returncode}")
        time.sleep(_POLL_INTERVAL_S)
    raise RuntimeError(f"Postgres not ready in {timeout:.1f}s: {last_err}")


@pytest.fixture(scope="module")
def pg_container() -> Iterator[tuple[str, str]]:
    reason = _podman_unavailable_reason()
    if reason is not None:
        pytest.skip(reason)

    container_name = _CONTAINER_NAME_PREFIX + uuid.uuid4().hex[:12]
    port = _free_port()
    user, password, db = "postgres", "test", "postgres"

    proc = subprocess.run(
        ["podman", "run", "-d", "--rm", "--name", container_name,
         "-p", f"127.0.0.1:{port}:5432",
         "-e", f"POSTGRES_USER={user}", "-e", f"POSTGRES_PASSWORD={password}",
         "-e", f"POSTGRES_DB={db}", _POSTGRES_IMAGE],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"podman run failed: {proc.stderr.strip()}")

    try:
        _wait_for_postgres_ready(container_name, _STARTUP_TIMEOUT_S)
        yield (
            f"postgresql://{user}:{password}@127.0.0.1:{port}/{db}",
            f"postgresql+asyncpg://{user}:{password}@127.0.0.1:{port}/{db}",
        )
    finally:
        subprocess.run(["podman", "rm", "-f", container_name],
                       capture_output=True, text=True)


@pytest_asyncio.fixture
async def _db_at_head(pg_container: tuple[str, str]) -> AsyncIterator[tuple[str, str]]:
    """Empty public schema upgraded to head. Yields (sync_dsn, async_dsn)."""
    sync_dsn, async_dsn = pg_container
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()

    proc = _run_alembic(sync_dsn, "upgrade", "head")
    assert proc.returncode == 0, f"upgrade to head failed:\n{proc.stdout}\n{proc.stderr}"
    yield sync_dsn, async_dsn


def _run_alembic(dsn: str, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "alembic", *args]
    env = {**os.environ, "DATABASE_URL": dsn}
    return subprocess.run(
        cmd, cwd=_BACKEND_DIR, capture_output=True, text=True, env=env, timeout=180
    )


async def _seed_rugby_league(async_dsn: str) -> dict[str, int]:
    """A venue-normalized rugby-league seed (canonical grounds only).

    Season 2025: three completed events (one draw).  Season 2026: two
    completed + one scheduled.  Every venue is a canonical ground from
    ``venue_aliases.CANONICAL_VENUES`` — the invariant the historic
    backfill establishes at load time.
    """
    engine = create_async_engine(async_dsn, poolclass=NullPool)

    async def one(conn: Any, sql: str, params: dict[str, Any] | None = None) -> int:
        return (await conn.execute(text(sql), params or {})).scalar_one()

    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO sports (id, display_name) "
                    "VALUES ('rugby-league', 'Rugby League') "
                    "ON CONFLICT (id) DO NOTHING"
                )
            )
            cid = await one(
                conn,
                "INSERT INTO competitions (sport_id, name, tier, format, timezone) "
                "VALUES ('rugby-league', 'NRL Backtest Comp', 'national', 'rounds', "
                "'Australia/Brisbane') RETURNING id",
            )
            s2025 = await one(
                conn,
                "INSERT INTO seasons (competition_id, label, is_current) "
                "VALUES (:cid, '2025', false) RETURNING id",
                {"cid": cid},
            )
            s2026 = await one(
                conn,
                "INSERT INTO seasons (competition_id, label, is_current) "
                "VALUES (:cid, '2026', true) RETURNING id",
                {"cid": cid},
            )
            pids = {
                name: await one(
                    conn,
                    "INSERT INTO participants (sport_id, kind, name) "
                    "VALUES ('rugby-league', 'team', :name) RETURNING id",
                    {"name": name},
                )
                for name in ("Broncos", "Storm", "Knights", "Eels")
            }

            # (no, year, season_id, round, venue, home, away, hs, as_, completed)
            fixtures: list[tuple[int, int, int, int, str, str, str,
                                 int | None, int | None, bool]] = [
                (1, 2025, s2025, 1, "Lang Park", "Broncos", "Storm",
                 28, 18, True),
                (2, 2025, s2025, 2, "Melbourne Rectangular Stadium", "Storm",
                 "Knights", 16, 16, True),  # the draw
                (3, 2025, s2025, 3, "Newcastle Stadium", "Knights", "Broncos",
                 30, 12, True),
                (4, 2026, s2026, 1, "Lang Park", "Broncos", "Storm",
                 24, 20, True),
                (5, 2026, s2026, 2, "Melbourne Rectangular Stadium", "Storm",
                 "Knights", 22, 10, True),
                (6, 2026, s2026, 3, "Newcastle Stadium", "Knights", "Eels",
                 None, None, False),
            ]
            for no, year, sid, rnd, venue, home, away, hs, as_, completed in fixtures:
                event_id = await one(
                    conn,
                    "INSERT INTO events (season_id, event_type, round_id, venue, "
                    "starts_at, status, completed, slug) "
                    "VALUES (:sid, 'match', :rnd, :venue, "
                    ":starts_at, :status, :completed, :slug) RETURNING id",
                    {
                        "sid": sid,
                        "rnd": rnd,
                        "venue": venue,
                        "starts_at": datetime(year, 3, 13 + rnd, 20, 5),
                        "status": "completed" if completed else "scheduled",
                        "completed": completed,
                        "slug": f"lb11e{no}",
                    },
                )
                decided = hs is not None and as_ is not None and hs != as_
                winner_side = (
                    ("home" if (hs or 0) > (as_ or 0) else "away") if decided else None
                )
                for side, pid, score in (
                    ("home", pids[home], hs),
                    ("away", pids[away], as_),
                ):
                    await conn.execute(
                        text(
                            "INSERT INTO event_participants "
                            "(event_id, participant_id, side, score, is_winner) "
                            "VALUES (:eid, :pid, :side, :score, :winner)"
                        ),
                        {
                            "eid": event_id,
                            "pid": pid,
                            "side": side,
                            "score": score,
                            "winner": (side == winner_side) if decided else None,
                        },
                    )
        return {"competition_id": cid, "season_2025": s2025, "season_2026": s2026}
    finally:
        await engine.dispose()


async def _generate_tips(async_dsn: str, competition_id: int) -> None:
    """Populate league_tips exactly as the sync hook would (the sport's
    reduced set: 5 completed events × 4 models = 20 tips)."""
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            summary = await LeagueHeuristicsService().generate_for_competition(
                session, competition_id=competition_id
            )
    finally:
        await engine.dispose()
    assert summary["tips_inserted"] == 20


def _by_heuristic(entries: list[dict]) -> dict[str, dict]:
    return {entry["heuristic"]: entry for entry in entries}


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_history_is_venue_alias_normalized(_db_at_head) -> None:
    """Every seeded event carries a CANONICAL ground — the invariant the
    historic backfill guarantees, which makes backtest grouping
    venue-stable across sponsor drift."""
    sync_dsn, async_dsn = _db_at_head
    ids = await _seed_rugby_league(async_dsn)

    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            venues = (
                await conn.execute(
                    text(
                        "SELECT DISTINCT e.venue FROM events e "
                        "WHERE e.season_id IN (:s25, :s26) AND e.venue IS NOT NULL"
                    ),
                    {"s25": ids["season_2025"], "s26": ids["season_2026"]},
                )
            ).scalars().all()
    finally:
        await engine.dispose()

    assert set(venues) <= CANONICAL_VENUES
    # The seed inserts canonical grounds directly (it bypasses the
    # provider, which is where alias collapse happens — covered by the
    # provider and historic-load tests), so the invariant to pin here
    # is storage-level: nothing non-canonical ever reaches events.
    assert "Lang Park" in set(venues)


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_current_season_end_to_end_over_normalized_history(
    _db_at_head,
) -> None:
    sync_dsn, async_dsn = _db_at_head
    ids = await _seed_rugby_league(async_dsn)
    await _generate_tips(async_dsn, ids["competition_id"])

    engine = create_async_engine(async_dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            service = LeagueBacktestService()
            seasons = await service.get_available_seasons(
                session, competition_id=ids["competition_id"]
            )
            current = await service.get_current_season_performance(
                session,
                competition_id=ids["competition_id"],
                models=RUGBY_LEAGUE_MODELS,
            )
    finally:
        await engine.dispose()

    assert seasons == {
        "available_years": ["2026", "2025"],
        "current_year": "2026",
    }

    # 2026 fixture: three rounds, of which rounds 1-2 are fully played.
    assert current["season"] == "2026"
    assert current["total_rounds"] == 3
    assert current["rounds_completed"] == 2

    by_heuristic = _by_heuristic(current["heuristics"])
    # The REDUCED set only — ladder has no rugby-league entry at all.
    assert set(by_heuristic) == set(RUGBY_LEAGUE_MODELS)
    assert "ladder" not in by_heuristic

    home = by_heuristic[HEURISTIC_HOME_ADVANTAGE]
    assert home["total_accuracy"] == pytest.approx(1.0)  # both homes won
    assert home["total_profit"] == pytest.approx(2 * _FALLBACK_WIN)
    assert home["rounds_played"] == 2
    assert home["avg_profit_per_round"] == pytest.approx(_FALLBACK_WIN)
    assert home["projected_annual_profit"] == pytest.approx(3 * _FALLBACK_WIN)
    assert home["odds_coverage"] == 0.0  # no odds source — honest zero

    # Matchup also reads 2/2: Broncos led the h2h coming into R1 (pick
    # home, won); Storm/Knights had only drawn (tie → home, won).
    matchup = by_heuristic[HEURISTIC_MATCHUP]
    assert matchup["total_accuracy"] == pytest.approx(1.0)
    assert matchup["total_profit"] == pytest.approx(2 * _FALLBACK_WIN)

    # Elo and form both back the Knights away in R2 (better rating /
    # more recent wins than the Storm) — the Storm won, so 1/2, −$1.
    for name in (HEURISTIC_ELO, HEURISTIC_FORM):
        entry = by_heuristic[name]
        assert entry["total_accuracy"] == pytest.approx(0.5), name
        assert entry["total_profit"] == pytest.approx(_FALLBACK_WIN - 10.0), name


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_compare_past_season_end_to_end(_db_at_head) -> None:
    sync_dsn, async_dsn = _db_at_head
    ids = await _seed_rugby_league(async_dsn)
    await _generate_tips(async_dsn, ids["competition_id"])

    engine = create_async_engine(async_dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            service = LeagueBacktestService()
            past = await service.compare_season(
                session,
                competition_id=ids["competition_id"],
                season_label="2025",
                models=RUGBY_LEAGUE_MODELS,
            )
    finally:
        await engine.dispose()

    assert past["season"] == "2025"
    comparison = past["comparison"]
    assert set(comparison) == set(RUGBY_LEAGUE_MODELS)

    # home_advantage: won R1 & R3, the R2 draw pushed ($0, graded
    # incorrect) → 2/3, +$18 across 3 rounds.
    home = comparison[HEURISTIC_HOME_ADVANTAGE]
    assert home["total_tips"] == 3
    assert home["total_correct"] == 2
    assert home["overall_accuracy"] == pytest.approx(2 / 3)
    assert home["total_profit"] == pytest.approx(2 * _FALLBACK_WIN)
    assert home["total_rounds"] == 3
    assert home["best_round_accuracy"] == pytest.approx(1.0)
    assert home["worst_round_accuracy"] == pytest.approx(0.0)  # the draw round
    assert home["odds_coverage"] == 0.0

    # Matchup: 2/3 (the draw round pushed; R3's h2h tie → home, won).
    matchup = comparison[HEURISTIC_MATCHUP]
    assert matchup["total_correct"] == 2
    assert matchup["total_profit"] == pytest.approx(2 * _FALLBACK_WIN)

    # Elo/form both missed R3 (they backed the away Broncos on prior
    # evidence) and pushed the R2 draw → 1/3, −$1.
    for name in (HEURISTIC_ELO, HEURISTIC_FORM):
        stats = comparison[name]
        assert stats["total_correct"] == 1, name
        assert stats["overall_accuracy"] == pytest.approx(1 / 3), name
        assert stats["total_profit"] == pytest.approx(_FALLBACK_WIN - 10.0), name

    # Best overall: the accuracy tie (home_advantage vs matchup, both
    # 2/3) resolves to the canonical reduced-set order.
    assert past["best_overall"]["heuristic"] == HEURISTIC_HOME_ADVANTAGE
    assert past["best_overall"]["accuracy"] == pytest.approx(2 / 3)
