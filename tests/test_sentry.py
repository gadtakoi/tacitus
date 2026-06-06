"""Tests for Sentry integration."""

from unittest.mock import MagicMock, patch

from app.core.sentry import _strip_sensitive_data, init_sentry, set_sentry_user


def test_init_sentry_skips_when_dsn_empty() -> None:
    """init_sentry should do nothing when SENTRY_DSN is empty."""
    with patch("app.core.sentry.settings") as mock_settings:
        mock_settings.sentry_dsn = ""
        with patch("app.core.sentry.sentry_sdk.init") as mock_init:
            init_sentry()
            mock_init.assert_not_called()


def test_init_sentry_initializes_when_dsn_set() -> None:
    """init_sentry should call sentry_sdk.init when DSN is configured."""
    with patch("app.core.sentry.settings") as mock_settings:
        mock_settings.sentry_dsn = "https://key@o123.ingest.sentry.io/456"
        mock_settings.sentry_environment = "test"
        mock_settings.sentry_traces_sample_rate = 0.5
        with patch("app.core.sentry.sentry_sdk.init") as mock_init:
            init_sentry()
            mock_init.assert_called_once()
            call_kwargs = mock_init.call_args.kwargs
            assert call_kwargs["dsn"] == "https://key@o123.ingest.sentry.io/456"
            assert call_kwargs["environment"] == "test"
            assert call_kwargs["traces_sample_rate"] == 0.5
            assert "before_send" in call_kwargs


def test_strip_sensitive_data_filters_passwords() -> None:
    """_strip_sensitive_data should replace sensitive keys with [FILTERED]."""
    event = {
        "request": {
            "data": {"username": "alice", "password": "secret123"},
            "headers": {"Authorization": "Bearer token"},
        },
        "extra": {"token": "sensitive-token"},
    }
    result = _strip_sensitive_data(event, None)
    assert result is not None
    assert result["request"]["data"]["password"] == "[FILTERED]"
    assert result["request"]["data"]["username"] == "alice"
    assert result["extra"]["token"] == "[FILTERED]"


def test_set_sentry_user_sets_context() -> None:
    """set_sentry_user should set username and request_id in Sentry scope."""
    mock_scope = MagicMock()
    with patch("app.core.sentry.sentry_sdk.push_scope", return_value=mock_scope):
        set_sentry_user("alice", request_id="req-42")
        mock_scope.__enter__.return_value.set_user.assert_called_once_with({"username": "alice"})
        mock_scope.__enter__.return_value.set_tag.assert_called_once_with("request_id", "req-42")
