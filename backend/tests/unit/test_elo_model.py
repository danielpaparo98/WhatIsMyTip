"""Unit tests for ``packages.shared.models_ml.elo``.

These tests pin the memory-optimisation (S1) that converted the Elo
recompute from "load all completed games into a list" (``.scalars().all()``)
to a server-side streaming iteration (``session.stream(...).scalars()``
with ``stream_results=True`` + ``yield_per``).

The non-negotiable contract:

* The full-table game loads MUST stream (never materialise the whole
  result set) so the recurring daily-sync memory spike disappears on the
  512 MB instance.
* The produced ratings MUST be numerically identical to the previous
  batch approach for the same input — Elo folding is order-dependent, so
  streaming must iterate rows in the same ``date`` order and apply the
  exact same update logic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.shared.models_ml.elo import EloModel


# ---------------------------------------------------------------------------
# Fakes / helpers
# ---------------------------------------------------------------------------


class _FakeGame:
    """Minimal ``Game``-shaped object exposing only the Elo attributes."""

    def __init__(self, home, away, h_score, a_score, date):
        self.home_team = home
        self.away_team = away
        self.home_score = h_score
        self.away_score = a_score
        self.date = date
        self.completed = True


def _make_games() -> list:
    """A small chronological fixture mixing wins and a draw."""
    base = datetime(2025, 3, 1, 12, 0, tzinfo=timezone.utc)
    return [
        _FakeGame("Brisbane", "Collingwood", 90, 70, base),
        # Equal scores -> home does not win (home_score > away_score is False)
        _FakeGame("Collingwood", "Richmond", 80, 80, base.replace(day=2)),
        _FakeGame("Richmond", "Brisbane", 60, 100, base.replace(day=3)),
        _FakeGame("Sydney", "Brisbane", 110, 55, base.replace(day=4)),
    ]


def _teams_in(games) -> set:
    teams: set = set()
    for g in games:
        teams.add(g.home_team)
        teams.add(g.away_team)
    return teams


def _stream_scalars(games):
    """Return an async generator yielding *games* (mimics stream().scalars())."""

    async def _gen():
        for g in games:
            yield g

    return _gen()


def _stub_session_teams_only(session, games):
    """Wire ``session.execute`` to answer the distinct home/away team queries.

    ``_compute_ratings_from_db`` / ``_compute_point_in_time_ratings`` issue two
    ``db.execute(select(Game.<col>).distinct())`` calls before the streamed
    games query; each returns rows shaped like ``("Team",)``.

    A few trailing empty results keep the *pre-fix* code path (which still
    fetched games via ``db.execute``) from raising ``StopIteration`` during
    RED runs — once games are streamed those extra results are simply unused.
    """
    teams = sorted(_teams_in(games))
    rows = [(t,) for t in teams]
    res_home = MagicMock()
    res_home.all.return_value = rows
    res_away = MagicMock()
    res_away.all.return_value = rows
    empty = MagicMock()
    empty.all.return_value = []
    session.execute = AsyncMock(
        side_effect=[res_home, res_away, empty, empty, empty]
    )


def _stub_session_stream(session, games):
    """Wire ``session.stream`` to return an async-iterable of *games*."""
    stream_result = MagicMock()
    stream_result.scalars.return_value = _stream_scalars(games)
    session.stream = AsyncMock(return_value=stream_result)


def _execution_options(stmt) -> dict:
    """Return the execution options attached to a SQLAlchemy statement."""
    return dict(getattr(stmt, "_execution_options", {}) or {})


# ---------------------------------------------------------------------------
# _compute_ratings_from_db
# ---------------------------------------------------------------------------


class TestEloStreamingRecompute:
    @pytest.mark.asyncio
    async def test_compute_ratings_from_db_uses_streaming(self):
        """The completed-games load MUST stream, not materialise via ``.all()``."""
        games = _make_games()
        session = MagicMock()
        _stub_session_teams_only(session, games)
        _stub_session_stream(session, games)

        await EloModel._compute_ratings_from_db(session)

        # The games query goes through session.stream(...) — never a
        # materialised .all() on the games result.
        assert session.stream.await_count == 1
        stmt = session.stream.await_args.args[0]
        opts = _execution_options(stmt)
        assert opts.get("stream_results") is True, (
            "Elo recompute must stream the completed-games query "
            "(execution option stream_results=True) to avoid loading "
            "every ORM row into memory at once."
        )
        yield_per = opts.get("yield_per")
        assert isinstance(yield_per, int) and yield_per > 0, (
            "Elo recompute must set a positive yield_per chunk size so the "
            f"server-side cursor buffers in batches; got {yield_per!r}."
        )

    @pytest.mark.asyncio
    async def test_compute_ratings_from_db_identical_to_batch(self):
        """Streamed ratings MUST equal the batch fold on the same input."""
        games = _make_games()
        session = MagicMock()
        _stub_session_teams_only(session, games)
        _stub_session_stream(session, games)

        # The reference: the pure, order-preserving batch computation.
        teams = sorted(_teams_in(games))
        expected = EloModel._compute_ratings_from_games(
            list(games),
            {t: 1500.0 for t in teams},
            k_factor=EloModel._DEFAULT_K_FACTOR,
            home_advantage=EloModel._DEFAULT_HOME_ADVANTAGE,
        )

        ratings = await EloModel._compute_ratings_from_db(session)

        # Every team present in the reference must be present and equal.
        assert set(ratings) == set(expected)
        for team in expected:
            assert ratings[team] == pytest.approx(expected[team]), (
                f"Streaming changed {team}'s rating: "
                f"streamed={ratings[team]!r} batch={expected[team]!r}"
            )


# ---------------------------------------------------------------------------
# _compute_point_in_time_ratings
# ---------------------------------------------------------------------------


class TestEloPointInTimeStreaming:
    @pytest.mark.asyncio
    async def test_compute_point_in_time_ratings_uses_streaming(self):
        """The historical-games load MUST stream with execution options."""
        games = _make_games()
        # Target game is after every historical game.
        target = _FakeGame(
            "Geelong", "Sydney", 0, 0,
            datetime(2025, 4, 1, 12, 0, tzinfo=timezone.utc),
        )
        session = MagicMock()
        _stub_session_teams_only(session, games)
        _stub_session_stream(session, games)

        model = EloModel()
        await model._compute_point_in_time_ratings(session, target)

        assert session.stream.await_count == 1
        stmt = session.stream.await_args.args[0]
        opts = _execution_options(stmt)
        assert opts.get("stream_results") is True
        assert isinstance(opts.get("yield_per"), int) and opts.get("yield_per") > 0

    @pytest.mark.asyncio
    async def test_compute_point_in_time_ratings_identical_to_batch(self):
        """Streamed point-in-time ratings MUST equal the batch fold."""
        games = _make_games()
        target = _FakeGame(
            "Geelong", "Sydney", 0, 0,
            datetime(2025, 4, 1, 12, 0, tzinfo=timezone.utc),
        )
        session = MagicMock()
        _stub_session_teams_only(session, games)
        _stub_session_stream(session, games)

        teams = sorted(_teams_in(games))
        expected = EloModel._compute_ratings_from_games(
            list(games),
            {t: 1500.0 for t in teams},
            k_factor=EloModel._DEFAULT_K_FACTOR,
            home_advantage=EloModel._DEFAULT_HOME_ADVANTAGE,
        )

        model = EloModel()
        ratings = await model._compute_point_in_time_ratings(session, target)

        assert set(ratings) == set(expected)
        for team in expected:
            assert ratings[team] == pytest.approx(expected[team]), (
                f"Streaming changed {team}'s point-in-time rating: "
                f"streamed={ratings[team]!r} batch={expected[team]!r}"
            )
