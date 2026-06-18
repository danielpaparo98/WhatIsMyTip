"""Unit tests for ``JobExecutionCRUD.cleanup_old_executions``.

Regression test for the production bug: the original implementation
ran an N+1 loop (one SELECT for ids, then one SELECT + one ORM
``session.delete()`` per id), which times out under load once
``job_executions`` accumulates a few thousand rows.  The fix is a
single ``DELETE ... WHERE started_at < cutoff`` statement.

These tests use a mocked ``AsyncSession`` so they do NOT require a
running Postgres.  We pin the contract at the level of:

1. Exactly one ``session.execute()`` call (no per-row round-trip).
2. The single statement is a ``delete(JobExecution)`` — not a
   ``select(JobExecution)``.
3. The return value is the ``rowcount`` of the DELETE.
4. The ORM ``session.delete()`` method is NEVER called (the old
   code used it in a loop).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import delete
from sqlalchemy.sql.dml import Delete, Update

from packages.shared.crud.jobs import JobExecution, JobExecutionCRUD


def _make_session(*, rowcount: int = 0) -> MagicMock:
    """Build a mock ``AsyncSession`` that records its ``execute`` calls.

    ``session.execute`` is an ``AsyncMock`` whose return value carries
    a ``rowcount`` attribute (the production code reads ``result.rowcount``).
    """
    session = MagicMock()
    session.execute = AsyncMock()
    session.execute.return_value = MagicMock(rowcount=rowcount)
    session.commit = AsyncMock()
    # The fix should NEVER call session.delete() — that's the bug.
    session.delete = MagicMock()
    return session


class TestCleanupOldExecutionsBulkDelete:
    """The cleanup MUST be a single DELETE statement — no N+1 loops."""

    def test_executes_exactly_one_sql_statement(self):
        """A single ``session.execute()`` call regardless of rowcount."""
        session = _make_session(rowcount=7)
        crud = JobExecutionCRUD(session)

        deleted = asyncio.run(crud.cleanup_old_executions(days_to_keep=30))

        assert session.execute.await_count == 1, (
            f"cleanup_old_executions must execute ONE SQL statement, "
            f"got {session.execute.await_count} (this is the N+1 bug)"
        )
        assert deleted == 7, (
            f"cleanup_old_executions should return the rowcount of the "
            f"DELETE statement; got {deleted!r}"
        )

    def test_statement_is_a_delete_not_a_select(self):
        """The single statement MUST be ``delete(JobExecution)``."""
        session = _make_session(rowcount=0)
        crud = JobExecutionCRUD(session)

        asyncio.run(crud.cleanup_old_executions(days_to_keep=30))

        stmt = session.execute.await_args.args[0]
        # ``delete(...)`` returns a ``Delete`` instance; we assert on
        # the type rather than comparing against the ``delete``
        # function (which Pylance flags as a misuse of ``isinstance``).
        assert isinstance(stmt, Delete), (
            f"cleanup_old_executions must issue a `delete(JobExecution)` "
            f"statement, got {type(stmt).__name__} (this is the SELECT-instead-of-DELETE bug)"
        )
        # And it must NOT be an UPDATE either.
        assert not isinstance(stmt, Update), (
            "cleanup_old_executions must issue a DELETE, not an UPDATE"
        )

    def test_where_clause_filters_on_started_at(self):
        """The DELETE must filter rows older than the cutoff."""
        session = _make_session(rowcount=0)
        crud = JobExecutionCRUD(session)

        asyncio.run(crud.cleanup_old_executions(days_to_keep=14))

        stmt = session.execute.await_args.args[0]
        rendered = str(stmt.whereclause.compile(compile_kwargs={"literal_binds": True}))
        assert "started_at" in rendered, (
            f"cleanup_old_executions should filter on `started_at`; "
            f"WHERE clause was: {rendered!r}"
        )
        assert "<" in rendered, (
            f"cleanup_old_executions should use a strict-less-than "
            f"comparison; WHERE clause was: {rendered!r}"
        )

    def test_does_not_call_orm_session_delete(self):
        """Regression: the old code called ``session.delete(row)`` in a loop.

        After the fix, no row is loaded into the ORM session; the DELETE
        is a single Core statement.  ``session.delete`` MUST NOT be
        called.
        """
        session = _make_session(rowcount=42)
        crud = JobExecutionCRUD(session)

        asyncio.run(crud.cleanup_old_executions(days_to_keep=30))

        session.delete.assert_not_called()

    def test_n_plus_1_does_not_scale_with_rowcount(self):
        """With 1000 rows matching, ``session.execute`` is still called once.

        This is the test that pins the production fix: the old
        implementation would have called ``session.execute`` (and
        ``session.delete``) ~1000 times.
        """
        session = _make_session(rowcount=1000)
        crud = JobExecutionCRUD(session)

        deleted = asyncio.run(crud.cleanup_old_executions(days_to_keep=30))

        assert session.execute.await_count == 1, (
            f"1000 matching rows must result in ONE execute() call, "
            f"got {session.execute.await_count} (N+1 regression)"
        )
        assert session.delete.call_count == 0
        assert deleted == 1000

    def test_commits_after_delete(self):
        """The cleanup MUST commit so the rows are actually freed."""
        session = _make_session(rowcount=5)
        crud = JobExecutionCRUD(session)

        asyncio.run(crud.cleanup_old_executions(days_to_keep=30))

        session.commit.assert_awaited_once()

    def test_default_days_to_keep(self):
        """The default ``days_to_keep`` is 30 (matches the production cadence)."""
        session = _make_session(rowcount=0)
        crud = JobExecutionCRUD(session)

        asyncio.run(crud.cleanup_old_executions())  # no kwarg

        # We just need the call to succeed and the DELETE to fire.
        assert session.execute.await_count == 1
        assert isinstance(session.execute.await_args.args[0], Delete)
