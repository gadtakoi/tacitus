"""Sentry integration for error tracking and performance monitoring."""

import logging
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

from app import __version__
from app.core.config import settings

logger = logging.getLogger(__name__)

# Compared case-insensitively: HTTP headers arrive with their original casing
# ("Authorization"), so an exact lowercase match would let them through.
_SENSITIVE_KEYS = {
    "password",
    "token",
    "access_token",
    "refresh_token",
    "jwt_secret",
    "s3_secret_key",
    "s3_access_key",
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
}

_FILTERED = "[FILTERED]"


def _redact(mapping: dict[str, Any]) -> None:
    """Replace values of sensitive keys in ``mapping`` in place.

    Args:
        mapping: Dictionary to sanitize. Keys are matched case-insensitively.
    """
    for key in mapping:
        if key.lower() in _SENSITIVE_KEYS:
            mapping[key] = _FILTERED


def _strip_sensitive_data(event: dict[str, Any], hint: Any) -> dict[str, Any] | None:
    """Remove sensitive fields from Sentry events before sending.

    Args:
        event: Sentry event dictionary.
        hint: Additional event metadata.

    Returns:
        Sanitized event or None to drop the event.
    """
    # Strip sensitive keys from request data
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("data", "cookies", "headers", "env"):
            section = request.get(key)
            if isinstance(section, dict):
                _redact(section)
    # Strip sensitive keys from extra context
    extra = event.get("extra")
    if isinstance(extra, dict):
        _redact(extra)
    return event


def init_sentry() -> None:
    """Initialize Sentry SDK if DSN is configured."""
    if not settings.sentry_dsn:
        logger.info("Sentry DSN not configured, skipping Sentry initialization")
        return

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        release=f"tacitus@{__version__}",
        integrations=[FastApiIntegration()],
        before_send=_strip_sensitive_data,  # type: ignore[arg-type]
    )
    logger.info(
        "Sentry initialized",
        extra={
            "environment": settings.sentry_environment,
            "traces_sample_rate": settings.sentry_traces_sample_rate,
        },
    )
