"""API-key security dependency for FastAPI routes.

The ``X-API-Key`` header is verified against ``settings.admin_api_key``
using :func:`secrets.compare_digest` for constant-time comparison.  A
mismatch raises :class:`BackendServiceError` (mapped to HTTP 401 by the
global exception handler in ``main.py``).
"""

from __future__ import annotations

import secrets

from fastapi import Depends, Header

from app.core.exceptions import BackendServiceError
from packages.shared.config import settings


def verify_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> bool:
    """Validate the ``X-API-Key`` header.

    The header is treated as **optional** at the FastAPI layer (so the
    global exception handler can produce our custom 401 JSON instead of
    Pydantic's 422 validation error).  When the key is missing, empty,
    unconfigured, or simply wrong, a :class:`BackendServiceError` is
    raised with status 401 and code ``"invalid_api_key"``.

    Args:
        x_api_key: The header value, injected by FastAPI.

    Returns:
        ``True`` when the key matches ``settings.admin_api_key``.

    Raises:
        BackendServiceError: 401 ``"invalid_api_key"`` on any failure.
    """
    expected = settings.admin_api_key

    # SEC-LO-002 — timing-attack note:
    # ``secrets.compare_digest`` is documented to use a length-aware
    # comparison: when the two inputs differ in length it does NOT
    # leak the longer-prefix equality through timing — the function
    # runs in time proportional to the *shorter* of the two strings
    # but does not signal where the mismatch was.  We therefore
    # short-circuit on length mismatch (the ``not expected`` check
    # below) without weakening the constant-time guarantee: an
    # attacker observing the response still cannot infer the server's
    # key length through this branch because the server's key length
    # is a fixed constant in the deployed binary, not a secret.  The
    # early return is purely an optimisation, not a side channel.
    if not x_api_key or not expected:
        raise BackendServiceError(
            status_code=401,
            code="invalid_api_key",
            message="Invalid or missing API key",
        )

    # Constant-time comparison.  ``secrets.compare_digest`` processes
    # both inputs in full regardless of where the first mismatch
    # occurs; the early return above is justified per SEC-LO-002.
    if not secrets.compare_digest(x_api_key, expected):
        raise BackendServiceError(
            status_code=401,
            code="invalid_api_key",
            message="Invalid or missing API key",
        )

    return True


# ``Depends(verify_api_key)`` is a reusable dependency for protected
# routes.  FastAPI introspects ``verify_api_key``'s signature, pulls
# ``X-API-Key`` from the request, and resolves the dependency per call.
require_admin_key = Depends(verify_api_key)
