"""Unit tests for alembic migration files (HI-006).

These tests pin the static structure of the migration tree (revision
IDs, down-revision chains, presence of the expected upgrade SQL).
Full up/down round-trip testing against a live Postgres lives in
the integration suite; here we only check what we can verify
without a database.

Why bother? The chain of ``down_revision`` values is easy to break
inadvertently when adding a new migration, and the cost is a
broken production deploy.
"""
from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _load_module(stem: str):
    """Load a migration module by file stem via importlib.util.

    The ``versions/`` directory is not a Python package (it has no
    ``__init__.py``) so we cannot use ``importlib.import_module``;
    instead we load the source file directly.
    """
    path = MIGRATIONS_DIR / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(f"_alembic_mig_{stem}", path)
    assert spec is not None and spec.loader is not None, (
        f"could not load migration spec from {path}"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_source(stem: str, attr: str) -> str:
    """Return the source code of ``module.<attr>`` as a string."""
    mod = _load_module(stem)
    return inspect.getsource(getattr(mod, attr))


def _all_migrations() -> dict[str, str | None]:
    """Return a ``{revision: down_revision}`` map for every migration."""
    revisions: dict[str, str | None] = {}
    for path in MIGRATIONS_DIR.glob("*.py"):
        if path.name.startswith("__"):
            continue
        mod = _load_module(path.stem)
        revisions[mod.revision] = mod.down_revision
    return revisions


def test_migration_0003_registers_composite_index():
    """The HI-006 migration must declare the expected revision ids."""
    mod = _load_module("2026_06_18_0145-0003_job_executions_metrics_index")
    assert mod.revision == "0003_job_executions_metrics_index"
    assert mod.down_revision == "0002_weather_players_injuries"


def test_migration_0003_upgrade_sql_targets_job_executions():
    """The migration SQL must create the composite index on
    ``job_executions``.
    """
    src = _read_source(
        "2026_06_18_0145-0003_job_executions_metrics_index", "upgrade"
    )
    assert "ix_job_executions_job_name_started_at" in src
    assert "job_executions" in src
    # The DESC on started_at is what lets the planner satisfy
    # ``ORDER BY started_at DESC LIMIT 1`` (last-run lookups) via
    # index-only scan.
    assert "started_at DESC" in src or "started_at desc" in src.lower()


def test_migration_0003_downgrade_drops_index():
    """The migration's ``downgrade()`` must drop the index it created."""
    src = _read_source(
        "2026_06_18_0145-0003_job_executions_metrics_index", "downgrade"
    )
    assert "ix_job_executions_job_name_started_at" in src


def test_migration_chain_is_consistent():
    """Every migration's ``down_revision`` must point to a revision
    that exists in the tree.
    """
    revisions = _all_migrations()

    # 0003 must chain off 0002, which chains off 0001.
    assert revisions.get("0003_job_executions_metrics_index") == (
        "0002_weather_players_injuries"
    )
    assert revisions.get("0002_weather_players_injuries") == (
        "0001_consolidated"
    )
    # Root must have a None down_revision.
    assert revisions.get("0001_consolidated") is None


def test_every_down_revision_resolves_to_a_known_revision():
    """For every migration in the tree, ``down_revision`` must be a
    known revision id (or ``None`` for the root).
    """
    revisions = _all_migrations()
    known = set(revisions) | {None}
    for rev, down in revisions.items():
        assert down in known, (
            f"migration {rev} has unknown down_revision {down!r}"
        )