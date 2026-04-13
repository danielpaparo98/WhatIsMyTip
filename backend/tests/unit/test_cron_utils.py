import pytest
from app.cron.base import classify_error, TransientJobError, PermanentJobError


class TestClassifyError:
    def test_timeout_is_transient(self):
        error = TimeoutError("Connection timeout")
        result = classify_error(error)
        assert isinstance(result, TransientJobError)

    def test_network_error_is_transient(self):
        error = ConnectionError("Network unreachable")
        result = classify_error(error)
        assert isinstance(result, TransientJobError)

    def test_value_error_is_permanent(self):
        error = ValueError("Invalid data")
        result = classify_error(error)
        assert isinstance(result, PermanentJobError)

    def test_context_included_in_message(self):
        error = ValueError("bad data")
        result = classify_error(error, context="Daily sync")
        assert "Daily sync" in str(result)

    def test_type_error_is_permanent(self):
        error = TypeError("wrong type")
        result = classify_error(error)
        assert isinstance(result, PermanentJobError)

    def test_runtime_error_is_permanent(self):
        error = RuntimeError("something broke")
        result = classify_error(error)
        assert isinstance(result, PermanentJobError)

    def test_transient_error_message_contains_original(self):
        error = TimeoutError("Connection timeout after 30s")
        result = classify_error(error)
        assert "Connection timeout after 30s" in str(result)

    def test_no_context_uses_error_message(self):
        error = ValueError("bad data")
        result = classify_error(error)
        assert "bad data" in str(result)

    def test_job_error_hierarchy(self):
        """TransientJobError and PermanentJobError both inherit from JobError."""
        from app.cron.base import JobError
        assert issubclass(TransientJobError, JobError)
        assert issubclass(PermanentJobError, JobError)
