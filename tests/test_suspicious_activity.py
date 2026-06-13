"""Unit tests for suspicious activity detection.

The middleware now persists events to PostgreSQL, so these unit tests mock
``SecurityEventRepository`` to avoid requiring a running database.
Integration tests that hit the real database live in
``test_security_logging_integration.py``.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.security_logging import SuspiciousActivityMiddleware


@pytest.fixture(autouse=True)
def reset_event_counter() -> None:
    """Reset the process-local event counter between tests."""
    SuspiciousActivityMiddleware._event_counter = 0
    yield
    SuspiciousActivityMiddleware._event_counter = 0


@pytest.mark.anyio
@patch("app.core.security_logging.SecurityEventRepository")
async def test_suspicious_activity_triggered(mock_repo_class: MagicMock) -> None:
    """Test suspicious activity is logged after >100 401 responses from same IP."""
    mock_repo = MagicMock()
    mock_repo_class.return_value = mock_repo
    mock_repo.record = AsyncMock()
    mock_repo.count_recent_by_ip = AsyncMock(return_value=101)
    mock_repo.cleanup_old = AsyncMock(return_value=0)

    middleware = SuspiciousActivityMiddleware(app=MagicMock())

    # Simulate 102 failed requests from the same IP.
    for _ in range(102):
        request = MagicMock()
        request.headers = {"x-forwarded-for": "10.0.0.1"}
        request.client = None
        request.url.path = "/auth/login"
        request.method = "POST"
        response = MagicMock()
        response.status_code = 401
        call_next = AsyncMock(return_value=response)
        await middleware.dispatch(request, call_next)

    with patch("app.core.security_logging.log_security_event") as mock_log:
        # 103rd request should trigger suspicious activity.
        request = MagicMock()
        request.headers = {"x-forwarded-for": "10.0.0.1"}
        request.client = None
        request.url.path = "/auth/login"
        request.method = "POST"
        response = MagicMock()
        response.status_code = 401
        call_next = AsyncMock(return_value=response)
        await middleware.dispatch(request, call_next)

        mock_log.assert_called_once()
        args, kwargs = mock_log.call_args
        assert args[0] == "security.suspicious_activity"
        assert kwargs["ip"] == "10.0.0.1"
        assert kwargs["severity"] == "warning"


@pytest.mark.anyio
@patch("app.core.security_logging.SecurityEventRepository")
async def test_no_suspicious_activity_for_200(mock_repo_class: MagicMock) -> None:
    """Test successful responses do not trigger suspicious activity."""
    mock_repo = MagicMock()
    mock_repo_class.return_value = mock_repo
    mock_repo.record = AsyncMock()
    mock_repo.count_recent_by_ip = AsyncMock(return_value=0)
    mock_repo.cleanup_old = AsyncMock(return_value=0)

    middleware = SuspiciousActivityMiddleware(app=MagicMock())

    with patch("app.core.security_logging.log_security_event") as mock_log:
        for _ in range(105):
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

        mock_log.assert_not_called()
    mock_repo.record.assert_not_called()
