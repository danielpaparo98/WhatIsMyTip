"""
Unit tests for the deployment script.

Phase 4 retires the FaaS deploy (``doctl serverless deploy``) in
favour of a container-based deploy: build the FastAPI image, push it
to the DigitalOcean Container Registry, and trigger an App Platform
deployment.

These tests verify the *contract* of ``scripts/deploy.sh``:

1. The script exists and is executable.
2. It uses ``set -euo pipefail`` (fail-fast on any error).
3. It references the new ``backend/Dockerfile`` (NOT the deleted
   ``doctl serverless deploy`` invocation from the FaaS era).
4. It supports a ``--dry-run`` flag that prints the commands without
   executing them.
5. It pushes to the DigitalOcean Container Registry (referenced via
   the ``DO_REGISTRY`` env var).
6. It does NOT mention any of the FaaS-only commands we just deleted
   (``doctl serverless deploy``, ``doctl serverless functions``,
   ``project.yml``).
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]   # backend/tests/unit/.../..
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy.sh"


def _read_deploy_script() -> str:
    if not DEPLOY_SCRIPT.is_file():
        pytest.skip(f"deploy.sh not found at {DEPLOY_SCRIPT}")
    return DEPLOY_SCRIPT.read_text(encoding="utf-8")


# ── File presence + executability ──────────────────────────────────────


def test_deploy_script_exists() -> None:
    """The script must be present at backend/scripts/deploy.sh."""
    assert DEPLOY_SCRIPT.is_file(), (
        f"deploy script missing at {DEPLOY_SCRIPT}"
    )


def test_deploy_script_is_executable() -> None:
    """
    The script must be executable.

    On POSIX systems this means the user-execute bit is set on the
    file.  On Windows the OS doesn't track unix file modes, so we
    additionally check the git index entry — the cross-platform way
    to declare a file executable in a git repo.
    """
    if not DEPLOY_SCRIPT.is_file():
        pytest.skip("deploy.sh not present")
    mode = DEPLOY_SCRIPT.stat().st_mode
    if mode & stat.S_IXUSR:
        return  # OS file mode is good (Linux/macOS dev box)
    # Fall back to the git index — `git update-index --chmod=+x`
    # sets the index mode to 100755 even on Windows, and a fresh
    # `git checkout` on a Linux CI runner will materialize the
    # executable bit from the index.
    import subprocess
    try:
        result = subprocess.run(
            ["git", "ls-files", "--stage", str(DEPLOY_SCRIPT)],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("git not available")
    assert result.stdout.startswith("100755"), (
        f"deploy.sh is not executable.  On Linux/macOS run `chmod +x "
        f"scripts/deploy.sh`; on Windows run `git update-index "
        f"--chmod=+x backend/scripts/deploy.sh` (the git index mode "
        f"is what makes the file executable on a fresh clone)."
    )


# ── Script header ──────────────────────────────────────────────────────


def test_deploy_script_uses_set_euo_pipefail() -> None:
    """The script must fail fast on any error."""
    content = _read_deploy_script()
    # `set -euo pipefail` is the standard hardening.  Some scripts use
    # `set -euxo pipefail` (with -x for tracing) — accept either.
    assert re.search(r"set\s+-[euxo]*\s*pipefail", content), (
        "deploy.sh must use `set -euo pipefail` (or `set -euxo "
        "pipefail`) to fail fast on errors and undefined variables."
    )


# ── Container-based deploy (not FaaS) ──────────────────────────────────


def test_deploy_script_references_new_dockerfile() -> None:
    """The script must build the new ``backend/Dockerfile``."""
    content = _read_deploy_script()
    assert "Dockerfile" in content, (
        "deploy.sh should reference the Dockerfile path (Phase 4 "
        "container-based deploy)."
    )
    # Should mention either the absolute path "backend/Dockerfile" or
    # the relative path "Dockerfile" with a -f flag
    assert re.search(
        r"docker\s+build.*-f.*Dockerfile",
        content,
        re.DOTALL,
    ), (
        "deploy.sh should `docker build -f ...Dockerfile ...` to build "
        "the FastAPI image."
    )


def test_deploy_script_pushes_to_container_registry() -> None:
    """The script must `docker push` to a configurable registry."""
    content = _read_deploy_script()
    assert "docker push" in content, (
        "deploy.sh must push the built image with `docker push` "
        "(Phase 4 container-based deploy)."
    )
    assert "DO_REGISTRY" in content, (
        "deploy.sh should source the registry hostname from the "
        "`DO_REGISTRY` env var (e.g. registry.digitalocean.com/whatismytip)."
    )


def test_deploy_script_does_not_invoke_doctl_serverless() -> None:
    """The script must NOT use the deleted `doctl serverless` commands."""
    content = _read_deploy_script()
    assert "doctl serverless" not in content, (
        "deploy.sh still references `doctl serverless` — this command "
        "is part of the FaaS architecture that Phase 4 retired.  The "
        "container-based deploy uses `doctl apps create-deployment` or "
        "`doctl compute` instead."
    )


def test_deploy_script_does_not_reference_project_yml() -> None:
    """The script must NOT mention the deleted ``project.yml``."""
    content = _read_deploy_script()
    assert "project.yml" not in content, (
        "deploy.sh still references `project.yml` — this file was the "
        "DO Functions project descriptor, deleted in Phase 4."
    )


# ── Dry-run support ────────────────────────────────────────────────────


def test_deploy_script_supports_dry_run() -> None:
    """The script must support a ``--dry-run`` flag."""
    content = _read_deploy_script()
    # The dry-run flag should be parsed early and short-circuit the
    # actual `docker` / `doctl` invocations.
    assert "--dry-run" in content, (
        "deploy.sh must accept a `--dry-run` flag that prints the "
        "commands it would run without executing them."
    )


def test_deploy_script_default_image_tag_uses_git_sha() -> None:
    """
    The default ``IMAGE_TAG`` should fall back to the current git
    short SHA (e.g. ``a1b2c3d``) so each build is uniquely tagged
    without manual intervention.
    """
    content = _read_deploy_script()
    assert "git rev-parse" in content or "git rev-parse --short" in content, (
        "deploy.sh should default IMAGE_TAG to the current git short "
        "SHA via `git rev-parse --short HEAD` so every build is "
        "uniquely tagged."
    )


# ── Migration step ─────────────────────────────────────────────────────


def test_deploy_script_runs_migrations() -> None:
    """The script must run Alembic migrations as part of the deploy."""
    content = _read_deploy_script()
    assert "alembic" in content, (
        "deploy.sh should run `alembic upgrade head` (or equivalent) "
        "so the database schema is in sync with the new code."
    )


# ── Group B infra/devops-review findings (CR-004, HI-004, HI-009,
#    ME-008, ME-009, LO-005, LO-006, LO-012, LO-013) ─────────────────


def test_deploy_script_has_crlf_check() -> None:
    """The script must refuse to run if it has CRLF line endings (ME-008)."""
    content = _read_deploy_script()
    assert "Refusing to run" in content, (
        "deploy.sh must include a CRLF self-check (ME-008)"
    )
    assert "od -An -c" in content or "head -c1" in content, (
        "deploy.sh must inspect its first byte for \\r"
    )


def test_deploy_script_validates_registry_login() -> None:
    """The script must verify doctl can reach the target registry (HI-004)."""
    content = _read_deploy_script()
    assert "doctl registry login" in content, (
        "deploy.sh must call `doctl registry login --registry ...` after sourcing .env"
    )


def test_deploy_script_no_force_rebuild() -> None:
    """The script must NOT pass --force-rebuild (HI-009)."""
    content = _read_deploy_script()
    assert "--force-rebuild" not in content, (
        "deploy.sh should rely on IMAGE_TAG uniqueness, not --force-rebuild "
        "(HI-009 — --force-rebuild bypasses the App Platform build cache)"
    )


def test_deploy_script_captures_previous_deployment_id() -> None:
    """The script must capture the previous deployment ID for rollback reference (CR-004)."""
    content = _read_deploy_script()
    assert "PREVIOUS_DEPLOYMENT_ID" in content or "list-deployments" in content, (
        "deploy.sh must capture the previous deployment ID for the rollback message"
    )
    assert "doctl apps rollback" in content, (
        "deploy.sh must print the rollback command on health-check failure"
    )


def test_deploy_script_health_poll_at_least_5_minutes() -> None:
    """The /health poll window must be ≥ 5 minutes (ME-009)."""
    content = _read_deploy_script()
    # Look for "30" retries × "10s" sleeps OR an explicit "300 s" mention.
    assert ("seq 1 30" in content and "sleep 10" in content) or "300" in content, (
        "deploy.sh must poll /health for at least 5 minutes (ME-009)"
    )


def test_deploy_script_uses_printf_not_echo_e() -> None:
    """The script must use printf, not `echo -e`, for portability (LO-005)."""
    content = _read_deploy_script()
    # Look for the legacy `echo -e` pattern in coloured output lines.
    import re
    bad = re.findall(r'echo\s+-e\b', content)
    assert not bad, (
        f"deploy.sh should use printf instead of `echo -e` (found {len(bad)} "
        "occurrences — LO-005)"
    )


def test_deploy_script_pytest_no_conflicting_flags() -> None:
    """The pytest invocation must not mix -v and -q (LO-006)."""
    content = _read_deploy_script()
    # Find pytest invocations and ensure no line has both -v and -q.
    import re
    for line in content.splitlines():
        if "pytest" in line and re.search(r"\bpytest\b", line):
            has_v = bool(re.search(r"(^|\s)-v(\s|$)", line))
            has_q = bool(re.search(r"(^|\s)-q(\s|$)", line))
            assert not (has_v and has_q), (
                f"deploy.sh pytest invocation mixes -v and -q: {line!r}"
            )


def test_deploy_script_uses_fsSL_for_health_check() -> None:
    """The health-check curl must use -fsSL (LO-012)."""
    content = _read_deploy_script()
    import re
    # Find the line that curls APP_URL/health and assert it uses -fsSL.
    for line in content.splitlines():
        if "APP_URL}/health" in line or "${APP_URL}/health" in line or "${APP_URL}/\\health" in line:
            assert "-fsSL" in line, (
                f"deploy.sh health-check curl must use -fsSL (LO-012): {line!r}"
            )
            break
    else:
        raise AssertionError("could not find health-check curl line in deploy.sh")


def test_deploy_script_has_err_trap_for_cleanup() -> None:
    """The script must trap ERR and clean up dangling images (LO-013)."""
    content = _read_deploy_script()
    assert "trap" in content and "ERR" in content, (
        "deploy.sh must trap ERR and clean up dangling docker images (LO-013)"
    )


# ── Group C infra/devops-review findings (ME-002, ME-015, LO-005,
#    LO-007, LO-008, LO-010, LO-011, LO-014) ─────────────────────────
#
# These tests guard the smaller, lower-severity shell-script /
# env-example / git-config issues raised in the Group C review pass.


# ── test_setup-db.sh ──────────────────────────────────────────────────

SETUP_DB_SCRIPT = REPO_ROOT / "scripts" / "setup-db.sh"


def _read_setup_db_script() -> str:
    if not SETUP_DB_SCRIPT.is_file():
        pytest.skip(f"setup-db.sh not found at {SETUP_DB_SCRIPT}")
    return SETUP_DB_SCRIPT.read_text(encoding="utf-8")


def test_setup_db_uses_pgpassword_env() -> None:
    """The script must use PGPASSWORD env var instead of inline URI (ME-002)."""
    content = _read_setup_db_script()
    assert "PGPASSWORD" in content, (
        "setup-db.sh must use PGPASSWORD env var instead of inline psql URI "
        "so the password never appears in argv (ME-002)"
    )


def test_setup_db_parses_url_with_python() -> None:
    """The script must parse DATABASE_URL with Python's urllib (ME-002)."""
    content = _read_setup_db_script()
    assert "urllib.parse" in content, (
        "setup-db.sh must use Python's urllib.parse to extract DB components "
        "from DATABASE_URL cleanly (ME-002)"
    )


