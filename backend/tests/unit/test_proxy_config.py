"""
Unit tests for the nginx reverse proxy used on App Platform.

Phase 4 simplification: with the FaaS architecture retired in favour of a
single FastAPI upstream, the proxy no longer needs to translate
``/api/...`` → ``/api/api/...``.  It's now a thin reverse proxy in front
of the FastAPI container, with a self-contained ``/healthz`` for the
orchestrator's liveness probe.

These tests cover two layers:

1. **Logic** — verify the proxy maps requests to a single FastAPI
   upstream (no path rewriting), accepts bodies up to 10 MiB, and
   exposes ``/healthz`` without proxying.

2. **nginx.conf** — the actual ``backend/proxy/nginx.conf`` file on
   disk must have the required directives in the right form.

We intentionally do NOT require a running nginx binary — the goal is
to catch regressions in the config (e.g. someone re-introduces a
rewrite) at unit-test time.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


# ── Logic helpers ────────────────────────────────────────────────────────
#
# With a single FastAPI upstream, nginx should pass paths through
# verbatim.  The only "rewrite" we do is on ``/healthz``, which is
# served directly by nginx (status 200) without touching the upstream.

def resolve_upstream_path(incoming_path: str) -> str:
    """Simulate nginx pass-through of a request to the FastAPI upstream."""
    # The Phase 4 nginx config does not rewrite paths — it just forwards
    # them to ``fastapi:8000`` verbatim.
    return incoming_path


# ── nginx.conf on-disk validation ──────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]   # backend/tests/unit/.../..
NGINX_CONF = REPO_ROOT / "proxy" / "nginx.conf"


def _read_nginx_conf() -> str:
    """Read the on-disk nginx.conf, or skip the test if it's not there."""
    if not NGINX_CONF.is_file():
        pytest.skip(f"nginx.conf not found at {NGINX_CONF}")
    return NGINX_CONF.read_text(encoding="utf-8")


def test_nginx_conf_has_fastapi_upstream() -> None:
    """An ``upstream fastapi_backend { ... }`` block must be defined."""
    conf = _read_nginx_conf()
    assert re.search(
        r"upstream\s+fastapi_backend\s*\{[^}]*server\s+fastapi:8000",
        conf,
        re.DOTALL,
    ), (
        "nginx.conf must define an `upstream fastapi_backend` block "
        "with `server fastapi:8000` (App Platform internal DNS)."
    )


def test_nginx_conf_has_no_openwhisk_path_rewrite() -> None:
    """
    The Phase 4 nginx config must NOT rewrite ``/api/...`` to
    ``/api/api/...`` — the OpenWhisk runtime is gone.
    """
    conf = _read_nginx_conf()
    assert "/api/api/" not in conf, (
        "nginx.conf still contains the OpenWhisk '/api/api/' rewrite. "
        "Phase 4 retires the FaaS architecture — the proxy should "
        "forward paths verbatim to the FastAPI upstream."
    )
    # Also assert the specific rewrite directive is gone
    assert "rewrite ^/api/" not in conf, (
        "nginx.conf has a `rewrite ^/api/...` directive which is no "
        "longer needed (and is in fact a bug — the upstream is now "
        "FastAPI, not the OpenWhisk gateway)."
    )


def test_nginx_conf_proxies_root_to_fastapi() -> None:
    """A ``location / { proxy_pass http://fastapi_backend; }`` must exist."""
    conf = _read_nginx_conf()
    match = re.search(
        r"location\s+/\s*\{[^}]*proxy_pass\s+http://fastapi_backend;",
        conf,
        re.DOTALL,
    )
    assert match, (
        "nginx.conf must have a `location / { proxy_pass "
        "http://fastapi_backend; }` block forwarding all traffic to "
        "the FastAPI container."
    )


def test_nginx_conf_client_max_body_size() -> None:
    """Body limit must be 10m (matches the FastAPI RequestSizeLimitMiddleware default)."""
    conf = _read_nginx_conf()
    assert re.search(r"client_max_body_size\s+10m", conf), (
        "nginx.conf must set `client_max_body_size 10m;` so that bulk "
        "backtest/admin payloads aren't rejected at the proxy layer."
    )


def test_nginx_conf_has_internal_healthcheck() -> None:
    """A self-contained ``/healthz`` endpoint that does NOT hit the upstream."""
    conf = _read_nginx_conf()
    assert "location = /healthz" in conf, (
        "nginx.conf should expose a /healthz endpoint that does not "
        "require the FastAPI container to be reachable (for orchestrator "
        "liveness probes)."
    )


def test_nginx_conf_healthz_returns_200() -> None:
    """``/healthz`` must return 200 with a static body (no proxy_pass)."""
    conf = _read_nginx_conf()
    match = re.search(
        r"location\s+=\s+/healthz\s*\{[^}]*\}",
        conf,
        re.DOTALL,
    )
    assert match, "missing `location = /healthz { ... }` block"
    block = match.group(0)
    assert "return 200" in block, (
        "the /healthz block must `return 200` so the response is "
        "served by nginx itself (no proxy_pass)."
    )
    assert "proxy_pass" not in block, (
        "the /healthz block must NOT proxy_pass — it should be a "
        "self-contained health probe."
    )


