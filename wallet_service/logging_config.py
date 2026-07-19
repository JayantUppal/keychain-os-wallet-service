"""Structured JSON logging. In production these lines become metrics and traces."""

import json
import logging
import os
import sys
from typing import Any

SERVICE_NAME = "wallet-service"
ENVIRONMENT = os.getenv("ENVIRONMENT", "local")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Standard LogRecord attributes we don't want to duplicate in the JSON payload.
_RESERVED = set(logging.makeLogRecord({}).__dict__.keys()) | {"extra_fields"}


class JsonFormatter(logging.Formatter):
    """Render each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "service": SERVICE_NAME,
            "environment": ENVIRONMENT,
            "message": record.getMessage(),
        }
        # Fields attached via logger.info(..., extra={"extra_fields": {...}}).
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(LOG_LEVEL)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    """Log a structured event with arbitrary key/value fields."""
    logger.log(level, message, extra={"extra_fields": fields})