def test_setup_db_does_not_echo_password() -> None:
    """The script must not echo the parsed password to stdout (ME-002)."""
    content = _read_setup_db_script()
    bad = re.findall(r"echo.*\$P(GPASSWORD|ASSWORD)", content)
    assert not bad, (
        "setup-db.sh must not echo the DB password to stdout "
        f"(found {len(bad)} occurrences — ME-002)"
    )


# ── test_run-migrations.sh ────────────────────────────────────────────

RUN_MIGRATIONS_SCRIPT = REPO_ROOT / "scripts" / "run-migrations.sh"


def _read_run_migrations_script() -> str:
    if not RUN_MIGRATIONS_SCRIPT.is_file():
        pytest.skip(f"run-migrations.sh not found at {RUN_MIGRATIONS_SCRIPT}")
    return RUN_MIGRATIONS_SCRIPT.read_text(encoding="utf-8")


def test_run_migrations_validates_database_url() -> None:
    """The script must fail fast with a clear message if DATABASE_URL is unset (LO-014)."""
    content = _read_run_migrations_script()
    assert "DATABASE_URL" in content, (
        "run-migrations.sh must reference DATABASE_URL"
    )
    assert re.search(r":\s*\"\$\{?DATABASE_URL", content), (
        "run-migrations.sh must use the `${VAR:?msg}` syntax to fail fast on "
        "missing DATABASE_URL (LO-014)"
    )