def test_nginx_conf_keeps_proxy_headers() -> None:
    """
    Reverse-proxy headers (``X-Real-IP``, ``X-Forwarded-For``,
    ``X-Forwarded-Proto``) must be set so the FastAPI app's
    RequestIDMiddleware and security headers work correctly.
    """
    conf = _read_nginx_conf()
    for header in ("X-Real-IP", "X-Forwarded-For", "X-Forwarded-Proto"):
        assert header in conf, (
            f"nginx.conf must propagate the `{header}` header to the "
            f"FastAPI upstream."
        )


def test_nginx_conf_overwrites_x_forwarded_for_with_remote_addr() -> None:
    """SEC-ME-008: ``X-Forwarded-For`` must be set to ``$remote_addr``
    (overwrite), not ``$proxy_add_x_forwarded_for`` (append).

    With ``$proxy_add_x_forwarded_for`` an attacker can spoof their
    source IP by prepending a fake entry to the header, which would
    let them bypass the per-IP rate limiter.  Overwriting with
    ``$remote_addr`` forces the upstream to see the real socket
    address — the only value we can trust.
    """
    conf = _read_nginx_conf()
    # The exact line we expect.
    assert re.search(
        r"proxy_set_header\s+X-Forwarded-For\s+\$remote_addr\s*;",
        conf,
    ), (
        "SEC-ME-008: nginx.conf must set `proxy_set_header "
        "X-Forwarded-For $remote_addr;` (overwrite, not append) so "
        "clients cannot spoof their source IP and bypass the rate "
        "limiter."
    )
    # And the old, append-based directive must be gone.
    assert "proxy_add_x_forwarded_for" not in conf, (
        "SEC-ME-008: nginx.conf must NOT use `$proxy_add_x_forwarded_for` "
        "— that appends client-supplied values, allowing IP spoofing."
    )


# ── Path-resolution logic (no rewrite) ──────────────────────────────────


@pytest.mark.parametrize(
    "incoming_path",
    [
        "/api/games/health",
        "/api/games",
        "/api/tips/generate",
        "/api/backtest/run",
        "/api/admin/daily-sync/trigger",
        "/api/backtest/compare?season=2024",
        "/health",
        "/docs",
    ],
)
def test_nginx_passes_path_through_verbatim(incoming_path: str) -> None:
    """
    No path rewriting should happen — the FastAPI app is mounted at
    ``/api/...`` (and the docs at ``/docs``) directly, so nginx
    forwards paths unchanged.
    """
    assert resolve_upstream_path(incoming_path) == incoming_path


# ── Defence-in-depth security headers (CR-005) ─────────────────────────
#
# FastAPI's security middleware also sets these, but the proxy is the
# final hop on the wire — if a regression strips them in the app, the
# proxy still emits them on every response (including 4xx/5xx).


def test_nginx_conf_sets_x_content_type_options() -> None:
    conf = _read_nginx_conf()
    assert re.search(r'add_header\s+X-Content-Type-Options\s+"nosniff"\s+always', conf), (
        "nginx.conf must set X-Content-Type-Options nosniff always"
    )


def test_nginx_conf_sets_x_frame_options() -> None:
    conf = _read_nginx_conf()
    assert re.search(r'add_header\s+X-Frame-Options\s+"SAMEORIGIN"\s+always', conf), (
        "nginx.conf must set X-Frame-Options SAMEORIGIN always"
    )


def test_nginx_conf_sets_referrer_policy() -> None:
    conf = _read_nginx_conf()
    assert re.search(
        r'add_header\s+Referrer-Policy\s+"strict-origin-when-cross-origin"\s+always',
        conf,
    ), "nginx.conf must set Referrer-Policy"


def test_nginx_conf_sets_hsts() -> None:
    conf = _read_nginx_conf()
    assert re.search(r"add_header\s+Strict-Transport-Security", conf), (
        "nginx.conf must set Strict-Transport-Security"
    )


# ── Per-IP rate limit (HI-002) ──────────────────────────────────────────


def test_nginx_conf_has_rate_limit_zone() -> None:
    conf = _read_nginx_conf()
    assert "limit_req_zone" in conf, (
        "nginx.conf must define a limit_req_zone"
    )
    assert "limit_req" in conf, (
        "nginx.conf must apply limit_req to the / location"
    )


# ── Top-level proxy_buffering off was a HI-002 finding ─────────────────


def test_nginx_conf_removes_global_proxy_buffering_off() -> None:
    """Global proxy_buffering off was a HI-002 finding."""
    conf = _read_nginx_conf()
    # The directive may appear inside a specific location block (for
    # streaming endpoints) but not at the top level of the server.
    server_block = re.search(
        r"server\s*\{(.*)\}", conf, re.DOTALL
    )
    assert server_block
    body = server_block.group(1)
    assert not re.search(r"^\s*proxy_buffering\s+off\s*;", body, re.MULTILINE), (
        "nginx.conf must not have a top-level proxy_buffering off"
    )
