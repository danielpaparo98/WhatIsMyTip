"""Structured logging with optional JSON output.

When the ``LOG_FORMAT`` environment variable is set to ``json``, log records
are emitted as single-line JSON objects suitable for production log
aggregation (e.g. Loki, Datadog, CloudWatch Logs Insights).

In all other cases the traditional human-readable format is used, which is
preferable for local development.
"""

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone

from .config import settings


class JsonFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object.

    Note (LO-002)
    ------------
    This formatter does NOT redact PII.  The application is expected
    to scrub sensitive fields (``email``, ``password``, ``token``,
    etc.) before passing them to ``logger.*`` so they never reach
    the log stream.  A future hardening pass may apply a
    configurable allow-list of fields to mask here.


    Standard fields: ``timestamp``, ``level``, ``logger``, ``message``.
    Any ``extra`` fields passed to the log call are merged into the object.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Merge structured extra fields (skip known LogRecord internal attributes).
        # Use a hard-coded whitelist for stability across Python versions.
        standard_attrs = {
            "args", "created", "exc_info", "exc_text", "filename",
            "funcName", "levelname", "levelno", "lineno", "module",
            "msecs", "msg", "name", "pathname", "process",
            "processName", "relativeCreated", "stack_info", "thread",
            "threadName", "taskName",
        }
        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                log_entry[key] = value

        if record.exc_info and record.exc_info[1] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


def generate_execution_id() -> str:
    """Return a short, unique execution ID (first 8 chars of uuid4)."""
    return uuid.uuid4().hex[:8]


def get_logger(name: str) -> logging.Logger:
    """Get a configured logger instance.

    Args:
        name: The name for the logger (typically __name__)

    Returns:
        A configured logger instance
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)

        if os.getenv("LOG_FORMAT", "").lower() == "json":
            formatter: logging.Formatter = JsonFormatter()
        else:
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )

        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.setLevel(logging.DEBUG if settings.environment == "development" else logging.INFO)
    return logger
