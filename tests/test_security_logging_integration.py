"""Integration tests for security logging backed by PostgreSQL.

These tests verify that ``SuspiciousActivityMiddleware`` persists 401/403
responses to the ``security_events`` table and detects suspicious spikes.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from app.core.security_logging import SuspiciousActivityMiddleware
from app.db.models import SecurityEvent


@pytest.mark.anyio
async def test_security_event_persisted_on_401(db_session) -> None:
    """A single 401 response creates a security_event row."""
    middleware = SuspiciousActivityMiddleware(app=MagicMock())

    request = MagicMock()
    request.headers = {"x-forwarded-for": "10.0.0.1"}
    request.client = None
    request.url.path = "/auth/me"
    request.method = "GET"
    response = MagicMock()
    response.status_code = 401
    call_next = AsyncMock(return_value=response)

    await middleware.dispatch(request, call_next)

    result = await db_session.execute(
        select(SecurityEvent).where(SecurityEvent.ip_address == "10.0.0.1")
    )
    events = result.scalars().all()
    assert len(events) == 1
    assert events[0].event_type == "security.response.failure"
    assert events[0].details["status_code"] == 401


@pytest.mark.anyio
async def test_security_event_not_persisted_on_200(db_session) -> None:
    """Successful responses do not create security_event rows."""
    middleware = SuspiciousActivityMiddleware(app=MagicMock())

    request = MagicMock()
    request.headers = {}
    request.client = MagicMock()
    request.client.host = "10.0.0.2"
    request.url.path = "/health"
    request.method = "GET"
    response = MagicMock()
    response.status_code = 200
    call_next = AsyncMock(return_value=response)

    await middleware.dispatch(request, call_next)

    result = await db_session.execute(
        select(SecurityEvent).where(SecurityEvent.ip_address == "10.0.0.2")
    )
    events = result.scalars().all()
    assert len(events) == 0


@pytest.mark.anyio
async def test_suspicious_activity_spike_detected(db_session) -> None:
    """More than 100 failures from the same IP trigger suspicious_activity."""
    middleware = SuspiciousActivityMiddleware(app=MagicMock())

    request = MagicMock()
    request.headers = {"x-forwarded-for": "10.0.0.3"}
    request.client = None
    request.url.path = "/auth/login"
    request.method = "POST"

    for _ in range(101):
        response = MagicMock()
        response.status_code = 401
        call_next = AsyncMock(return_value=response)
        await middleware.dispatch(request, call_next)

    result = await db_session.execute(
        select(SecurityEvent)
        .where(SecurityEvent.ip_address == "10.0.0.3")
        .where(SecurityEvent.event_type == "security.suspicious_activity")
    )
    events = result.scalars().all()
    assert len(events) >= 1
