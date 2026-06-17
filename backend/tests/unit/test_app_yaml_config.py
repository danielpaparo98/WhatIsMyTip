"""Contract tests for `.do/app.yaml` — the DigitalOcean App Platform spec.

These tests lock in the **FastAPI architecture shape** of the spec so a
future regression to the FaaS/OpenWhisk shape (e.g. re-introducing a
``functions:`` block, dropping the api service, or pointing the spec
at a stale branch) is caught at unit-test time rather than at deploy
time on App Platform.

The bug this guards against (regression case): commit history shows
``.do/app.yaml`` was last updated for the OpenWhisk/FaaS architecture,
declaring a ``functions:`` block that pointed at ``backend/project.yml``
and a `whatismytip-proxy` nginx service.  The dev branch carries a
FastAPI reimplementation under ``backend/app/`` + ``backend/main.py``
with a multi-stage Dockerfile in ``backend/Dockerfile`` (port 8000).
The spec MUST match the FastAPI shape so a `push: main` triggers a
valid container deploy rather than trying to load a FastAPI app as an
OpenWhisk action.

The tests deliberately use ``re`` (no PyYAML) so they run in the
lightweight unit-test environment that doesn't pull in dev-only
parsing dependencies.  They assert the key invariants the spec must
satisfy, not the exact line-for-line content.

Invariants pinned
-----------------
1. No top-level ``functions:`` block (the OpenWhisk/FaaS shape).
2. Exactly one ``services:`` block is declared.
3. The service is named ``whatismytip-api``.
4. The service declares ``http_port: 8000`` (FastAPI default).
5. The service has a health check at ``/health``.
6. The service ``source_dir`` is ``backend``.
7. The service ``dockerfile_path`` is ``Dockerfile`` (relative to
   ``source_dir``).
8. The service has ``deploy_on_push: true`` so pushes to ``main``
   automatically redeploy the new container.
9. No nginx proxy service is declared (FastAPI serves ``/api/...``
   directly, no path-rewrite hop needed).
10. The app name, region, and ``whatismytip.com`` domain are
    preserved so existing DNS and certs keep working.
11. The deploy branch is ``main`` (so the deploy job in
    ``.github/workflows/deploy.yml`` triggers on the new spec).
12. The repo is ``danielpaparo98/WhatIsMyTip``.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Make ``backend`` importable so the test runs from the repo root or
# from ``backend/`` (mirrors the pattern used in the other unit
# tests that need to resolve repo-root-relative paths).
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# Path to the spec, relative to the repo root.
SPEC_PATH = _REPO_ROOT / ".do" / "app.yaml"


def _load_spec() -> str:
    """Read ``.do/app.yaml`` as text.

    A small helper so each test gets a fresh read and a clear error if
    the file is missing (rather than a confusing ``None`` slice).
    """
    if not SPEC_PATH.exists():
        pytest_skip = f".do/app.yaml not found at {SPEC_PATH}"
        raise FileNotFoundError(pytest_skip)
    return SPEC_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Top-level shape: services, no functions
# ---------------------------------------------------------------------------


class TestAppYamlTopLevelShape:
    """``.do/app.yaml`` must declare FastAPI-shaped components, not FaaS."""

    def test_no_functions_block(self):
        """No top-level ``functions:`` block — FaaS/OpenWhisk shape is forbidden.

        Regression guard for the original bug: the FaaS spec declared a
        ``functions:`` block with ``name: whatismytip-backend`` and a
        ``source_dir: backend`` that pointed at ``project.yml``.  The
        FastAPI reimplementation is a single Docker container and must
        be deployed as a ``services:`` component.
        """
        spec = _load_spec()
        # The ``functions:`` key is only forbidden at the top level of
        # the spec (an env var named ``functions`` would be legal, but
        # this YAML format doesn't allow that).  Match a line that
        # starts with ``functions:`` at column 0.
        assert not re.search(r"^functions:\s*$", spec, re.MULTILINE), (
            ".do/app.yaml declares a top-level 'functions:' block — this "
            "is the FaaS/OpenWhisk shape and is forbidden by the FastAPI "
            "migration.  Replace it with a 'services:' block that runs "
            "the FastAPI container on port 8000."
        )

    def test_has_services_block(self):
        """At least one top-level ``services:`` block is declared."""
        spec = _load_spec()
        assert re.search(r"^services:\s*$", spec, re.MULTILINE), (
            ".do/app.yaml must declare a 'services:' block for the "
            "FastAPI container."
        )

    def test_app_name_preserved(self):
        """App name is preserved so the existing DO App Platform app
        ID and DNS records keep working across the migration."""
        spec = _load_spec()
        assert re.search(r"^name:\s*whatismytip\s*$", spec, re.MULTILINE), (
            ".do/app.yaml must keep 'name: whatismytip' so the existing "
            "DO App Platform app is updated in place rather than created "
            "as a new app (which would orphan DNS and TLS certs)."
        )

    def test_region_preserved(self):
        """Region stays as ``syd`` so the managed DB / Redis stay
        on the same private network as the app."""
        spec = _load_spec()
        assert re.search(r"^region:\s*syd\s*$", spec, re.MULTILINE), (
            ".do/app.yaml must keep 'region: syd' so the FastAPI "
            "container stays on the same private VPC as the managed "
            "Postgres and Redis."
        )

    def test_domain_preserved(self):
        """``whatismytip.com`` PRIMARY domain is preserved."""
        spec = _load_spec()
        assert "whatismytip.com" in spec, (
            ".do/app.yaml must keep the 'whatismytip.com' PRIMARY "
            "domain so the public URL stays stable."
        )


# ---------------------------------------------------------------------------
# whatismytip-api service
# ---------------------------------------------------------------------------


class TestWhatismytipApiService:
    """The FastAPI service must be a ``services:`` component."""

    def _service_block(self) -> str:
        """Extract the text of the ``whatismytip-api`` service entry."""
        spec = _load_spec()
        # Find the service entry whose ``name:`` is ``whatismytip-api``.
        # The service entry is a YAML list item under ``services:`` —
        # it starts with ``  - name: whatismytip-api`` and continues
        # until the next sibling list item or the end of the block.
        match = re.search(
            r"-\s+name:\s*whatismytip-api\s*\n(?P<body>(?:[ \t]+.*\n|^\s*\n)+)",
            spec,
            re.MULTILINE,
        )
        assert match is not None, (
            "No service entry with 'name: whatismytip-api' found in "
            ".do/app.yaml.  The FastAPI container must be declared as "
            "a 'services:' component named 'whatismytip-api'."
        )
        return match.group("body")

    def test_service_uses_port_8000(self):
        body = self._service_block()
        assert re.search(r"^\s+http_port:\s*8000\s*$", body, re.MULTILINE), (
            "The api service must declare 'http_port: 8000' — this is "
            "the port uvicorn binds to in backend/Dockerfile:72."
        )

    def test_service_source_dir_is_backend(self):
        body = self._service_block()
        assert re.search(r"^\s+source_dir:\s*backend\s*$", body, re.MULTILINE), (
            "The api service must declare 'source_dir: backend' so the "
            "container is built from the FastAPI source tree."
        )

    def test_service_dockerfile_path_is_Dockerfile(self):
        body = self._service_block()
        # App Platform interprets ``dockerfile_path`` relative to
        # ``source_dir``.  We use the bare ``Dockerfile`` so it picks
        # up ``backend/Dockerfile`` (the multi-stage FastAPI image).
        assert re.search(r"^\s+dockerfile_path:\s*Dockerfile\s*$", body, re.MULTILINE), (
            "The api service must declare 'dockerfile_path: Dockerfile' "
            "(relative to source_dir 'backend').  This builds "
            "'backend/Dockerfile', the multi-stage FastAPI image."
        )

    def test_service_has_health_check_on_health(self):
        body = self._service_block()
        # App Platform expects ``health_check:`` with a ``http_path:``
        # sub-field.  The FastAPI app exposes ``/health`` (see
        # ``backend/app/api/health.py``).
        assert re.search(r"^\s+health_check:\s*$", body, re.MULTILINE), (
            "The api service must declare a 'health_check:' block so "
            "App Platform can probe the FastAPI container."
        )
        # The health check ``http_path:`` must be ``/health``.  The
        # ``http_path:`` line is indented under ``health_check:``,
        # so it appears at 4+ spaces of indent.
        assert re.search(r"^\s{4,}http_path:\s*/health\s*$", body, re.MULTILINE), (
            "The api service's health_check.http_path must be '/health' "
            "to match the FastAPI endpoint exposed by "
            "'backend/app/api/health.py'."
        )

    def test_service_deploys_on_push_to_main(self):
        body = self._service_block()
        # The service-level ``github.deploy_on_push: true`` (nested
        # under ``github:``) ensures a push to ``main`` triggers a
        # new container build automatically.
        assert "deploy_on_push:\n        true" in body or re.search(
            r"^\s+deploy_on_push:\s*true\s*$", body, re.MULTILINE
        ), (
            "The api service must declare 'deploy_on_push: true' so "
            "pushes to 'main' automatically trigger a new container "
            "deploy.  Without it the migration ships as a spec-only "
            "change and the live container keeps running the old FaaS "
            "image."
        )

    def test_service_branch_is_main(self):
        body = self._service_block()
        # The service-level ``github.branch: main`` keeps the deploy
        # in lockstep with the existing CI workflow
        # (``.github/workflows/deploy.yml`` only deploys from main).
        assert re.search(r"^\s+branch:\s*main\s*$", body, re.MULTILINE), (
            "The api service's github.branch must be 'main' so the "
            "existing CI deploy job triggers on the new container."
        )

    def test_service_repo_is_whatismytip(self):
        body = self._service_block()
        assert re.search(
            r"^\s+repo:\s*danielpaparo98/WhatIsMyTip\s*$", body, re.MULTILINE
        ), (
            "The api service's github.repo must be "
            "'danielpaparo98/WhatIsMyTip' so DO App Platform can pull "
            "the source from the correct GitHub repo."
        )


# ---------------------------------------------------------------------------
# Components that must NOT appear (FaaS remnants)
# ---------------------------------------------------------------------------


class TestNoFaaSRemnants:
    """The spec must not declare any FaaS/OpenWhisk components."""

    def test_no_nginx_proxy_service(self):
        """No ``whatismytip-proxy`` *service entry*.

        The FaaS architecture routed every ``/api/...`` request through
        an nginx proxy to handle the OpenWhisk URL-rewrite quirk.
        FastAPI serves ``/api/...`` directly, so the proxy would be
        dead weight (and an extra hop / extra failure mode).

        The assertion matches the YAML list-item pattern
        ``- name: whatismytip-proxy`` (after stripping line comments)
        so doc-comment references to the proxy are not treated as a
        live service declaration.
        """
        spec = _load_spec()
        # Strip line comments (any ``  # ...`` segment) so a doc
        # comment that mentions the proxy is not mistaken for an
        # active service entry.
        uncommented = "\n".join(
            line.split("  #", 1)[0] if "  #" in line else line
            for line in spec.splitlines()
        )
        assert not re.search(
            r"^-\s+name:\s*whatismytip-proxy\s*$",
            uncommented,
            re.MULTILINE,
        ), (
            ".do/app.yaml declares a 'whatismytip-proxy' service "
            "entry.  FastAPI serves /api/... directly; the nginx "
            "proxy was an OpenWhisk/FaaS-only URL-rewrite hack and "
            "must be removed entirely (not just commented out)."
        )

    def test_no_function_namespace_env(self):
        """No ``FUNCTION_NAMESPACE`` env var.

        That env var is OpenWhisk-specific (the FaaS namespace for
        the action).  FastAPI doesn't read it and keeping it would
        mislead a future operator into thinking the deployment is
        FaaS-shaped.
        """
        spec = _load_spec()
        assert "FUNCTION_NAMESPACE" not in spec, (
            ".do/app.yaml must not declare FUNCTION_NAMESPACE — that "
            "env var is an OpenWhisk-only concept.  FastAPI doesn't "
            "read it and it confuses the deploy topology."
        )

    def test_no_doserverless_function_host(self):
        """No ``faas-syd1-*.doserverless.co`` function host.

        That URL is the OpenWhisk gateway.  FastAPI's API service is
        reached over the App Platform private network via the service
        name (``http://whatismytip-api:8000``) or via the public
        ingress — never via the OpenWhisk gateway.
        """
        spec = _load_spec()
        assert "doserverless.co" not in spec, (
            ".do/app.yaml must not reference 'doserverless.co' — that "
            "is the OpenWhisk gateway URL.  FastAPI is reached over "
            "the App Platform private network or the public ingress."
        )


# ---------------------------------------------------------------------------
# Ingress routing (the public-facing wiring)
# ---------------------------------------------------------------------------


class TestIngressRouting:
    """The public ingress must route ``/api`` to the FastAPI service."""

    def test_api_prefix_routes_to_api_service(self):
        spec = _load_spec()
        # App Platform ingress rules are declared either as a top-
        # level ``ingress:`` block (legacy) or as service-level
        # ``routes:`` (newer).  The FastAPI spec uses the latter, so
        # we look for ``/api`` under the api service.
        # We extract the api service block (same regex as above) and
        # look for ``routes:`` containing ``path: /api``.
        match = re.search(
            r"-\s+name:\s*whatismytip-api\s*\n(?P<body>(?:[ \t]+.*\n|^\s*\n)+)",
            spec,
            re.MULTILINE,
        )
        assert match is not None, (
            "Cannot find the 'whatismytip-api' service in .do/app.yaml."
        )
        body = match.group("body")
        assert re.search(r"^\s+routes:\s*$", body, re.MULTILINE), (
            "The api service must declare a 'routes:' block so the "
            "public ingress mounts it at /api."
        )
        # The route's ``path:`` is ``/api`` (the prefix App Platform
        # forwards to the container).  We just need a ``/api`` token
        # under the ``routes:`` block to confirm the wiring.
        assert "/api" in body, (
            "The api service's 'routes:' must include a 'path: /api' "
            "entry so the public ingress forwards /api/... requests "
            "to the FastAPI container."
        )

    def test_root_prefix_routes_to_frontend(self):
        spec = _load_spec()
        # The frontend static site must still be declared so the
        # ``/`` (everything not ``/api``) goes to the Nuxt static
        # site.  We check the static_sites: block name.
        assert re.search(r"^static_sites:\s*$", spec, re.MULTILINE), (
            ".do/app.yaml must declare a 'static_sites:' block for "
            "the Nuxt frontend."
        )
        assert re.search(
            r"-\s+name:\s*whatismytip-frontend\s*$", spec, re.MULTILINE
        ), (
            "The static site must be named 'whatismytip-frontend' so "
            "the public ingress routes '/' to the Nuxt build."
        )
