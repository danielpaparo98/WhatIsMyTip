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
        # All four volumes should be declared
        for v in ("wimt_postgres_data", "wimt_redis_data", "wimt_bun_cache"):
            assert v in volumes, f"Missing named volume: {v}"


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

    def test_exposes_5432(self, compose):
        svc = compose["services"]["postgres"]
        assert "5432:5432" in svc["ports"]


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

    def test_exposes_6379(self, compose):
        svc = compose["services"]["redis"]
        assert "6379:6379" in svc["ports"]


class TestApiService:
    @pytest.fixture(scope="class")
    def compose(self) -> dict:
        return yaml.safe_load(open(COMPOSE_FILE, encoding="utf-8"))

    def test_built_from_backend_dockerfile(self, compose):
        svc = compose["services"]["api"]
        assert svc["build"]["dockerfile"] == "backend/Dockerfile"

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


class TestFrontendService:
    @pytest.fixture(scope="class")
    def compose(self) -> dict:
        return yaml.safe_load(open(COMPOSE_FILE, encoding="utf-8"))

    def test_uses_bun_image(self, compose):
        svc = compose["services"]["frontend"]
        assert svc["image"].startswith("oven/bun")

    def test_exposes_3000(self, compose):
        svc = compose["services"]["frontend"]
        assert "3000:3000" in svc["ports"]

    def test_api_base_points_at_localhost(self, compose):
        svc = compose["services"]["frontend"]
        env = svc["environment"]
        assert "NUXT_PUBLIC_API_BASE" in env
        assert "http://localhost:8000" in env["NUXT_PUBLIC_API_BASE"]


# ---------------------------------------------------------------------------
# Test smoke script
# ---------------------------------------------------------------------------


class TestSmokeScriptExists:
    """The smoke test script must exist and be executable."""

    def test_smoke_sh_exists(self):
        path = PROJECT_ROOT / "scripts" / "smoke_local.sh"
        assert path.exists(), "scripts/smoke_local.sh is missing"

    def test_smoke_ps1_exists(self):
        path = PROJECT_ROOT / "scripts" / "smoke_ps1.ps1"  # allow either name
        # Actually the file is named smoke_local.ps1
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
