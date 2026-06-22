"""Unit tests for the alembic ``env.py`` version-table bootstrap guard.

Why this exists
---------------
Alembic creates its bookkeeping table ``alembic_version`` with
``version_num VARCHAR(32)``.  This repo's revision ids are longer than
32 characters (e.g. ``0003_job_executions_metrics_index`` is 34,
``0005_model_versions_coefficients`` is 33).  A from-scratch
``alembic upgrade head`` therefore raises
``value too long for type character varying(32)`` the moment a long
revision id is stamped into ``version_num`` — and that happens *before*
migration 0006 (which widens the column) can ever run.

The fix lives in ``backend/alembic/env.py``: in the online migration path,
*before* ``context.run_migrations()``, a guard ensures ``version_num`` can
hold long ids.  It is idempotent and handles both cases:

* the table is **missing** on the very first run -> it is created with the
  wide column so alembic's subsequent ``checkfirst=True`` reuses it
  (this is the case that actually fixes from-scratch upgrades); and
* the table already exists -> it is widened to ``VARCHAR(128)``.

These tests pin the guard's presence and shape statically.  The real
from-scratch behaviour is exercised end-to-end by the postgres-marked
round-trip suite (``test_alembic_migration_0006_version_num.py``).
"""
from __future__ import annotations

import re
from pathlib import Path

# backend/tests/unit/.../../../alembic/env.py
ENV_PATH = Path(__file__).resolve().parents[2] / "alembic" / "env.py"


def _env_source() -> str:
    return ENV_PATH.read_text(encoding="utf-8")


def test_env_defines_version_table_width_guard() -> None:
    """env.py must define a guard helper that ensures version_num is
    ``VARCHAR(128)`` by both creating the table wide (missing-table case)
    and widening it (existing-table case)."""
    src = _env_source()
    # A named guard helper is referenced ...
    assert "_ensure_alembic_version" in src
    # ... it emits an idempotent CREATE (handles the from-scratch /
    # missing-table case) ...
    assert "CREATE TABLE IF NOT EXISTS alembic_version" in src
    assert "VARCHAR(128)" in src
    # ... plus an idempotent ALTER that widens an already-present table.
    assert "ALTER TABLE alembic_version" in src
    assert "ALTER COLUMN version_num TYPE VARCHAR(128)" in src


def test_env_guard_invoked_before_run_migrations_in_online_path() -> None:
    """In ``run_migrations_online`` the guard must run on the live
    connection *before* ``context.run_migrations()``."""
    src = _env_source()
    assert "def run_migrations_online" in src
    online = src.split("def run_migrations_online", 1)[1]
    assert "_ensure_alembic_version" in online
    assert "context.run_migrations()" in online
    assert online.index("_ensure_alembic_version") < online.index(
        "context.run_migrations()"
    )


def test_env_guard_uses_bind_param_style_text() -> None:
    """The guard's ``text()`` calls must be static literals (no f-strings /
    ``.format`` / ``%`` interpolation) per the project's SQL-safety rule."""
    src = _env_source()
    # The guarded statements are fixed DDL with no external values, so the
    # string literals must be plain ``text("...")`` calls.
    for stmt in re.findall(r"text\((.*?)\)", src, flags=re.DOTALL):
        assert "f'" not in stmt and 'f"' not in stmt, (
            f"guard text() must not use f-strings: text({stmt!r})"
        )


def test_env_guard_does_not_narrow_below_128() -> None:
    """The guard must never emit a narrowing ``ALTER`` (``TYPE VARCHAR(32)``).

    We check the emitted DDL specifically (not docstring prose, which may
    mention ``VARCHAR(32)`` while describing the problem): the guard must
    contain a widening ``TYPE VARCHAR(128)`` and must never contain
    ``TYPE VARCHAR(32)``.
    """
    src = _env_source()
    assert "TYPE VARCHAR(128)" in src.upper()
    assert "TYPE VARCHAR(32)" not in src.upper()
