"""Unit tests for ``EloModel._compute_ratings_from_db`` team discovery (P0-4).

Regression: the team-discovery queries used a Python identity check —
``Game.home_team is not None`` — which is always ``True`` for a
SQLAlchemy column, so no ``IS NOT NULL`` ever reached the database.
TBC fixtures (NULL participant names) then leaked into the ratings
table as a ``None`` key.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.models_ml.elo import EloModel


def _result(rows) -> MagicMock:
    r = MagicMock()
    r.all.return_value = rows
    r.scalars.return_value.all.return_value = []
    return r


class TestEloTeamDiscovery:
    @pytest.mark.asyncio
    async def test_queries_use_sql_is_not_null(self):
        """The team-discovery queries must carry ``IS NOT NULL`` into the
        emitted SQL so TBC fixtures (NULL participant names) are excluded
        by the database — a Python ``is not None`` identity check is
        always ``True`` and filters nothing."""
        db = AsyncMock(spec=AsyncSession)
        db.execute = AsyncMock(
            side_effect=[_result([]), _result([]), _result([])]
        )

        ratings = await EloModel._compute_ratings_from_db(db)

        assert ratings == {}
        for call in db.execute.await_args_list[:2]:
            statement = call.args[0]
            compiled = str(statement.compile())
            assert "IS NOT NULL" in compiled, (
                f"Team-discovery query has no IS NOT NULL filter: {compiled}"
            )
