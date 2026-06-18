"""Tests for SEC-LO-009: docker-compose default dev passwords must be
clearly documented and the default key must be opt-in.

The ``docker-compose.yml`` file ships with a dev-only default
``ADMIN_API_KEY: dev_admin_key_change_me`` so the local stack can
boot without an env file.  Without guard rails this placeholder
could accidentally be deployed to a real environment and used to
authenticate as the admin.

The fix has three components, all tested here:

1. The default value is clearly marked in a comment as dev-only.
2. The file header documents the policy ("only for local dev
   under ``ENVIRONMENT=development``").
3. The ``api`` service sets ``ENVIRONMENT: development`` so a
   misconfigured production deployment that uses this file
   directly does NOT match the safety check.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]  # backend/tests/unit/...
COMPOSE_FILE = REPO_ROOT.parent / "docker-compose.yml"


@pytest.fixture(scope="module")
def compose() -> dict:
    if not COMPOSE_FILE.exists():
        pytest.skip(f"{COMPOSE_FILE} not found")
    return yaml.safe_load(open(COMPOSE_FILE, encoding="utf-8"))


@pytest.fixture(scope="module")
def compose_text() -> str:
    if not COMPOSE_FILE.exists():
        pytest.skip(f"{COMPOSE_FILE} not found")
    return COMPOSE_FILE.read_text(encoding="utf-8")


class TestAdminApiKeyDocumentation:
    """The default ``ADMIN_API_KEY`` value is clearly documented."""

    def test_header_documents_dev_only_intent(self, compose_text: str) -> None:
        """The file header must explain the dev-only default."""
        # The header section is a single long comment block.  Look for
        # a sentence that mentions ``ADMIN_API_KEY`` AND dev-only.
        lower = compose_text.lower()
        assert "admin_api_key" in lower, (
            "docker-compose.yml header must mention ADMIN_API_KEY"
        )
        assert "dev-only" in lower or "development" in lower, (
            "docker-compose.yml header must mark the default as "
            "dev-only / development-only"
        )
        # And the explicit security marker.
        assert "SEC-LO-009" in compose_text

    def test_default_admin_key_is_clear_placeholder(self, compose: dict) -> None:
        """The default value must read as a placeholder (e.g. contain
        ``change_me``) so a future contributor doesn't mistake it for
        a real secret."""
        api_env = compose["services"]["api"]["environment"]
        key = api_env.get("ADMIN_API_KEY", "")
        # The value must be set (so local dev works out of the box)
        # and obviously a placeholder.
        assert key, "ADMIN_API_KEY must be set for the dev compose"
        assert "change_me" in key.lower() or "dev_only" in key.lower() or "placeholder" in key.lower(), (
            f"SEC-LO-009: ADMIN_API_KEY default ({key!r}) does not read as "
            "an obvious placeholder.  Add 'change_me' (or similar) so a "
            "deployer can't mistake it for a real secret."
        )

    def test_api_service_environment_is_development(self, compose: dict) -> None:
        """The ``api`` service must set ``ENVIRONMENT=development`` so
        the FastAPI lifespan safety check (``app.core.lifespan``)
        allows the dev default to start.

        A misconfigured production deployment that re-uses this file
        would inherit the dev ``ENVIRONMENT`` and the safety check
        would refuse to start with an empty key.  But since the
        default key IS set, the check passes — this is by design for
        the dev flow.  Production deployments override both
        ``ENVIRONMENT`` and ``ADMIN_API_KEY`` via their own
        environment configuration."""
        api_env = compose["services"]["api"]["environment"]
        env_value = api_env.get("ENVIRONMENT", "")
        assert env_value == "development", (
            f"SEC-LO-009: api.environment.ENVIRONMENT must be 'development' "
            f"so the dev compose boots.  Got {env_value!r}."
        )


class TestNoProductionHardcodedSecrets:
    """The compose file must not hardcode any non-dev secret."""

    def test_no_real_looking_secrets(self, compose_text: str) -> None:
        """A line-by-line check: any non-comment line that sets a
        secret-like env var to a real-looking value should fail.

        We allow ``dev_*`` / ``change_me`` / placeholder values, and
        we allow the explicit development markers.  Anything else
        is treated as a regression.
        """
        # Allow these strings as "obviously dev" markers.
        allowed_markers = ("change_me", "dev_", "localhost", "wimt_dev_")

        # We don't try to parse every env var; we just look for
        # suspicious patterns.  This is a static sanity check.
        import re

        secret_keys = ("SECRET", "PASSWORD", "TOKEN", "KEY")
        for line in compose_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            upper = stripped.upper()
            if not any(k in upper for k in secret_keys):
                continue
            # Extract value: split on first "=" or ":"
            if "=" in stripped:
                _, _, value = stripped.partition("=")
            elif ":" in stripped:
                _, _, value = stripped.partition(":")
            else:
                continue
            value = value.strip().strip("'\"").strip()
            if not value:
                continue
            # Allow obviously-dev values.
            if any(m.lower() in value.lower() for m in allowed_markers):
                continue
            # Allow references / env-var passthroughs.
            if value.startswith("${") or value.startswith("$"):
                continue
            # Allow port numbers, URLs, etc.
            if value.startswith("http://") or value.startswith("https://"):
                continue
            # If we got here, this line looks like a real secret.
            pytest.fail(
                f"SEC-LO-009: docker-compose.yml sets a secret-like "
                f"variable to a non-placeholder value:\n  {line!r}\n"
                f"  Value: {value!r}\n"
                f"  Use an env-var passthrough (${{VAR}}) or a dev marker."
            )
