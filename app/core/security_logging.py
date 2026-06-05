"""Dedicated security logging module."""

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.ip import get_client_ip

logger = logging.getLogger("security")


def log_security_event(
    event_type: str,
    severity: str = "info",
    ip: str | None = None,
    user: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Log a structured security event.

    Args:
        event_type: Event category, e.g. ``auth.login.success``.
        severity: Log level name (``info``, ``warning``, ``error``).
        ip: Client IP address.
        user: Username or user identifier.
        extra: Additional context fields.
    """
    payload: dict[str, Any] = {
        "event_type": event_type,
        "severity": severity,
    }
    if ip is not None:
        payload["ip"] = ip
    if user is not None:
        payload["user"] = user
    if extra is not None:
        payload.update(extra)

    log_method = getattr(logger, severity, logger.info)
    log_method("Security event", extra=payload)


class SuspiciousActivityMiddleware(BaseHTTPMiddleware):
    """Middleware that detects brute-force-like patterns (401/403 spikes)."""

    _lock = threading.Lock()
    _responses: dict[str, list[datetime]] = {}

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        """Track 401/403 responses and log suspicious activity.

        Args:
            request: Incoming request.
            call_next: Next handler.

        Returns:
            Response object.
        """
        response = await call_next(request)
        if response.status_code in (401, 403):
            ip = get_client_ip(request)
            now = datetime.now(UTC)
            with self._lock:
                timestamps = self._responses.get(ip, [])
                cutoff = now - timedelta(minutes=5)
                timestamps = [t for t in timestamps if t > cutoff]
                timestamps.append(now)
                self._responses[ip] = timestamps
                if len(timestamps) > 100:
                    log_security_event(
                        "security.suspicious_activity",
                        severity="warning",
                        ip=ip,
                        extra={
                            "count": len(timestamps),
                            "window_minutes": 5,
                            "status_code": response.status_code,
                        },
                    )
        return response
