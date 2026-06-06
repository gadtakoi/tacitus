"""Structured logging configuration."""

import json
import logging
import logging.handlers
import os
import sys
import uuid
from typing import Any

from fastapi import Request

from app.core.config import settings


class JsonFormatter(logging.Formatter):
    """JSON structured log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "request_id"):
            log_data["request_id"] = record.request_id
        for key, value in record.__dict__.items():
            if key not in (
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "message",
                "asctime",
                "request_id",
            ) and not key.startswith("_"):
                log_data[key] = value
        return json.dumps(log_data, default=str, ensure_ascii=False)


class RequestIdFilter(logging.Filter):
    """Inject request_id into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = getattr(record, "request_id", "unknown")
        return True


def _ensure_log_dir(path: str) -> None:
    """Create log directory if it does not exist."""
    log_dir = os.path.dirname(path)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)


def setup_logging() -> None:
    """Configure structured logging for the application."""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    root_logger.handlers = []

    handlers: list[logging.Handler] = []

    if settings.log_format == "json":
        formatter: logging.Formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | request_id=%(request_id)s | %(message)s",
        )

    if settings.log_destination in ("stdout", "both"):
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(RequestIdFilter())
        handlers.append(stream_handler)

    if settings.log_destination in ("file", "both"):
        _ensure_log_dir(settings.log_file_path)
        file_handler = logging.handlers.RotatingFileHandler(
            filename=settings.log_file_path,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(RequestIdFilter())
        handlers.append(file_handler)

    for handler in handlers:
        root_logger.addHandler(handler)

    # Dedicated security log file
    security_logger = logging.getLogger("security")
    security_logger.setLevel(logging.INFO)
    security_log_path = os.path.join(os.path.dirname(settings.log_file_path), "security.log")
    _ensure_log_dir(security_log_path)
    security_handler = logging.handlers.RotatingFileHandler(
        filename=security_log_path,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
    )
    security_handler.setFormatter(formatter)
    security_handler.addFilter(RequestIdFilter())
    security_logger.addHandler(security_handler)


def get_request_id(request: Request) -> str:
    """Extract or generate request_id from request headers.

    Args:
        request: FastAPI request object.

    Returns:
        Request ID string.
    """
    header_request_id = request.headers.get("x-request-id")
    if header_request_id:
        return header_request_id
    return str(uuid.uuid4())
