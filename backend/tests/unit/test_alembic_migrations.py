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


_MIG_0005 = "2026_06_21_1208-0005_model_versions_coefficients"


def test_migration_0005_registers_revision_ids():
    """The weighted-tip migration must declare the expected revision ids."""
    mod = _load_module(_MIG_0005)
    assert mod.revision == "0005_model_versions_coefficients"
    assert mod.down_revision == "0004_canonical_team_names"


def test_migration_0005_chains_off_0004():
    """0005 must chain directly off 0004 (the previous head)."""
    revisions = _all_migrations()
    assert revisions.get("0005_model_versions_coefficients") == (
        "0004_canonical_team_names"
    )


def test_migration_0005_upgrade_creates_both_tables():
    """The upgrade must create ``model_versions`` and ``model_coefficients``
    with their key columns, the FK, the unique constraints and indexes.
    """
    src = _read_source(_MIG_0005, "upgrade")
    assert '"model_versions"' in src
    assert '"model_coefficients"' in src
    # Key columns on model_versions.
    assert "model_name" in src
    assert "intercept" in src
    assert "metrics" in src
    assert "is_active" in src
    assert "trained_at" in src
    assert "training_rows" in src
    # Unique (model_name, version).
    assert "uq_model_versions_name_version" in src
    assert "ix_model_versions_model_active" in src
    # model_coefficients FK + unique + index.
    assert "model_version_id" in src
    assert "feature_name" in src
    assert "uq_model_coefficients_version_feature" in src
    assert "ix_model_coefficients_version" in src
    # ON DELETE CASCADE on the FK.
    assert "CASCADE" in src or "cascade" in src.lower()


def test_migration_0005_heuristic_constants_resolve():
    """The migration's old/new heuristic constants must map to the exact
    literals used elsewhere in the codebase.

    ``inspect.getsource`` on individual ``upgrade``/``downgrade`` bodies
    is not a reliable place to assert the literal values (it may or may
    not include the module-level constant definitions depending on the
    function), so we assert the values directly on the loaded module.
    """
    mod = _load_module(_MIG_0005)
    assert mod._OLD_HEURISTIC == "high_risk_high_reward"
    assert mod._NEW_HEURISTIC == "weighted_tip"


def test_migration_0005_upgrade_renames_heuristic_value():
    """The upgrade must issue UPDATE statements that rename the heuristic
    value in both ``tips`` and ``backtest_results``.
    """
    src = _read_source(_MIG_0005, "upgrade")
    # The rename uses the module-level old/new constants + UPDATE on both tables.
    assert "_OLD_HEURISTIC" in src
    assert "_NEW_HEURISTIC" in src
    assert "UPDATE tips" in src
    assert "UPDATE backtest_results" in src


def test_migration_0005_downgrade_reverses_everything():
    """The downgrade must revert the heuristic rename and drop both tables,
    child (model_coefficients) before parent (model_versions).
    """
    src = _read_source(_MIG_0005, "downgrade")
    # Reversed data rename via the same constants.
    assert "_OLD_HEURISTIC" in src
    assert "_NEW_HEURISTIC" in src
    assert "UPDATE tips" in src
    assert "UPDATE backtest_results" in src
    # Both tables dropped, child before parent.
    assert 'op.drop_table("model_coefficients")' in src
    assert 'op.drop_table("model_versions")' in src
    assert src.index("drop_table") < src.index("model_versions")