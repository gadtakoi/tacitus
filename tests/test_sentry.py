"""Tests for Sentry integration."""

from unittest.mock import patch

from app.core.sentry import _strip_sensitive_data, init_sentry


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


def test_strip_sensitive_data_filters_authorization_header() -> None:
    """The bearer token must never reach Sentry.

    Header names keep their original casing, so matching has to be
    case-insensitive — a lowercase-only comparison let ``Authorization``
    through untouched.
    """
    event = {
        "request": {
            "headers": {
                "Authorization": "Bearer eyJhbGci",
                "Cookie": "session=abc",
                "User-Agent": "curl/8.0",
            },
        },
    }
    result = _strip_sensitive_data(event, None)
    assert result is not None
    headers = result["request"]["headers"]
    assert headers["Authorization"] == "[FILTERED]"
    assert headers["Cookie"] == "[FILTERED]"
    assert headers["User-Agent"] == "curl/8.0"


def test_strip_sensitive_data_tolerates_missing_sections() -> None:
    """An event without request/extra sections passes through unchanged."""
    assert _strip_sensitive_data({}, None) == {}
    assert _strip_sensitive_data({"request": None, "extra": "not-a-dict"}, None) is not None


def test_version_matches_pyproject() -> None:
    """The runtime version and the packaging version stay in step.

    ``app.__version__`` is what Sentry reports as the release; the image does
    not install the project as a distribution, so package metadata is not
    available there. Two literals therefore exist, and this keeps them from
    drifting apart unnoticed.
    """
    import tomllib
    from pathlib import Path

    from app import __version__

    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert __version__ == pyproject["project"]["version"]


def test_sentry_release_carries_the_application_version() -> None:
    """The release string is derived, not hardcoded."""
    from app import __version__

    with (
        patch("app.core.sentry.sentry_sdk") as mock_sdk,
        patch("app.core.sentry.settings") as mock_settings,
    ):
        mock_settings.sentry_dsn = "https://key@o123.ingest.sentry.io/456"
        mock_settings.sentry_environment = "test"
        mock_settings.sentry_traces_sample_rate = 0.5
        init_sentry()

    assert mock_sdk.init.call_args.kwargs["release"] == f"tacitus@{__version__}"
