"""Tests for custom exception classes and error classification."""

import pytest

from packages.shared.exceptions import (
    JobError,
    TransientJobError,
    PermanentJobError,
    classify_error,
)


# ---------------------------------------------------------------------------
# JobError base class
# ---------------------------------------------------------------------------


class TestJobError:
    """Tests for the JobError base exception class."""

    def test_construction_with_message(self):
        err = JobError("something went wrong")
        assert err.message == "something went wrong"
        assert str(err) == "something went wrong"

    def test_construction_with_details(self):
        err = JobError("boom", details={"code": 42})
        assert err.details == {"code": 42}

    def test_default_details_is_empty_dict(self):
        err = JobError("boom")
        assert err.details == {}

    def test_is_exception(self):
        err = JobError("bang")
        assert isinstance(err, Exception)


# ---------------------------------------------------------------------------
# TransientJobError
# ---------------------------------------------------------------------------


class TestTransientJobError:
    """Tests for TransientJobError."""

    def test_inherits_job_error(self):
        err = TransientJobError("timeout")
        assert isinstance(err, JobError)
        assert isinstance(err, Exception)

    def test_preserves_message_and_details(self):
        err = TransientJobError("timeout", details={"retry": True})
        assert err.message == "timeout"
        assert err.details == {"retry": True}


# ---------------------------------------------------------------------------
# PermanentJobError
# ---------------------------------------------------------------------------


class TestPermanentJobError:
    """Tests for PermanentJobError."""

    def test_inherits_job_error(self):
        err = PermanentJobError("bad data")
        assert isinstance(err, JobError)
        assert isinstance(err, Exception)

    def test_preserves_message_and_details(self):
        err = PermanentJobError("invalid input", details={"field": "email"})
        assert err.message == "invalid input"
        assert err.details == {"field": "email"}

    def test_transient_and_permanent_are_distinct(self):
        t = TransientJobError("t")
        p = PermanentJobError("p")
        assert not isinstance(t, PermanentJobError)
        assert not isinstance(p, TransientJobError)


# ---------------------------------------------------------------------------
# classify_error — transient patterns
# ---------------------------------------------------------------------------


class TestClassifyErrorTransient:
    """Verify that known transient error messages are classified correctly."""

    @pytest.mark.parametrize(
        "message",
        [
            "Connection timed out",
            "timeout after 30s",
            "timed out waiting for response",
            "connection reset by peer",
            "connection refused on port 5432",
            "connection closed unexpectedly",
            "connection aborted",
            "network error during request",
            "network failure detected",
            "network unreachable",
            "temporary failure in DNS resolution",
            "service unavailable",
            "HTTP 503 Service Unavailable",
            "HTTP 429 Too Many Requests",
            "rate limit exceeded",
            "could not connect to host",
            "connection pool exhausted",
            "redis connection lost",
            "redis error: READONLY",
            "dns resolution failed",
            "dns error",
            "socket closed prematurely",
            "socket error: ECONNREFUSED",
            "socket shutdown",
            "ssl handshake failed",
            "ssl certificate verify failed",
        ],
    )
    def test_classified_as_transient(self, message):
        result = classify_error(RuntimeError(message))
        assert isinstance(result, TransientJobError)
        assert not isinstance(result, PermanentJobError)

    def test_details_contain_original_type(self):
        result = classify_error(ConnectionError("connection refused"))
        assert result.details["original_type"] == "ConnectionError"


# ---------------------------------------------------------------------------
# classify_error — permanent patterns
# ---------------------------------------------------------------------------


class TestClassifyErrorPermanent:
    """Verify that known permanent error messages are classified correctly."""

    @pytest.mark.parametrize(
        "message",
        [
            "user not found",
            "invalid data format",
            "invalid input provided",
            "invalid format string",
            "invalid argument count",
            "invalid parameter value",
            "permission denied for table users",
            "unauthorized access",
            "forbidden: insufficient privileges",
            "record already exists",
            "duplicate key value violates unique constraint",
            "unique constraint violation",
            "foreign key constraint fails",
            "check constraint violated",
            "data integrity error",
        ],
    )
    def test_classified_as_permanent(self, message):
        result = classify_error(ValueError(message))
        assert isinstance(result, PermanentJobError)
        assert not isinstance(result, TransientJobError)

    def test_details_contain_original_type(self):
        result = classify_error(LookupError("not found in database"))
        assert result.details["original_type"] == "LookupError"


# ---------------------------------------------------------------------------
# classify_error — pass-through for existing JobError instances
# ---------------------------------------------------------------------------


class TestClassifyErrorPassthrough:
    """If the error is already a JobError it should be returned unchanged."""

    def test_transient_job_error_passthrough(self):
        original = TransientJobError("custom transient", details={"src": "manual"})
        result = classify_error(original)
        assert result is original

    def test_permanent_job_error_passthrough(self):
        original = PermanentJobError("custom permanent", details={"src": "manual"})
        result = classify_error(original)
        assert result is original

    def test_base_job_error_passthrough(self):
        original = JobError("generic job error")
        result = classify_error(original)
        assert result is original


# ---------------------------------------------------------------------------
# classify_error — unknown errors default to transient
# ---------------------------------------------------------------------------


class TestClassifyErrorDefault:
    """Unknown error messages should default to TransientJobError."""

    def test_unknown_defaults_to_transient(self):
        result = classify_error(RuntimeError("something completely unexpected"))
        assert isinstance(result, TransientJobError)

    def test_empty_message_defaults_to_transient(self):
        result = classify_error(RuntimeError(""))
        assert isinstance(result, TransientJobError)

    def test_details_contain_original_type_for_unknown(self):
        result = classify_error(TypeError("unsupported operand"))
        assert result.details["original_type"] == "TypeError"


# ---------------------------------------------------------------------------
# classify_error — case insensitivity
# ---------------------------------------------------------------------------


class TestClassifyErrorCaseInsensitive:
    """Pattern matching should be case-insensitive."""

    def test_timeout_uppercase(self):
        result = classify_error(RuntimeError("CONNECTION TIMEOUT"))
        assert isinstance(result, TransientJobError)

    def test_not_found_mixed_case(self):
        result = classify_error(ValueError("Not Found"))
        assert isinstance(result, PermanentJobError)

    def test_rate_limit_uppercase(self):
        result = classify_error(RuntimeError("RATE LIMIT exceeded"))
        assert isinstance(result, TransientJobError)

    def test_unauthorized_uppercase(self):
        result = classify_error(PermissionError("UNAUTHORIZED"))
        assert isinstance(result, PermanentJobError)
