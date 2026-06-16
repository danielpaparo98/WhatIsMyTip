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
