"""Unit tests for structured logging (JSON formatter, execution ID)."""

import json
import logging
import os
import sys
import pytest
from unittest.mock import patch

# Make shared package importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ---------------------------------------------------------------------------
# Tests: JsonFormatter
# ---------------------------------------------------------------------------

class TestJsonFormatter:
    """Tests for the JsonFormatter class."""

    def test_produces_valid_json_output(self):
        """JsonFormatter.format() returns valid JSON string."""
        from packages.shared.logger import JsonFormatter

        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["message"] == "Test message"
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test.logger"
        assert "timestamp" in parsed

    def test_includes_extra_fields(self):
        """Extra fields passed to log records appear in JSON output."""
        from packages.shared.logger import JsonFormatter

        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Job started",
            args=(),
            exc_info=None,
        )
        record.job_name = "daily-sync"
        record.execution_id = "abc12345"

        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["job_name"] == "daily-sync"
        assert parsed["execution_id"] == "abc12345"

    def test_includes_exception_info(self):
        """Exception info is included in JSON output when present."""
        from packages.shared.logger import JsonFormatter

        formatter = JsonFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test.logger",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )

        output = formatter.format(record)
        parsed = json.loads(output)

        assert "exception" in parsed
        assert "ValueError: test error" in parsed["exception"]

    def test_timestamp_is_iso_format(self):
        """Timestamp field is in ISO 8601 format."""
        from packages.shared.logger import JsonFormatter
        from datetime import datetime

        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="msg",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)
        parsed = json.loads(output)

        # Should be parseable as ISO format
        datetime.fromisoformat(parsed["timestamp"])


# ---------------------------------------------------------------------------
# Tests: generate_execution_id
# ---------------------------------------------------------------------------

class TestGenerateExecutionId:
    """Tests for the generate_execution_id() function."""

    def test_returns_8_char_string(self):
        """Execution ID is exactly 8 characters long."""
        from packages.shared.logger import generate_execution_id

        eid = generate_execution_id()
        assert isinstance(eid, str)
        assert len(eid) == 8

    def test_returns_hex_string(self):
        """Execution ID contains only hex characters."""
        from packages.shared.logger import generate_execution_id

        eid = generate_execution_id()
        assert all(c in "0123456789abcdef" for c in eid)

    def test_generates_unique_ids(self):
        """Multiple calls produce different IDs (with high probability)."""
        from packages.shared.logger import generate_execution_id

        ids = {generate_execution_id() for _ in range(100)}
        # All 100 should be unique (probability of collision is negligible)
        assert len(ids) == 100


# ---------------------------------------------------------------------------
# Tests: get_logger with LOG_FORMAT
# ---------------------------------------------------------------------------

class TestGetLogger:
    """Tests for get_logger() with different LOG_FORMAT settings."""

    def test_default_formatter_is_human_readable(self):
        """When LOG_FORMAT is not set, logger uses human-readable format."""
        with patch.dict(os.environ, {}, clear=False):
            if "LOG_FORMAT" in os.environ:
                del os.environ["LOG_FORMAT"]

            from packages.shared.logger import get_logger
            import importlib
            import packages.shared.logger as logger_mod

            # Clear handler cache by using a unique name
            logger_name = f"test_human_readable_{id(self)}"
            test_logger = logging.getLogger(logger_name)
            test_logger.handlers.clear()

            with patch.object(logger_mod, "settings") as mock_settings:
                mock_settings.environment = "test"
                result = get_logger(logger_name)

            assert len(result.handlers) == 1
            handler = result.handlers[0]
            assert not isinstance(handler.formatter, logger_mod.JsonFormatter)

    def test_json_formatter_when_log_format_is_json(self):
        """When LOG_FORMAT=json, logger uses JsonFormatter."""
        with patch.dict(os.environ, {"LOG_FORMAT": "json"}):
            import importlib
            import packages.shared.logger as logger_mod
            importlib.reload(logger_mod)

            logger_name = f"test_json_format_{id(self)}"
            test_logger = logging.getLogger(logger_name)
            test_logger.handlers.clear()

            with patch.object(logger_mod, "settings") as mock_settings:
                mock_settings.environment = "test"
                result = logger_mod.get_logger(logger_name)

            assert len(result.handlers) == 1
            handler = result.handlers[0]
            assert isinstance(handler.formatter, logger_mod.JsonFormatter)

        # Cleanup
        if "LOG_FORMAT" in os.environ:
            del os.environ["LOG_FORMAT"]

    def test_execution_id_included_in_log_output(self):
        """When using JSON formatter, execution_id from extra appears in output."""
        from packages.shared.logger import JsonFormatter, generate_execution_id

        formatter = JsonFormatter()
        execution_id = generate_execution_id()

        record = logging.LogRecord(
            name="cron.daily-sync",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Job started",
            args=(),
            exc_info=None,
        )
        record.execution_id = execution_id
        record.job_name = "daily-sync"

        output = formatter.format(record)
        parsed = json.loads(output)

        assert parsed["execution_id"] == execution_id
        assert parsed["job_name"] == "daily-sync"
