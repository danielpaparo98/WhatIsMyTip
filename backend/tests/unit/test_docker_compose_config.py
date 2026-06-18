"""Tests for the local Docker Compose configuration.

These are pure-Python unit tests that do NOT require a running container
runtime.  They validate the structure and content of ``docker-compose.yml``
at the project root so that the configuration cannot drift unnoticed.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest
import yaml

# Ensure backend is on sys.path (in case this file is run in isolation).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

PROJECT_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"

# ---------------------------------------------------------------------------
# Test the YAML structure
# ---------------------------------------------------------------------------


class TestDockerComposeStructure:
    """Validate the high-level structure of docker-compose.yml."""

    @pytest.fixture(scope="class")
    def compose(self) -> dict:
        if not COMPOSE_FILE.exists():
            pytest.skip(f"{COMPOSE_FILE} not found")
        with open(COMPOSE_FILE, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def test_top_level_name(self, compose):
        assert compose.get("name") == "whatismytip"

    def test_required_services_present(self, compose):
        services = compose.get("services", {})
        assert "postgres" in services
        assert "redis" in services
        assert "api" in services
        assert "init-data" in services
        assert "frontend" in services

    def test_required_volumes_present(self, compose):
        volumes = compose.get("volumes", {}) or {}
        # Two named volumes: postgres + redis.  Bun/nuxt caches are
        # bind-mounted into the host, not declared as named volumes.
        for v in ("wimt_postgres_data", "wimt_redis_data"):
            assert v in volumes, f"Missing named volume: {v}"

    def test_required_networks_present(self, compose):
        """Split-network design (HI-006): backend_net + frontend_net."""
        networks = compose.get("networks", {}) or {}
        assert "backend_net" in networks, (
            "docker-compose.yml must define `backend_net` "
            "(api ↔ db/redis traffic)"
        )
        assert "frontend_net" in networks, (
            "docker-compose.yml must define `frontend_net` "
            "(api ↔ frontend traffic)"
        )


# ---------------------------------------------------------------------------
# Test individual services
# ---------------------------------------------------------------------------


class TestPostgresService:
    @pytest.fixture(scope="class")
    def compose(self) -> dict:
        return yaml.safe_load(open(COMPOSE_FILE, encoding="utf-8"))

    def test_uses_postgres_16(self, compose):
        svc = compose["services"]["postgres"]
        assert svc["image"].startswith("postgres:16")

    def test_has_healthcheck(self, compose):
        svc = compose["services"]["postgres"]
        assert "healthcheck" in svc
        assert "test" in svc["healthcheck"]

    def test_does_not_publish_5432(self, compose):
        """HI-005: postgres must not be reachable from the host by default.

        Use ``docker compose port postgres 5432`` to grab an ephemeral
        host port for ad-hoc debugging instead.
        """
        svc = compose["services"]["postgres"]
        assert "ports" not in svc, (
            "postgres must not publish 5432 to the host; it should only "
            "be reachable via the backend_net network."
        )

    def test_attached_to_backend_net(self, compose):
        svc = compose["services"]["postgres"]
        nets = svc.get("networks", [])
        assert "backend_net" in nets, (
            "postgres must be attached to backend_net"
        )

    def test_has_no_new_privileges(self, compose):
        svc = compose["services"]["postgres"]
        opts = svc.get("security_opt", [])
        assert "no-new-privileges:true" in opts, (
            "postgres must run with no-new-privileges"
        )


class TestRedisService:
    @pytest.fixture(scope="class")
    def compose(self) -> dict:
        return yaml.safe_load(open(COMPOSE_FILE, encoding="utf-8"))

    def test_uses_redis_7(self, compose):
        svc = compose["services"]["redis"]
        assert svc["image"].startswith("redis:7")

    def test_has_healthcheck(self, compose):
        svc = compose["services"]["redis"]
        assert "healthcheck" in svc

    def test_does_not_publish_6379(self, compose):
        """HI-005: redis must not be reachable from the host by default."""
        svc = compose["services"]["redis"]
        assert "ports" not in svc, (
            "redis must not publish 6379 to the host; it should only "
            "be reachable via the backend_net network."
        )

    def test_attached_to_backend_net(self, compose):
        svc = compose["services"]["redis"]
        nets = svc.get("networks", [])
        assert "backend_net" in nets, (
            "redis must be attached to backend_net"
        )


class TestApiService:
    @pytest.fixture(scope="class")
    def compose(self) -> dict:
        return yaml.safe_load(open(COMPOSE_FILE, encoding="utf-8"))

    def test_built_from_backend_dockerfile(self, compose):
        svc = compose["services"]["api"]
        # The api image is built from the Dockerfile in the backend
        # folder.  The compose file uses ``context: backend`` so the
        # dockerfile path is relative to that context (i.e. just
        # ``Dockerfile``).
        assert svc["build"]["context"] == "backend"
        assert svc["build"]["dockerfile"] == "Dockerfile"

    def test_waits_for_init_data_completion(self, compose):
        svc = compose["services"]["api"]
        deps = svc["depends_on"]["init-data"]
        assert deps.get("condition") == "service_completed_successfully"

    def test_exposes_8000(self, compose):
        svc = compose["services"]["api"]
        assert "8000:8000" in svc["ports"]

    def test_database_url_points_at_postgres_service(self, compose):
        svc = compose["services"]["api"]
        env = svc["environment"]
        assert "DATABASE_URL" in env
        assert "@postgres:" in env["DATABASE_URL"]

    def test_redis_url_points_at_redis_service(self, compose):
        svc = compose["services"]["api"]
        env = svc["environment"]
        assert "REDIS_URL" in env
        assert "redis://redis:" in env["REDIS_URL"]

    def test_has_admin_api_key(self, compose):
        svc = compose["services"]["api"]
        assert "ADMIN_API_KEY" in svc["environment"]

    def test_has_cors_origins(self, compose):
        svc = compose["services"]["api"]
        assert "CORS_ORIGINS" in svc["environment"]
        cors = svc["environment"]["CORS_ORIGINS"]
        assert "http://localhost:3000" in cors
        assert "http://localhost:8000" in cors

    def test_api_attached_to_both_networks(self, compose):
        """The api is the bridge between frontend_net and backend_net."""
        svc = compose["services"]["api"]
        nets = svc.get("networks", [])
        assert "backend_net" in nets, "api must be on backend_net"
        assert "frontend_net" in nets, "api must be on frontend_net"

    def test_api_read_only_filesystem(self, compose):
        """HI-008: the api should run with a read-only root fs + tmpfs."""
        svc = compose["services"]["api"]
        assert svc.get("read_only") is True, (
            "api service should run with read_only: true"
        )
        tmpfs = svc.get("tmpfs", [])
        # /tmp is the minimum; the api also needs /app/.cache writable
        # for uv/python to function.  Don't pin the exact list, just
        # assert at least one tmpfs path exists.
        assert len(tmpfs) >= 1, "api service should declare at least one tmpfs"


class TestInitDataService:
    @pytest.fixture(scope="class")
    def compose(self) -> dict:
        return yaml.safe_load(open(COMPOSE_FILE, encoding="utf-8"))

    def test_is_one_shot(self, compose):
        """init-data must exit 0 and not be restarted."""
        svc = compose["services"]["init-data"]
        assert svc.get("restart") == "no"

    def test_waits_for_postgres_health(self, compose):
        svc = compose["services"]["init-data"]
        deps = svc["depends_on"]
        assert deps["postgres"]["condition"] == "service_healthy"
        assert deps["redis"]["condition"] == "service_healthy"

    def test_invokes_migrate_and_seed(self, compose):
        svc = compose["services"]["init-data"]
        cmd = svc["command"]
        # join the list into a single string for substring checks
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        assert "migrate_and_seed.py" in cmd_str
        assert "--from-csv" in cmd_str

    def test_uses_asyncpg_url(self, compose):
        svc = compose["services"]["init-data"]
        assert "asyncpg" in svc["environment"]["DATABASE_URL"]

    def test_supports_init_mode_toggle(self, compose):
        """init-data must respect the WIMT_INIT_MODE env var.

        ``csv`` (default) loads scraped CSVs from /data; ``seed`` runs
        the synthetic offline seeder.  We trigger the latter by
        invoking ``migrate_and_seed.py`` *without* ``--seed`` *and*
        without ``--no-seed`` so the script falls through to
        ``_run_synthetic_seed()`` (which calls ``scripts/seed_data.py``
        under the hood).
        """
        svc = compose["services"]["init-data"]
        cmd = svc["command"]
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        # Both branches of the toggle are present in the shell command.
        assert "WIMT_INIT_MODE" in cmd_str
        assert "--from-csv" in cmd_str
        assert "migrate_and_seed.py" in cmd_str
        # Default fallback is "csv".
        env = svc["environment"]
        assert "WIMT_INIT_MODE" in env
        assert env["WIMT_INIT_MODE"] == "${WIMT_INIT_MODE:-csv}"
        # The seed branch must NOT pass --seed (that would try to load
        # CSVs from the image's seed_data/ which is gitignored / empty)
        # and must NOT pass --no-seed (that would skip the synthetic
        # seeder entirely).
        seed_branch = cmd_str.split("else")[0]
        assert "--seed" not in seed_branch
        assert "--no-seed" not in seed_branch
        csv_branch = cmd_str.split("else")[1]
        assert "--from-csv" in csv_branch
        assert "--no-seed" in csv_branch


class TestFrontendService:
    @pytest.fixture(scope="class")
    def compose(self) -> dict:
        return yaml.safe_load(open(COMPOSE_FILE, encoding="utf-8"))

    def test_uses_bun_image(self, compose):
        svc = compose["services"]["frontend"]
        assert svc["image"].startswith("oven/bun")

    def test_uses_pinned_bun_1_3(self, compose):
        """HI-008: pin the bun version that matches the committed bun.lockb."""
        svc = compose["services"]["frontend"]
        # Accept the 1.3.6 patch pin or any 1.3.x patch level; reject
        # the bare `oven/bun:1` float.
        image = svc["image"]
        assert image.startswith("oven/bun:1.3"), (
            f"frontend must use a pinned oven/bun:1.3.x image, got {image!r}"
        )

    def test_exposes_3000(self, compose):
        svc = compose["services"]["frontend"]
        assert "3000:3000" in svc["ports"]

    def test_api_base_points_at_localhost(self, compose):
        svc = compose["services"]["frontend"]
        env = svc["environment"]
        assert "NUXT_PUBLIC_API_BASE" in env
        assert "http://localhost:8000" in env["NUXT_PUBLIC_API_BASE"]

    def test_frontend_attached_to_frontend_net_only(self, compose):
        """Postgres/redis must NOT be reachable from the frontend."""
        svc = compose["services"]["frontend"]
        nets = svc.get("networks", [])
        assert "frontend_net" in nets, "frontend must be on frontend_net"
        assert "backend_net" not in nets, (
            "frontend must NOT be on backend_net (least-privilege: "
            "frontend has no business talking to postgres/redis directly)"
        )


# ---------------------------------------------------------------------------
# Test smoke script
# ---------------------------------------------------------------------------


class TestSmokeScriptExists:
    """The smoke test script must exist and be executable."""

    def test_smoke_sh_exists(self):
        path = PROJECT_ROOT / "scripts" / "smoke_local.sh"
        assert path.exists(), "scripts/smoke_local.sh is missing"

    def test_smoke_ps1_exists(self):
        path = PROJECT_ROOT / "scripts" / "smoke_local.ps1"
        assert path.exists(), "scripts/smoke_local.ps1 is missing"

    def test_dev_sh_exists(self):
        assert (PROJECT_ROOT / "scripts" / "dev.sh").exists()

    def test_dev_ps1_exists(self):
        assert (PROJECT_ROOT / "scripts" / "dev.ps1").exists()

    def test_smoke_sh_mentions_runtime_detection(self):
        path = PROJECT_ROOT / "scripts" / "smoke_local.sh"
        text = path.read_text(encoding="utf-8")
        assert "docker" in text and "podman" in text

    def test_dev_sh_mentions_runtime_detection(self):
        path = PROJECT_ROOT / "scripts" / "dev.sh"
        text = path.read_text(encoding="utf-8")
        assert "WIMT_RUNTIME" in text
        assert "podman" in text
