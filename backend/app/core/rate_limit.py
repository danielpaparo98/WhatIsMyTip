"""Rate limiting via slowapi.

* :func:`get_limiter` — factory that builds a :class:`slowapi.Limiter`
  configured from settings (``rate_limit_max_requests`` / window).
* The limiter is keyed on the real client IP (NOT a forwarded-for
  header) so clients cannot trivially spoof their identity.
* Tighter per-route limits can be applied via
  ``@limiter.limit("5/minute")`` on individual route handlers.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from packages.shared.config import settings


def get_limiter() -> Limiter:
    """Build a fresh :class:`slowapi.Limiter` instance.

    Returns a new Limiter per call so tests can override settings
    between calls.  Production code in ``main.py`` caches the result
    on ``app.state.limiter``.

    Returns:
        A Limiter with the default limit derived from
        ``settings.rate_limit_max_requests`` /
        ``settings.rate_limit_window_seconds`` and the key function
        pinned to :func:`slowapi.util.get_remote_address` (real IP).
    """
    default_limit = (
        f"{settings.rate_limit_max_requests}/"
        f"{settings.rate_limit_window_seconds} seconds"
    )

    return Limiter(
        key_func=get_remote_address,
        default_limits=[default_limit],
        # When the limit is exceeded, slowapi raises ``RateLimitExceeded``,
        # which is mapped to a 429 by the exception handler registered
        # in ``main.py``.
        headers_enabled=True,
    )
