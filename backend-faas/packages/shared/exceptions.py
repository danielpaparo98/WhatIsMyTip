"""Custom exception classes for backend-faas.

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
