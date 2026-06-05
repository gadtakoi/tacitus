"""Tests for suspicious activity detection."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.security_logging import SuspiciousActivityMiddleware


@pytest.mark.anyio
async def test_suspicious_activity_triggered() -> None:
    """Test suspicious activity is logged after >100 401 responses from same IP."""
    middleware = SuspiciousActivityMiddleware(app=MagicMock())

    # Simulate 102 failed requests from the same IP
    for _ in range(102):
        request = MagicMock()
        request.headers = {"x-forwarded-for": "10.0.0.1"}
        request.client = None
        response = MagicMock()
        response.status_code = 401
        call_next = AsyncMock(return_value=response)
        await middleware.dispatch(request, call_next)

    with patch("app.core.security_logging.log_security_event") as mock_log:
        # 103rd request should trigger suspicious activity
        request = MagicMock()
        request.headers = {"x-forwarded-for": "10.0.0.1"}
        request.client = None
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
async def test_no_suspicious_activity_for_200() -> None:
    """Test successful responses do not trigger suspicious activity."""
    middleware = SuspiciousActivityMiddleware(app=MagicMock())

    with patch("app.core.security_logging.log_security_event") as mock_log:
        for _ in range(105):
            request = MagicMock()
            request.headers = {}
            request.client = MagicMock()
            request.client.host = "10.0.0.2"
            response = MagicMock()
            response.status_code = 200
            call_next = AsyncMock(return_value=response)
            await middleware.dispatch(request, call_next)

        mock_log.assert_not_called()
