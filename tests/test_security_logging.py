"""Tests for security logging."""

from unittest.mock import MagicMock, patch

from app.core.security_logging import log_security_event


@patch("app.core.security_logging.logger")
def test_log_security_event(mock_logger: MagicMock) -> None:
    """Test structured security event logging."""
    log_security_event(
        "auth.login.success",
        severity="info",
        ip="127.0.0.1",
        user="testuser",
        extra={"request_id": "abc123"},
    )
    mock_logger.info.assert_called_once()
    call_args = mock_logger.info.call_args
    assert call_args[0][0] == "Security event"
    assert call_args[1]["extra"]["event_type"] == "auth.login.success"
    assert call_args[1]["extra"]["severity"] == "info"
    assert call_args[1]["extra"]["ip"] == "127.0.0.1"
    assert call_args[1]["extra"]["user"] == "testuser"
    assert call_args[1]["extra"]["request_id"] == "abc123"


@patch("app.core.security_logging.logger")
def test_log_security_event_warning(mock_logger: MagicMock) -> None:
    """Test security event with warning severity."""
    log_security_event(
        "auth.login.failed",
        severity="warning",
        ip="10.0.0.1",
        user="attacker",
    )
    mock_logger.warning.assert_called_once()
