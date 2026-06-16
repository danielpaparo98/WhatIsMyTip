"""Unit tests for ``app.core.exceptions``.

These cover the FastAPI-era ``BackendServiceError`` used by middleware and
exception handlers.  Existing FaaS exception classes from
``packages.shared.exceptions`` must still be importable from the new
``app.core.exceptions`` module so service-layer code keeps working during
the migration.
"""

from __future__ import annotations

import pytest


class TestBackendServiceError:
    """BackendServiceError carries HTTP context plus a stable error code."""

    def test_is_exception_subclass(self):
        from app.core.exceptions import BackendServiceError

        assert issubclass(BackendServiceError, Exception)

    def test_stores_status_code_code_and_message(self):
        from app.core.exceptions import BackendServiceError

        err = BackendServiceError(
            status_code=404,
            code="not_found",
            message="Resource missing",
        )

        assert err.status_code == 404
        assert err.code == "not_found"
        assert err.message == "Resource missing"

    def test_details_default_to_empty_dict(self):
        from app.core.exceptions import BackendServiceError

        err = BackendServiceError(
            status_code=400, code="bad_request", message="nope"
        )

        assert err.details == {}

    def test_details_optional_dict_is_preserved(self):
        from app.core.exceptions import BackendServiceError

        err = BackendServiceError(
            status_code=422,
            code="validation_error",
            message="invalid input",
            details={"field": "email"},
        )

        assert err.details == {"field": "email"}

    def test_str_returns_message(self):
        from app.core.exceptions import BackendServiceError

        err = BackendServiceError(
            status_code=500, code="boom", message="something went wrong"
        )

        assert str(err) == "something went wrong"

    def test_can_be_raised_and_caught(self):
        from app.core.exceptions import BackendServiceError

        with pytest.raises(BackendServiceError) as exc_info:
            raise BackendServiceError(
                status_code=401, code="unauthorized", message="nope"
            )

        assert exc_info.value.status_code == 401


class TestHttpErrorHelper:
    """The ``http_error`` factory wraps status_code/code/message in one call."""

    def test_returns_backend_service_error(self):
        from app.core.exceptions import BackendServiceError, http_error

        err = http_error(403, "forbidden", "no access")

        assert isinstance(err, BackendServiceError)
        assert err.status_code == 403
        assert err.code == "forbidden"
        assert err.message == "no access"
        assert err.details == {}

    def test_details_default_to_empty_dict(self):
        from app.core.exceptions import http_error

        err = http_error(429, "rate_limited", "slow down")

        assert err.details == {}

    def test_message_required(self):
        """The helper enforces a non-empty message — used by all error responses."""
        from app.core.exceptions import http_error

        err = http_error(500, "internal_error", "boom")

        assert err.message == "boom"


class TestLegacyExceptionsReexported:
    """``packages.shared.exceptions`` classes must remain importable here.

    This guarantees service-layer code (which still raises the legacy
    ``JobError`` / ``TransientJobError`` / ``PermanentJobError``) continues to
    work without changes during the FastAPI migration.
    """

    def test_job_error_reexported(self):
        from app.core.exceptions import JobError

        assert issubclass(JobError, Exception)

    def test_transient_job_error_reexported(self):
        from app.core.exceptions import TransientJobError, JobError

        assert issubclass(TransientJobError, JobError)

    def test_permanent_job_error_reexported(self):
        from app.core.exceptions import PermanentJobError, JobError

        assert issubclass(PermanentJobError, JobError)

    def test_classify_error_reexported(self):
        from app.core.exceptions import classify_error

        result = classify_error(RuntimeError("connection refused"))
        # "connection refused" matches a transient pattern
        assert result.__class__.__name__ == "TransientJobError"
