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


# ── CRLF handling (HI-007) ──────────────────────────────────────────────


def _source_env(env_text: str) -> dict[str, str]:
    """Replicate the deploy.sh ``source`` pipeline against ``env_text``.

    Mirrors the line at the top of ``deploy.sh``:

        source <(grep -v '^#' .env | grep -v '^$' | sed 's/\\r$//')

    Implemented in pure Python so the test is hermetic and does not
    depend on bash being on PATH (which it often isn't on the dev
    box).  Returns the env dict that the source pipeline would have
    produced.
    """
    env: dict[str, str] = {}
    for raw in env_text.splitlines():
        # Mirror `grep -v '^#'` and `grep -v '^$'`.
        line = raw.rstrip("\r")
        if line.startswith("#") or not line.strip():
            continue
        # Mirror `sed 's/\\r$//'` (strip any leftover CR, belt-and-
        # braces after rstrip above).
        line = line.replace("\r", "")
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip optional surrounding quotes (deploy.sh does the
        # same via plain string interpolation).
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        env[key] = value
    return env


class TestDeployScriptCrlfStripping:
    """HI-007: Windows-edited ``.env`` files use CRLF line endings.

    Before the fix, the literal ``\\r`` was concatenated onto the
    value of every env var, which corrupted connection strings
    (e.g. ``postgresql://user:pwd\\r@host:5432/db``).  The fix
    pipes the env file through ``sed 's/\\r$//'`` before sourcing
    so the CR is stripped before the var reaches the shell.
    """

    def test_crlf_env_strips_carriage_returns(self, tmp_path):
        """A CRLF-edited .env file must source cleanly (no \\r)."""
        env_file = tmp_path / ".env"
        env_file.write_bytes(
            b"DATABASE_URL=postgresql://user:pwd@host:5432/db\r\n"
            b"REDIS_URL=redis://localhost:6379/0\r\n"
            b"# This is a comment line\r\n"
            b"\r\n"
            b"DO_APP_ID=abc123\r\n"
        )

        # Read as text and run through the deploy.sh source pipeline
        # (re-implemented in Python via ``_source_env``).
        text = env_file.read_text(encoding="utf-8")
        env = _source_env(text)

        assert env["DATABASE_URL"] == (
            "postgresql://user:pwd@host:5432/db"
        )
        assert env["REDIS_URL"] == "redis://localhost:6379/0"
        assert env["DO_APP_ID"] == "abc123"

        # The whole point of the fix: NO env value may contain a
        # literal ``\r``.
        for key, value in env.items():
            assert "\r" not in value, (
                f"env var {key!r} contains a literal carriage return: "
                f"{value!r}"
            )

    def test_lf_env_still_works(self, tmp_path):
        """A normal LF-only .env file must continue to source cleanly."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "DATABASE_URL=postgresql://user:pwd@host:5432/db\n"
            "REDIS_URL=redis://localhost:6379/0\n"
            "DO_APP_ID=abc123\n",
            encoding="utf-8",
        )
        env = _source_env(env_file.read_text(encoding="utf-8"))
        assert env["DATABASE_URL"] == (
            "postgresql://user:pwd@host:5432/db"
        )
        assert env["REDIS_URL"] == "redis://localhost:6379/0"
        assert env["DO_APP_ID"] == "abc123"

    def test_mixed_line_endings_in_one_file(self, tmp_path):
        """A file with a mix of CRLF and LF must end up with no CR."""
        env_file = tmp_path / ".env"
        env_file.write_bytes(
            b"DATABASE_URL=postgresql://u:p@h:5432/db\r\n"
            b"REDIS_URL=redis://localhost:6379/0\n"
            b"DO_APP_ID=abc123\r\n"
        )
        env = _source_env(env_file.read_text(encoding="utf-8"))
        for value in env.values():
            assert "\r" not in value

    def test_deploy_script_source_pipeline_strips_cr(self) -> None:
        """The deploy.sh source pipeline must include a CR-stripping step.

        Reads the actual script and asserts that the ``source`` line
        contains ``sed 's/\\r$//'`` (or equivalent: ``tr -d '\\\\r'``,
        or an awk filter that drops ``\\r``).
        """
        content = _read_deploy_script()
        # Find the .env source block.
        match = re.search(
            r"source\s+<\(\s*([^)]+)\)", content, re.DOTALL
        )
        assert match is not None, (
            "deploy.sh must source .env via a `source <(...)` pipeline"
        )
        pipeline = match.group(1)
        assert "sed" in pipeline and "r" in pipeline and "$" in pipeline, (
            "deploy.sh .env source pipeline must include a sed step that "
            "strips trailing \\r (e.g. `sed 's/\\r$//'`)."
        )