# ── test.sh / test_dockerfile.sh ──────────────────────────────────────

TEST_SCRIPT = REPO_ROOT / "scripts" / "test.sh"


def _read_test_script() -> str:
    if not TEST_SCRIPT.is_file():
        pytest.skip(f"test.sh not found at {TEST_SCRIPT}")
    return TEST_SCRIPT.read_text(encoding="utf-8")


def test_test_script_has_no_faas_label() -> None:
    """The script must not say 'FaaS' (LO-007)."""
    content = _read_test_script()
    assert "FaaS" not in content, (
        "test.sh must not use the legacy 'FaaS backend tests' label "
        "(LO-007 — Phase 4 retired the FaaS architecture)"
    )


def test_test_script_uses_printf() -> None:
    """The script must use printf for coloured output (LO-005)."""
    content = _read_test_script()
    bad = re.findall(r"echo\s+-e\b", content)
    assert not bad, (
        f"test.sh should use printf instead of `echo -e` (found {len(bad)} "
        "occurrences — LO-005)"
    )


TEST_DOCKERFILE_SCRIPT = REPO_ROOT / "scripts" / "test_dockerfile.sh"


def _read_test_dockerfile_script() -> str:
    if not TEST_DOCKERFILE_SCRIPT.is_file():
        pytest.skip(f"test_dockerfile.sh not found at {TEST_DOCKERFILE_SCRIPT}")
    return TEST_DOCKERFILE_SCRIPT.read_text(encoding="utf-8")


