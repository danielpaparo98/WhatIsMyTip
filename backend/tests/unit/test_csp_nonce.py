"""Tests for SEC-ME-006: tighten CSP and add structural nonce support.

Why this matters
----------------
The Phase 4 ``_DEFAULT_CSP`` allows ``img-src 'self' data: https:`` —
the ``https:`` wildcard lets the page load images from ANY HTTPS host,
which is broader than we need (we only ship self-hosted team logos from
``/public/logos``).  We also still have ``'unsafe-inline'`` for styles
because the Nuxt frontend emits inline ``<style>`` blocks.  Removing
that without coordinating with the Nuxt build would break rendering,
so this change adds a **structural** CSP-nonce path that the future
Nuxt-side change can plug into without re-touching this middleware.

Scope of this fix
-----------------
* Narrow ``img-src`` from ``'self' data: https:`` to ``'self' data:``
  (we never load images from arbitrary third-party HTTPS hosts).
* Add a ``generate_csp_nonce()`` helper and a ``_DEFAULT_CSP_TEMPLATE``
  with a ``{nonce}`` placeholder so the future Nuxt-side work can
  switch the inline-style policy to ``style-src 'self' 'nonce-{nonce}'``
  with a one-line change.
"""

from __future__ import annotations

import re

import pytest

from app.core.middleware import (
    _DEFAULT_CSP_TEMPLATE,
    SecurityHeadersMiddleware,
    generate_csp_nonce,
)


class TestCspBaseline:
    """The current CSP shape must still be returned to the client."""

    def test_csp_narrows_img_src(self) -> None:
        """``img-src`` must not include the ``https:`` wildcard."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware)

        @app.get("/")
        def _r():
            return {"ok": True}

        client = TestClient(app)
        resp = client.get("/")
        csp = resp.headers["Content-Security-Policy"]
        assert "img-src 'self' data:" in csp
        # The wildcard "https:" must no longer be in img-src.
        assert "img-src 'self' data: https:" not in csp

    def test_csp_still_includes_default_directives(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware)

        @app.get("/")
        def _r():
            return {"ok": True}

        client = TestClient(app)
        resp = client.get("/")
        csp = resp.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "style-src 'self'" in csp
        assert "script-src 'self'" in csp

    def test_csp_template_has_nonce_placeholder(self) -> None:
        """The CSP template must support a ``{nonce}`` placeholder for the
        future inline-style hardening."""
        assert "{nonce}" in _DEFAULT_CSP_TEMPLATE


class TestGenerateCspNonce:
    """``generate_csp_nonce`` returns a fresh per-request nonce."""

    def test_returns_string(self) -> None:
        nonce = generate_csp_nonce()
        assert isinstance(nonce, str)
        assert len(nonce) > 0

    def test_returns_distinct_values(self) -> None:
        """Two calls must produce different nonces (otherwise the CSP
        wouldn't be a meaningful per-request value)."""
        a = generate_csp_nonce()
        b = generate_csp_nonce()
        assert a != b

    def test_nonce_is_url_safe(self) -> None:
        """The nonce must not contain characters that would break a CSP
        header value (quotes, semicolons, newlines)."""
        nonce = generate_csp_nonce()
        # CSP source-list values are separated by ``;`` — disallow it
        # inside the nonce itself.
        assert ";" not in nonce
        # And no quote characters (CSP source values are quoted)
        assert '"' not in nonce
        assert "'" not in nonce
        # Base64url characters: A-Z a-z 0-9 - _
        assert re.fullmatch(r"[A-Za-z0-9_\-]+", nonce), (
            f"nonce {nonce!r} contains non-URL-safe characters"
        )


class TestMiddlewareNoncePlumbing:
    """A per-request nonce must be attachable to the response (placeholder).

    This is the "structural" part of the fix — we don't yet render the
    nonce in the CSP header (we keep the Phase 4 inline-style policy
    so the Nuxt frontend doesn't break).  But the helper exists and is
    pure / well-tested so the future Nuxt-side PR can switch to
    ``style-src 'self' 'nonce-{nonce}'`` without re-touching this file.
    """

    def test_generate_csp_nonce_is_callable_from_middleware_module(self) -> None:
        """The helper is exported from ``app.core.middleware`` so the
        future Nuxt-side change can ``from app.core.middleware import
        generate_csp_nonce`` and start using it."""
        import app.core.middleware as m

        assert hasattr(m, "generate_csp_nonce")
        assert callable(m.generate_csp_nonce)
