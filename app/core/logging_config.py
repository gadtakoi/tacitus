"""Structured logging configuration."""

import json
import logging
import logging.handlers
import os
import re
import sys
import uuid
from typing import Any

from fastapi import Request

from app.core.config import settings

# Attributes the logging module puts on every record. Derived from a probe
# record rather than hardcoded, so fields added by newer Python versions are
# excluded automatically — ``taskName`` (new in 3.12) previously leaked into
# every JSON line as ``"taskName": null``. ``message`` and ``asctime`` are
# added during formatting and are emitted under our own keys.
_RESERVED_RECORD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__,
) | {"message", "asctime", "request_id"}

# Bound for an inbound ``X-Request-Id``. Comfortably fits the formats that
# actually appear in this position — a UUID (36), nginx's ``$request_id``
# (32 hex), a W3C ``traceparent`` (55) — while keeping an unbounded string out
# of every log line.
_MAX_REQUEST_ID_LENGTH = 64

# ``fullmatch`` is used rather than ``match`` with ``$``: ``$`` also matches
# just before a trailing newline, so ``"abc\n"`` would slip through and let a
# client append a forged line to the plain-text log format.
_SAFE_REQUEST_ID = re.compile(rf"[A-Za-z0-9._-]{{1,{_MAX_REQUEST_ID_LENGTH}}}")


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
            if key not in _RESERVED_RECORD_ATTRS and not key.startswith("_"):
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

    An inbound ``X-Request-Id`` is honoured so a request can be correlated
    across systems, but the value is attacker-controlled and ends up in three
    places that make it dangerous unsanitised: every log line, the
    ``x-request-id`` response header, and error response bodies. It is
    therefore accepted only when it is short and made of characters that
    cannot forge a log record — otherwise a fresh UUID is generated and the
    supplied value is discarded.

    Args:
        request: FastAPI request object.

    Returns:
        The client-supplied ID when it is well-formed, otherwise a new UUID4.
    """
    header_request_id = request.headers.get("x-request-id")
    if header_request_id and _SAFE_REQUEST_ID.fullmatch(header_request_id):
        return header_request_id
    return str(uuid.uuid4())
