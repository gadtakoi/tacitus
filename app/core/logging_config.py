"""Structured logging configuration."""

import logging
import sys
import uuid

from fastapi import Request


class RequestIdFilter(logging.Filter):
    """Inject request_id into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = getattr(record, "request_id", "unknown")
        return True


def setup_logging() -> None:
    """Configure structured logging for the application."""
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | request_id=%(request_id)s | %(message)s",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(RequestIdFilter())

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers = [handler]


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
