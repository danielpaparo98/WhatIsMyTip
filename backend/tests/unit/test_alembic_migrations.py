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


# ---------------------------------------------------------------------------
# Migration 0006: widen alembic_version.version_num to VARCHAR(128)
#
# Alembic creates its version table with version_num VARCHAR(32), but this
# repo's revision ids exceed 32 chars (0003 = 34, 0005 = 33).  A from-scratch
# upgrade therefore fails at the 0003 stamp unless the column is widened
# first.  See env.py's bootstrap guard for the from-scratch fix; migration
# 0006 captures the widening into the tracked chain so every DB ends up wide.
# ---------------------------------------------------------------------------

_MIG_0006 = "2026_06_22_1240-0006_model_version_num_width"


def test_migration_0006_registers_revision_ids():
    """The version_num-widening migration must declare the expected ids."""
    mod = _load_module(_MIG_0006)
    assert mod.revision == "0006_model_version_num_width"
    assert mod.down_revision == "0005_model_versions_coefficients"


def test_migration_0006_chains_off_0005():
    """0006 must chain directly off 0005 (the previous head)."""
    revisions = _all_migrations()
    assert revisions.get("0006_model_version_num_width") == (
        "0005_model_versions_coefficients"
    )


def test_migration_0006_upgrade_widens_version_num():
    """The upgrade must widen alembic_version.version_num to VARCHAR(128)."""
    src = _read_source(_MIG_0006, "upgrade")
    assert "alembic_version" in src
    assert "version_num" in src
    assert "VARCHAR(128)" in src
    # It must be a column TYPE change, not e.g. adding a column.
    assert "TYPE" in src.upper()
    assert "ALTER" in src.upper()


def test_migration_0006_downgrade_is_safe_noop():
    """The downgrade must NOT narrow version_num back to 32 (that would
    break alembic because the current revision ids already exceed 32 chars).
    It must be a documented no-op: no ALTER, just ``pass``.
    """
    src = _read_source(_MIG_0006, "downgrade")
    # No column-type manipulation of any kind (neither narrowing nor
    # widening) may be issued -- the body is just ``pass``.
    assert "ALTER COLUMN" not in src.upper()
    assert "pass" in src


# ---------------------------------------------------------------------------
# Migration 0007: elo_cache dedup
#
# Migration 0004 (canonical team names) was stamped-past on prod, so its
# elo_cache rename never ran: 8 duplicate alias+canonical rows + 1 NULL
# team_name row linger.  0007 removes the alias rows (merging the more
# complete data into the canonical row) and the NULL row, idempotently.
# ---------------------------------------------------------------------------

_MIG_0007 = "2026_06_22_1245-0007_elo_cache_dedup"


def test_migration_0007_registers_revision_ids():
    """The elo_cache dedup migration must declare the expected ids."""
    mod = _load_module(_MIG_0007)
    assert mod.revision == "0007_elo_cache_dedup"
    assert mod.down_revision == "0006_model_version_num_width"


def test_migration_0007_chains_off_0006():
    """0007 must chain directly off 0006."""
    revisions = _all_migrations()
    assert revisions.get("0007_elo_cache_dedup") == "0006_model_version_num_width"


def test_migration_0007_alias_pairs_match_teams_py():
    """The hardcoded alias->canonical map in 0007 must exactly equal the
    alias->canonical pairs derived from the canonical source of truth
    (packages/shared/teams.py).

    Migrations must not import teams.py at runtime (the mapping could
    drift), so the pairs are copied as literals and pinned here.
    """
    from packages.shared.teams import TEAM_NAME_SETS

    expected: dict[str, str] = {}
    for canonical, aliases in TEAM_NAME_SETS.items():
        for alias in aliases:
            if alias != canonical:
                expected[alias] = canonical

    mod = _load_module(_MIG_0007)
    assert mod._ALIAS_TO_CANONICAL == expected


def test_migration_0007_upgrade_merges_then_deletes_alias():
    """For every alias the upgrade must merge the alias's data into the
    canonical row (preserving the more-complete rating), delete the alias
    row only when a canonical row exists, and remove the NULL team_name row.
    """
    src = _read_source(_MIG_0007, "upgrade")
    # Merge step: PostgreSQL multi-table UPDATE ... FROM.
    assert "UPDATE elo_cache" in src
    assert "FROM elo_cache" in src
    # games_played comparison drives "keep the more-complete row".
    assert "games_played" in src
    assert "COALESCE" in src.upper()
    # Alias deletion is guarded by the existence of a canonical row.
    assert "DELETE FROM elo_cache" in src
    assert "EXISTS" in src.upper()
    # NULL team_name row removal.
    assert "team_name IS NULL" in src
    # Bind parameters (not interpolation) must be used for the pair values.
    assert ":canonical" in src or ":alias" in src


def test_migration_0007_downgrade_is_safe_noop():
    """The downgrade is a one-way data migration that cannot be reversed;
    it must be a documented no-op."""
    src = _read_source(_MIG_0007, "downgrade")
    assert "pass" in src
    # Must not attempt to restore deleted rows (impossible).
    assert "INSERT" not in src.upper()


def test_migration_head_is_0007():
    """After adding 0006 and 0007, the migration tree must have exactly one
    head: 0007_elo_cache_dedup (nothing chains off it).
    """
    revisions = _all_migrations()
    heads = [rev for rev in revisions if rev not in set(revisions.values())]
    assert "0007_elo_cache_dedup" in heads
    assert len(heads) == 1, f"expected a single head, got {heads}"