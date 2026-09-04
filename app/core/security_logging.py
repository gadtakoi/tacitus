"""Dedicated security logging module.

Provides file-based structured security logging and middleware that detects
suspicious patterns (many 401/403 responses from a single IP) by storing
events in PostgreSQL.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.ip import get_client_ip
from app.db.engine import AsyncSessionLocal
from app.db.repositories.auth import SecurityEventRepository

logger = logging.getLogger("security")

# Window and threshold for detecting suspicious activity from a single IP.
SUSPICIOUS_WINDOW_MINUTES = 5
SUSPICIOUS_THRESHOLD = 100

# Events older than this are considered stale and can be cleaned up.
SECURITY_EVENT_RETENTION_DAYS = 7

# Run cleanup approximately every N events processed by this process.
_CLEANUP_INTERVAL = 100


def log_security_event(
    event_type: str,
    severity: str = "info",
    ip: str | None = None,
    user: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Log a structured security event to the security log file.

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
    """Middleware that detects brute-force-like patterns (401/403 spikes).

    For every 401/403 response the middleware persists a security event to
    PostgreSQL and checks whether the same IP has produced more than
    ``SUSPICIOUS_THRESHOLD`` such events within ``SUSPICIOUS_WINDOW_MINUTES``.
    Old events are cleaned up periodically to control table growth.
    """

    _event_counter: int = 0

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        """Track 401/403 responses and log suspicious activity.

        All database work in this middleware is best-effort: observability
        must never break request handling. If PostgreSQL is unavailable the
        original response is returned unchanged and the failure is logged.

        Args:
            request: Incoming request.
            call_next: Next handler in the middleware stack.

        Returns:
            Response object.
        """
        response = await call_next(request)

        if response.status_code not in (401, 403):
            return response

        ip = get_client_ip(request)
        now = datetime.now(UTC)

        try:
            async with AsyncSessionLocal() as session:
                repo = SecurityEventRepository(session)
                await repo.record(
                    event_type="security.response.failure",
                    ip_address=ip,
                    details={
                        "status_code": response.status_code,
                        "path": request.url.path,
                        "method": request.method,
                    },
                )

                cutoff = now - timedelta(minutes=SUSPICIOUS_WINDOW_MINUTES)
                count = await repo.count_recent_by_ip(ip, cutoff)

                if count > SUSPICIOUS_THRESHOLD:
                    details = {
                        "count": count,
                        "window_minutes": SUSPICIOUS_WINDOW_MINUTES,
                        "status_code": response.status_code,
                        "threshold": SUSPICIOUS_THRESHOLD,
                    }
                    log_security_event(
                        "security.suspicious_activity",
                        severity="warning",
                        ip=ip,
                        extra=details,
                    )
                    # Also persist the detection, so the audit trail is
                    # self-contained: the default log destination is stdout,
                    # where a spike can be lost entirely. Written only on the
                    # request that crosses the threshold — recording every
                    # subsequent failure would double the write volume exactly
                    # during an attack.
                    if count == SUSPICIOUS_THRESHOLD + 1:
                        await repo.record(
                            event_type="security.suspicious_activity",
                            ip_address=ip,
                            details=details,
                        )

                # Best-effort periodic cleanup of stale events. The counter is
                # process-local, so cleanup may happen more or less often in
                # multi-instance deployments, but the table stays bounded.
                SuspiciousActivityMiddleware._event_counter += 1
                if SuspiciousActivityMiddleware._event_counter >= _CLEANUP_INTERVAL:
                    SuspiciousActivityMiddleware._event_counter = 0
                    retention_cutoff = now - timedelta(days=SECURITY_EVENT_RETENTION_DAYS)
                    cleaned = await repo.cleanup_old(retention_cutoff)
                    if cleaned:
                        logger.info(
                            "Cleaned up stale security events",
                            extra={"count": cleaned},
                        )

                # Repositories stage work and leave the transaction to the
                # caller, so this session has to commit its own: the audit
                # trail is deliberately independent of the request's
                # transaction and must survive a failed request.
                await session.commit()
        except Exception:
            # Observability must never take down request handling: log the
            # failure and return the original response to the client.
            logger.exception(
                "Failed to persist security event; skipping suspicious activity tracking",
                extra={"ip": ip, "path": request.url.path},
            )

        return response
