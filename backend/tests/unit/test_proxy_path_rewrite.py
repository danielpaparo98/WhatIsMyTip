"""
Unit tests for the nginx reverse proxy used on App Platform.

The proxy sits in front of the function component (OpenWhisk gateway)
and translates user-facing `/api/...` URLs into the function
gateway's `/api/api/...` URL form, so the OpenWhisk runtime can
resolve the `<package>/<action>` path correctly.

These tests cover two layers:

1. **Logic** — that the path-rewriting arithmetic we'd see from
   `proxy_pass http://host:port/api/api/` with incoming `/api/...`
   produces the expected upstream URL.

2. **nginx.conf** — that the actual `backend/proxy/nginx.conf` file
   on disk has the required `location /api/` block with a
   `proxy_pass` ending in `/api/api/`, and the right `upstream`
   definition referencing `${FUNCTION_HOST}` / `${FUNCTION_PORT}`.

We intentionally do NOT require a running nginx binary — the goal is
to catch regressions in the config (e.g. someone removes the
trailing slash and silently breaks path rewriting) at unit-test time.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest


# ── Path-rewriting logic ───────────────────────────────────────────────
#
# This is exactly what nginx does when you write:
#
#     location /api/ {
#         proxy_pass http://upstream:8080/api/api/;
#     }
#
# …given an incoming request to `/api/games/health`.  The trailing
# slash on `proxy_pass` tells nginx to REPLACE the matched `/api`
# prefix with `/api/api` and append the rest of the path verbatim.

def rewrite(incoming_path: str, upstream_base: str) -> str:
    """Simulate nginx's `proxy_pass .../api/api/` rewrite of `/api/...`."""
    assert incoming_path == "/api" or incoming_path.startswith("/api/"), (
        f"incoming path must start with /api, got {incoming_path!r}"
    )
    suffix = incoming_path[len("/api"):]   # e.g. "/games/health"
    new_path = f"/api/api{suffix}"         # e.g. "/api/api/games/health"
    base = urlparse(upstream_base)
    return urlunparse((base.scheme, base.netloc, new_path, "", "", ""))


UPSTREAM = "http://whatismytip-backend:8080"


@pytest.mark.parametrize(
    ("incoming", "expected_upstream"),
    [
        ("/api/games/health",
         "http://whatismytip-backend:8080/api/api/games/health"),
        ("/api/games",
         "http://whatismytip-backend:8080/api/api/games"),
        ("/api/games/",
         "http://whatismytip-backend:8080/api/api/games/"),
        ("/api/tips/generate",
         "http://whatismytip-backend:8080/api/api/tips/generate"),
        ("/api/backtest/run",
         "http://whatismytip-backend:8080/api/api/backtest/run"),
        ("/api/admin/daily-sync/trigger",
         "http://whatismytip-backend:8080/api/api/admin/daily-sync/trigger"),
        ("/api/",
         "http://whatismytip-backend:8080/api/api/"),
    ],
)
def test_nginx_proxy_path_rewriting(incoming: str, expected_upstream: str) -> None:
    """User-facing `/api/...` must become `/api/api/...` upstream."""
    assert rewrite(incoming, UPSTREAM) == expected_upstream


# ── nginx.conf on-disk validation ──────────────────────────────────────
#
# The test below reads the actual `backend/proxy/nginx.conf` from the
# repo (or skips with a clear message if it isn't present) and asserts
# the critical directives are present in the right form.  This is the
# single most likely place for a regression: someone deletes the
# trailing slash on `proxy_pass` and the entire API silently 404s.

REPO_ROOT = Path(__file__).resolve().parents[2]   # backend/tests/unit/.../..
NGINX_CONF = REPO_ROOT / "proxy" / "nginx.conf"


def _read_nginx_conf() -> str:
    """Read the on-disk nginx.conf, or skip the test if it's not there."""
    if not NGINX_CONF.is_file():
        pytest.skip(f"nginx.conf not found at {NGINX_CONF}")
    return NGINX_CONF.read_text(encoding="utf-8")


def test_nginx_conf_has_upstream_block() -> None:
    """An `upstream whatismytip_function { … }` block must be defined."""
    conf = _read_nginx_conf()
    assert re.search(
        r"upstream\s+whatismytip_function\s*\{[^}]*server\s+\$\{FUNCTION_HOST\}:\$\{FUNCTION_PORT\}",
        conf,
        re.DOTALL,
    ), (
        "nginx.conf must define an `upstream whatismytip_function` block "
        "with `server ${FUNCTION_HOST}:${FUNCTION_PORT}`."
    )


def test_nginx_conf_location_api_with_trailing_slash_proxy_pass() -> None:
    """
    The `location /api/` block must end in `proxy_pass .../api/api/;`
    (trailing slash required for path-replacement semantics).
    """
    conf = _read_nginx_conf()
    match = re.search(
        r"location\s+/api/\s*\{[^}]*proxy_pass\s+(http://[^\s;]+);",
        conf,
        re.DOTALL,
    )
    assert match, "missing `location /api/` block with `proxy_pass`"
    proxy_pass_value = match.group(1)
    assert proxy_pass_value.endswith("/api/api/"), (
        f"`proxy_pass` for /api/ must end with '/api/api/' "
        f"(trailing slash) to enable path replacement. Got: {proxy_pass_value!r}"
    )


def test_nginx_conf_has_internal_healthcheck() -> None:
    """A self-contained `/healthz` endpoint that does NOT hit the upstream."""
    conf = _read_nginx_conf()
    assert "location = /healthz" in conf, (
        "nginx.conf should expose a /healthz endpoint that does not "
        "require the function gateway to be reachable (for k8s probes)."
    )


def test_nginx_conf_listens_on_port_8080() -> None:
    """App Platform routes HTTP traffic to port 8080 by convention."""
    conf = _read_nginx_conf()
    assert re.search(r"listen\s+8080", conf), (
        "nginx.conf must `listen 8080;` (App Platform service port)."
    )
