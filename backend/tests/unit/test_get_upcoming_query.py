"""Regression tests for the SQLAlchemy boolean-negation bug in
``GameCRUD.get_upcoming`` and ``GameCRUD.get_recently_finished_games``.

Background
----------
Both methods build a ``.where(...)`` clause to exclude completed games.
They used ``not Game.completed`` (Python's ``not``), which evaluates to the
plain Python ``bool`` ``False`` at expression-build time because
``Game.completed`` is a truthy ``InstrumentedAttribute`` object.  SQLAlchemy
then compiles ``.where(False)`` to ``WHERE false`` — a clause that matches
*no* rows — so:

* ``GET /api/games`` (the default branch → ``get_upcoming``) returned
  ``{"count": 0}`` even when upcoming games existed in the database, and
* the match-completion detector (``get_recently_finished_games``) could
  never find any candidate games to mark complete.

The fix is SQLAlchemy's bitwise-NOT operator ``~Game.completed``, which
compiles to ``WHERE NOT games.completed`` — the same operator the working
``GET /api/games?latest=true`` path already uses in ``app.api.games``.

These tests compile each query to SQL and assert the rendered WHERE clause
selects real games rather than the always-false literal.  They run fully
in-memory (no Postgres / Podman) by mocking the session's ``execute`` call
and inspecting the statement it was handed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.cache import short_cache
from packages.shared.crud.games import GameCRUD


def _compile(stmt) -> str:
    """Compile a SQLAlchemy statement to literal-bound Postgres SQL."""
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _capturing_session() -> AsyncMock:
    """Return a mock ``AsyncSession`` that records every executed statement.

    ``execute`` is an ``AsyncMock`` returning a ``MagicMock`` shaped like a
    SQLAlchemy ``Result`` (``.scalars().all()`` → ``[]``).  Each statement
    handed to it is recoverable via ``db.execute.await_args_list``.
    """
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(return_value=result)
    return db


class TestGetUpcomingNotCompletedQuery:
    """``get_upcoming`` must filter on ``NOT games.completed``."""

    @pytest.mark.asyncio
    async def test_where_clause_is_not_always_false(self):
        db = _capturing_session()

        # Bypass the Redis cache so the real SQL is built and executed.
        with patch.object(short_cache, "get", AsyncMock(return_value=None)), \
                patch.object(short_cache, "set", AsyncMock(return_value=None)):
            await GameCRUD.get_upcoming(db, limit=50)

        assert db.execute.await_args_list, (
            "get_upcoming did not execute any statement"
        )
        sql = _compile(db.execute.await_args_list[-1].args[0]).lower()

        # The bug compiled to ``WHERE false`` (matches no rows).
        assert "where false" not in sql, sql
        # The fix compiles to a real negation of the completed column.
        assert "not games.completed" in sql, sql


class TestGetRecentlyFinishedGamesNotCompletedQuery:
    """``get_recently_finished_games`` must also negate ``completed`` correctly.

    This is the query that backs the match-completion detector cron job.
    """

    @pytest.mark.asyncio
    async def test_where_clause_is_not_always_false(self):
        db = _capturing_session()
        await GameCRUD.get_recently_finished_games(db, buffer_minutes=60)

        assert db.execute.await_args_list, (
            "get_recently_finished_games did not execute any statement"
        )
        sql = _compile(db.execute.await_args_list[-1].args[0]).lower()

        assert "where false" not in sql, sql
        assert "not games.completed" in sql, sql
