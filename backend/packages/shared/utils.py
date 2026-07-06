"""Utility functions for the application."""

import secrets
import string
from datetime import date, datetime
from typing import Optional


def generate_slug(length: int = 10) -> str:
    """Generate a random alphanumeric slug.

    Uses lowercase ascii letters and digits for URL-friendly slugs.

    Args:
        length: Number of characters in the slug (default: 10)

    Returns:
        A random alphanumeric string of the specified length
    """
    alphabet = string.ascii_lowercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def ensure_datetime(value: object) -> Optional[datetime]:
    """Coerce ``value`` to a ``datetime`` (idempotent, never raises).

    Defends against ORM objects that lost their ``datetime`` type during
    a cache (JSON) round-trip, where a ``DateTime`` column comes back as
    an ISO-8601 ``str``. ``date`` instances are promoted to midnight
    datetimes; ``datetime`` values are returned unchanged; ``None`` and
    unparseable values are returned unchanged.
    """
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    return value
