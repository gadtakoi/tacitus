"""Sentry integration for error tracking and performance monitoring."""

import logging
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

from app.core.config import settings

logger = logging.getLogger(__name__)

_SENSITIVE_KEYS = {
    "password",
    "token",
    "access_token",
    "refresh_token",
    "jwt_secret",
    "s3_secret_key",
}


def _strip_sensitive_data(event: dict[str, Any], hint: Any) -> dict[str, Any] | None:
    """Remove sensitive fields from Sentry events before sending.

    Args:
        event: Sentry event dictionary.
        hint: Additional event metadata.

    Returns:
        Sanitized event or None to drop the event.
    """
    # Strip sensitive keys from request data
    if "request" in event:
        request = event["request"]
        for key in ("data", "cookies", "headers", "env"):
            if key in request and isinstance(request[key], dict):
                for sensitive in _SENSITIVE_KEYS:
                    if sensitive in request[key]:
                        request[key][sensitive] = "[FILTERED]"
    # Strip sensitive keys from extra context
    if "extra" in event and isinstance(event["extra"], dict):
        for sensitive in _SENSITIVE_KEYS:
            if sensitive in event["extra"]:
                event["extra"][sensitive] = "[FILTERED]"
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
        release="tacitus@0.1.0",
        integrations=[FastApiIntegration()],
        before_send=_strip_sensitive_data,
    )
    logger.info(
        "Sentry initialized",
        extra={
            "environment": settings.sentry_environment,
            "traces_sample_rate": settings.sentry_traces_sample_rate,
        },
    )


def set_sentry_user(username: str, request_id: str | None = None) -> None:
    """Set user context in Sentry scope.

    Args:
        username: Authenticated username.
        request_id: Optional request ID to attach as tag.
    """
    with sentry_sdk.push_scope() as scope:
        scope.set_user({"username": username})
        if request_id:
            scope.set_tag("request_id", request_id)