def test_test_dockerfile_has_push_flag() -> None:
    """The script must support a --push flag (LO-008)."""
    content = _read_test_dockerfile_script()
    assert "--push" in content, (
        "test_dockerfile.sh must accept a --push flag for CI use (LO-008)"
    )
    # And it must actually tag + push to DO_REGISTRY when --push is set.
    assert "docker push" in content, (
        "test_dockerfile.sh --push must call `docker push` to DO_REGISTRY"
    )


# ── .gitattributes ────────────────────────────────────────────────────

GITATTRIBUTES = REPO_ROOT.parent / ".gitattributes"


def test_gitattributes_has_dockerfile_pattern() -> None:
    """The gitattributes must cover *.Dockerfile (LO-010)."""
    if not GITATTRIBUTES.is_file():
        pytest.skip(".gitattributes not present")
    content = GITATTRIBUTES.read_text(encoding="utf-8")
    assert "*.Dockerfile" in content, (
        ".gitattributes must enforce LF for *.Dockerfile (LO-010)"
    )


# ── .gitignore ────────────────────────────────────────────────────────

GITIGNORE = REPO_ROOT.parent / ".gitignore"


def test_gitignore_no_redundant_env_patterns() -> None:
    """The .gitignore must not have redundant `*.env` (LO-011)."""
    if not GITIGNORE.is_file():
        pytest.skip(".gitignore not present")
    content = GITIGNORE.read_text(encoding="utf-8")
    lines = [l.strip() for l in content.splitlines()]
    assert "*.env" not in lines, (
        ".gitignore must not have a standalone `*.env` line — `.env` and "
        "`.env.*` with `!.env.example` is sufficient (LO-011)"
    )


# ── env examples ──────────────────────────────────────────────────────

BACKEND_ENV_EXAMPLE = REPO_ROOT / ".env.example"
FRONTEND_ENV_EXAMPLE = REPO_ROOT.parent / "frontend" / ".env.example"


def test_backend_env_example_documents_cron_vars() -> None:
    """backend/.env.example must document the cron variables (ME-015)."""
    if not BACKEND_ENV_EXAMPLE.is_file():
        pytest.skip("backend/.env.example not present")
    content = BACKEND_ENV_EXAMPLE.read_text(encoding="utf-8")
    for var in (
        "DATABASE_URL",
        "REDIS_URL",
        "ADMIN_API_KEY",
        "CORS_ORIGINS",
        "LOG_FORMAT",
        "CRON_ENABLED",
        "CRON_TIMEZONE",
        "RATE_LIMIT_PER_MINUTE",
        "MAX_REQUEST_BODY_BYTES",
    ):
        assert var in content, (
            f"backend/.env.example must document {var} (ME-015)"
        )


def test_frontend_env_example_documents_api_base() -> None:
    """frontend/.env.example must document NUXT_PUBLIC_API_BASE (ME-015)."""
    if not FRONTEND_ENV_EXAMPLE.is_file():
        pytest.skip("frontend/.env.example not present")
    content = FRONTEND_ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "NUXT_PUBLIC_API_BASE" in content, (
        "frontend/.env.example must document NUXT_PUBLIC_API_BASE (ME-015)"
    )
