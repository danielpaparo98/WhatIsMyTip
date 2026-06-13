"""Custom exception classes for backend.

Provides error classification for cron jobs and API handlers,
enabling intelligent retry behavior and structured error responses.
"""

import re


class JobError(Exception):
    """Base exception for all job-related errors."""

    def __init__(self, message: str, *, details: dict | None = None):
        self.message = message
        self.details = details or {}
        super().__init__(message)


class TransientJobError(JobError):
    """Error that is likely to resolve on retry (network, timeout, etc.)."""
    pass


class PermanentJobError(JobError):
    """Error that will not resolve on retry (bad data, logic error, etc.)."""
    pass


# Patterns that indicate transient/retryable errors
_TRANSIENT_PATTERNS = [
    re.compile(r"timeout", re.IGNORECASE),
    re.compile(r"timed?\s*out", re.IGNORECASE),
    re.compile(r"connection\s*(reset|refused|closed|aborted)", re.IGNORECASE),
    re.compile(r"network\s*(error|failure|unreachable)", re.IGNORECASE),
    re.compile(r"temporary\s*failure", re.IGNORECASE),
    re.compile(r"service\s*unavailable", re.IGNORECASE),
    re.compile(r"503", re.IGNORECASE),
    re.compile(r"429", re.IGNORECASE),
    re.compile(r"rate\s*limit", re.IGNORECASE),
    re.compile(r"could\s*not\s*connect", re.IGNORECASE),
    re.compile(r"connection\s*pool\s*exhausted", re.IGNORECASE),
    re.compile(r"redis\s*(error|connection)", re.IGNORECASE),
    re.compile(r"dns\s*(error|resolution|failure)", re.IGNORECASE),
    re.compile(r"socket\s*(error|closed|shutdown)", re.IGNORECASE),
    re.compile(r"ssl\s*(error|handshake|certificate)", re.IGNORECASE),
]

# Patterns that indicate permanent/non-retryable errors
_PERMANENT_PATTERNS = [
    re.compile(r"not\s*found", re.IGNORECASE),
    re.compile(r"invalid\s*(data|input|format|argument|parameter)", re.IGNORECASE),
    re.compile(r"permission\s*denied", re.IGNORECASE),
    re.compile(r"unauthorized", re.IGNORECASE),
    re.compile(r"forbidden", re.IGNORECASE),
    re.compile(r"already\s*exists", re.IGNORECASE),
    re.compile(r"duplicate\s*key", re.IGNORECASE),
    re.compile(r"unique\s*constraint", re.IGNORECASE),
    re.compile(r"foreign\s*key\s*constraint", re.IGNORECASE),
    re.compile(r"check\s*constraint", re.IGNORECASE),
    re.compile(r"data\s*integrity", re.IGNORECASE),
]


def classify_error(error: Exception) -> JobError:
    """Classify an exception into TransientJobError or PermanentJobError.

    Args:
        error: The original exception to classify.

    Returns:
        A TransientJobError or PermanentJobError wrapping the original error.
    """
    message = str(error)

    # If it's already a JobError, return as-is
    if isinstance(error, JobError):
        return error

    # Check for transient patterns first
    for pattern in _TRANSIENT_PATTERNS:
        if pattern.search(message):
            return TransientJobError(message, details={"original_type": type(error).__name__})

    # Check for permanent patterns
    for pattern in _PERMANENT_PATTERNS:
        if pattern.search(message):
            return PermanentJobError(message, details={"original_type": type(error).__name__})

    # Default to transient (safer to retry unknown errors)
    return TransientJobError(message, details={"original_type": type(error).__name__})


# ---------------------------------------------------------------------------
# Retry helper — portable from the original FastAPI BaseJob.retry_with_backoff
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402
import random  # noqa: E402
from typing import Awaitable, Callable, TypeVar  # noqa: E402

_T = TypeVar("_T")


async def retry_with_backoff(
    func: Callable[[], Awaitable[_T]],
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_multiplier: float = 2.0,
    max_delay: float = 300.0,
    jitter: float = 0.1,
) -> _T:
    """Retry an async callable with exponential backoff and jitter.

    Only retries on ``TransientJobError``.  ``PermanentJobError`` is
    re-raised immediately, and all other exceptions are assumed transient.

    Args:
        func: An async callable that takes no arguments.
        max_retries: Maximum number of retries (default 3).
        initial_delay: Initial delay in seconds (default 1.0).
        backoff_multiplier: Multiplier for exponential backoff (default 2.0).
        max_delay: Maximum delay between retries in seconds (default 300.0).
        jitter: Random jitter factor, 0–1 (default 0.1).

    Returns:
        The return value of ``func()``.

    Raises:
        PermanentJobError: If ``func()`` raises a permanent error.
        Exception: If all retries are exhausted, the last transient
            exception is re-raised.
    """
    last_exception: Exception | None = None
    delay = initial_delay

    for attempt in range(max_retries + 1):
        try:
            return await func()
        except PermanentJobError:
            raise
        except TransientJobError:
            last_exception = TransientJobError(str(last_exception)) if last_exception else None  # type: ignore[arg-type]
            last_exception = TransientJobError(str(last_exception))  # noqa
        except Exception as e:
            last_exception = e

        if attempt == max_retries:
            break

        actual_delay = min(delay * (1 + random.uniform(-jitter, jitter)), max_delay)
        delay *= backoff_multiplier
        await asyncio.sleep(actual_delay)

    raise last_exception  # type: ignore[misc]
