"""Utility functions for the application."""

import secrets
import string


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
