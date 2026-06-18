"""Tests for SEC-LO-001: drop or header-only restrict the legacy
``api_helpers.verify_api_key`` helper.

The Phase 4 ``packages.shared.api_helpers.verify_api_key`` is
intentionally permissive:

* It accepts the key from headers, **query string**, **or body**.
* The header key is matched case-sensitively only on
  ``x-api-key``/``X-Api-Key``, missing common alternatives.

Allowing the key in the query string is dangerous because the value
ends up in nginx access logs, server-side request logs, and browser
history.  The FastAPI path already has a much stricter
``app.core.security.verify_api_key`` (header-only) that all routes
should use.  The legacy helper is dead code in the FaaS-less
architecture.

The fix:
* Delete the legacy ``verify_api_key`` function entirely.
* Remove ``response()``'s wildcard ``allowed_methods`` fallback
  (``["GET", "POST", "OPTIONS"]``) so callers must opt in
  explicitly — the current default is too permissive and lets a
  misconfigured route accidentally expose PUT/DELETE to CORS.
* Verify that the module's public exports no longer include
  ``verify_api_key``.
"""

from __future__ import annotations

import pytest


class TestLegacyVerifyApiKeyRemoved:
    """``packages.shared.api_helpers`` no longer exports ``verify_api_key``."""

    def test_verify_api_key_not_in_module(self) -> None:
        from packages.shared import api_helpers

        assert not hasattr(api_helpers, "verify_api_key"), (
            "SEC-LO-001: `packages.shared.api_helpers.verify_api_key` "
            "must be removed (it accepts the API key from query params "
            "and the request body — a logging/audit-log leak).  Use "
            "`app.core.security.verify_api_key` (header-only) instead."
        )

    def test_verify_api_key_not_importable(self) -> None:
        """Importing the symbol directly must raise ImportError."""
        with pytest.raises(ImportError):
            from packages.shared.api_helpers import verify_api_key  # noqa: F401


class TestResponseNoWildcardFallback:
    """``response()`` must require ``allowed_methods`` to be explicit."""

    def test_response_default_allowed_methods_is_just_options(self) -> None:
        """Without an explicit ``allowed_methods`` kwarg, the response
        must default to ``["OPTIONS"]`` only — preflight only, no
        actual data operations.  A misconfigured route that forgot
        to pass ``allowed_methods`` should be safe-by-default."""
        from packages.shared.api_helpers import response

        # The preflight-only default makes a missing ``allowed_methods``
        # call safe — the browser will reject non-OPTIONS methods via
        # the Access-Control-Allow-Methods header.
        result = response(200, data={"ok": True})
        # And the explicit caller-provided value still wins.
        result2 = response(
            200,
            data={"ok": True},
            allowed_methods=["GET", "POST"],
        )
        assert "GET" in result2["headers"]["Access-Control-Allow-Methods"]
        assert "POST" in result2["headers"]["Access-Control-Allow-Methods"]
        # The default (no allowed_methods) must NOT include GET/POST.
        assert "GET" not in result["headers"]["Access-Control-Allow-Methods"]
        assert "POST" not in result["headers"]["Access-Control-Allow-Methods"]
        # But OPTIONS must be in the default (preflight).
        assert "OPTIONS" in result["headers"]["Access-Control-Allow-Methods"]


class TestNoDirectApiKeyCallers:
    """No other module in the codebase imports the legacy helper."""

    def test_no_internal_imports_of_legacy_helper(self) -> None:
        """Static scan: the legacy symbol is not imported by any
        production module under ``backend/packages`` or ``backend/app``."""
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]  # backend/tests/unit/...
        prod_roots = (repo_root / "packages", repo_root / "app")
        offenders: list[str] = []
        for root in prod_roots:
            for path in root.rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                if "api_helpers.verify_api_key" in text or (
                    "from packages.shared.api_helpers import" in text
                    and "verify_api_key" in text
                ):
                    offenders.append(str(path.relative_to(repo_root)))

        assert not offenders, (
            "SEC-LO-001: legacy `verify_api_key` is still imported by:\n  "
            + "\n  ".join(offenders)
        )
