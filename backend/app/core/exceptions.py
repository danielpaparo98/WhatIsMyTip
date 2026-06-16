"""Exception types for the FastAPI app layer.

Defines ``BackendServiceError`` (carries HTTP context for the global
exception handler) and a small ``http_error`` factory.  Legacy exception
classes from ``packages.shared.exceptions`` are re-exported so that the
service layer can keep raising them unchanged.
"""

from __future__ import annotations

from typing import Any

# Re-export the FaaS-era exception classes so service code keeps working
# during the FastAPI migration.
from packages.shared.exceptions import (  # noqa: F401
    JobError,
    PermanentJobError,
    TransientJobError,
    classify_error,
)


class BackendServiceError(Exception):
    """Application-level error with an HTTP mapping.

    Attributes:
        status_code: HTTP status code (e.g. 400, 401, 404, 422, 500).
        code: Stable, machine-readable error code (e.g. ``"not_found"``).
        message: Human-readable error message safe for client display.
        details: Optional structured context (e.g. validation errors).
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details or {}
        super().__init__(message)

    def __str__(self) -> str:
        return self.message


def http_error(status_code: int, code: str, message: str) -> BackendServiceError:
    """Build a :class:`BackendServiceError` with empty details.

    The common case in services and route handlers is to raise a
    well-typed error with no structured payload, so this is a small
    convenience that reads more naturally at the call site:

        raise http_error(404, "game_not_found", "No game with that slug")
    """
    return BackendServiceError(status_code=status_code, code=code, message=message)
