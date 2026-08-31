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


def _create_file_handler(path: str) -> logging.Handler | None:
    """Create a rotating file handler, degrading gracefully on errors.

    In containerized deployments the log directory may be read-only or
    missing entirely. Crashing on startup because a log file cannot be
    opened would take the whole service down, so the failure is logged to
    stderr and ``None`` is returned instead — the service keeps running
    with the remaining handlers (e.g. stdout).

    Args:
        path: Log file path.

    Returns:
        Configured RotatingFileHandler or None when the file is unusable.
    """
    try:
        _ensure_log_dir(path)
        return logging.handlers.RotatingFileHandler(
            filename=path,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
        )
    except OSError as exc:
        print(
            f"Cannot open log file {path!r}: {exc}; continuing without file logging",
            file=sys.stderr,
        )
        return None


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
        file_handler = _create_file_handler(settings.log_file_path)
        if file_handler is not None:
            file_handler.setFormatter(formatter)
            file_handler.addFilter(RequestIdFilter())
            handlers.append(file_handler)
        elif settings.log_destination == "file":
            # File destination requested but unusable: fall back to stdout so
            # log records are not silently lost.
            fallback = logging.StreamHandler(sys.stdout)
            fallback.setFormatter(formatter)
            fallback.addFilter(RequestIdFilter())
            handlers.append(fallback)

    for handler in handlers:
        root_logger.addHandler(handler)

    # Dedicated security logger. It respects ``log_destination``: events go
    # to the separate security.log file when file logging is enabled and to
    # stdout otherwise. A fallback to stdout prevents total loss of security
    # events when the log directory is not writable.
    security_logger = logging.getLogger("security")
    security_logger.setLevel(logging.INFO)
    security_logger.propagate = False
    security_logger.handlers = []

    if settings.log_destination in ("file", "both"):
        security_log_path = os.path.join(os.path.dirname(settings.log_file_path), "security.log")
        security_handler = _create_file_handler(security_log_path)
        if security_handler is None:
            security_handler = logging.StreamHandler(sys.stdout)
    else:
        security_handler = logging.StreamHandler(sys.stdout)

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
