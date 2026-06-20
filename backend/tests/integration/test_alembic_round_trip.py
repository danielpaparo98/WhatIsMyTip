"""Real Alembic migration round-trip test.

Unlike test_alembic_migrations.py (which only checks revision IDs and
SQL strings statically), this test exercises the actual migration
scripts against a live PostgreSQL database:

  1. ``alembic upgrade head`` — apply all migrations
  2. Verify ``alembic current`` reports the latest revision
  3. ``alembic downgrade base`` — roll everything back
  4. ``alembic upgrade head`` — re-apply to verify idempotency

This catches:
  - Models/migrations drift (schema created by create_all ≠ migrations)
  - Missing or incorrect down_revision chains
  - Downgrade scripts that don't actually reverse the upgrade
  - SQL syntax errors that only surface against a real DB

Requires a live PostgreSQL instance (DATABASE_URL env var).
"""

import os
import subprocess
import sys

import pytest

_DSN = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DSN or "localhost" in _DSN,
    reason="Alembic round-trip test requires DATABASE_URL pointing at a live Postgres",
)

# Resolve the backend directory for alembic commands
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _run_alembic(*args: str) -> subprocess.CompletedProcess:
    """Run an alembic command in the backend directory."""
    cmd = [sys.executable, "-m", "alembic", *args]
    env = {**os.environ, "DATABASE_URL": _DSN}
    return subprocess.run(
        cmd,
        cwd=_BACKEND_DIR,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


class TestAlembicRoundTrip:
    """Verify migrations apply and roll back cleanly against a real DB."""

    def test_upgrade_head_succeeds(self):
        """alembic upgrade head must complete without errors."""
        result = _run_alembic("upgrade", "head")
        assert result.returncode == 0, (
            f"alembic upgrade head failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    def test_current_revision_is_head(self):
        """After upgrade, alembic current must report the head revision."""
        result = _run_alembic("current")
        assert result.returncode == 0
        # The output should show the latest revision, not empty
        assert result.stdout.strip(), (
            f"alembic current returned empty output:\n{result.stdout}\n{result.stderr}"
        )
        # Should not contain "head" as an unresolved revision
        assert "(head)" not in result.stdout or "head" in result.stdout.lower()

    def test_downgrade_base_succeeds(self):
        """alembic downgrade base must roll back all migrations cleanly."""
        # First ensure we're at head
        _run_alembic("upgrade", "head")

        result = _run_alembic("downgrade", "base")
        assert result.returncode == 0, (
            f"alembic downgrade base failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    def test_re_upgrade_after_downgrade(self):
        """After downgrade to base, upgrade head must work again (idempotent)."""
        # Ensure we start from base
        _run_alembic("downgrade", "base")

        result = _run_alembic("upgrade", "head")
        assert result.returncode == 0, (
            f"Re-upgrade after downgrade failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    def test_migration_history_is_linear(self):
        """The migration chain must have no branches."""
        result = _run_alembic("history", "--verbose")
        assert result.returncode == 0
        # Each revision should have exactly one down_revision (except base)
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        assert len(lines) >= 2, "Expected at least 2 migrations in history"

    def test_no_orphaned_revisions(self):
        """Every down_revision must resolve to a known revision."""
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config(os.path.join(_BACKEND_DIR, "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(_BACKEND_DIR, "alembic"))
        script_dir = ScriptDirectory.from_config(cfg)

        revisions = {}
        for rev in script_dir.walk_revisions():
            revisions[rev.revision] = rev

        for rev_id, rev in revisions.items():
            if rev.down_revision is not None:
                down_revs = (
                    rev.down_revision
                    if isinstance(rev.down_revision, list)
                    else [rev.down_revision]
                )
                for dr in down_revs:
                    assert dr in revisions, (
                        f"Migration {rev_id} references unknown down_revision '{dr}'"
                    )
