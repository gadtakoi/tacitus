"""Tests for production startup guardrails.

``validate_startup_settings`` is the last line of defence against booting the
service with a development configuration in production: debug output, default
MinIO credentials, a short JWT secret or a wildcard CORS origin. Every one of
these must abort startup rather than silently degrade security at runtime.
"""

import pytest

from app.core import config as config_module
from app.core.config import validate_startup_settings


@pytest.fixture
def production_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put ``settings`` into a valid production configuration.

    Individual tests then break exactly one field, so a failure points at the
    guardrail under test instead of at leftover defaults.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    settings = config_module.settings
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "jwt_secret", "x" * 32)
    monkeypatch.setattr(settings, "s3_access_key", "prod-access-key")
    monkeypatch.setattr(settings, "s3_secret_key", "prod-secret-key")
    monkeypatch.setattr(settings, "cors_origins", "https://app.example.com")
    monkeypatch.setattr(settings, "mtx_api_password", "prod-mediamtx-password")


def test_valid_production_settings_pass(production_settings: None) -> None:
    """A correctly configured production environment starts up."""
    validate_startup_settings()


def test_debug_rejected_in_production(
    production_settings: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Debug mode aborts startup in production.

    With ``debug`` enabled the generic exception handler returns the raw
    exception text to clients and the database engine echoes every statement,
    bind parameters included, into the logs.
    """
    monkeypatch.setattr(config_module.settings, "debug", True)
    with pytest.raises(RuntimeError, match="Debug mode must be disabled"):
        validate_startup_settings()


def test_short_jwt_secret_rejected_in_production(
    production_settings: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A JWT secret shorter than 32 characters aborts startup."""
    monkeypatch.setattr(config_module.settings, "jwt_secret", "too-short")
    with pytest.raises(RuntimeError, match="JWT secret"):
        validate_startup_settings()


@pytest.mark.parametrize("field", ["s3_access_key", "s3_secret_key"])
def test_default_minio_credentials_rejected_in_production(
    production_settings: None,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    """Default MinIO credentials abort startup, whichever half is left over.

    Args:
        production_settings: Valid production configuration fixture.
        monkeypatch: Pytest monkeypatch fixture.
        field: Credential field reset to the shipped default.
    """
    monkeypatch.setattr(config_module.settings, field, "minioadmin")
    with pytest.raises(RuntimeError, match="Default MinIO credentials"):
        validate_startup_settings()


def test_default_mediamtx_credentials_rejected_in_production(
    production_settings: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MediaMTX development password aborts startup.

    That password is what keeps anonymous clients from publishing to and
    reading from the RTSP server, so shipping it to production would leave the
    media plane effectively open.
    """
    monkeypatch.setattr(config_module.settings, "mtx_api_password", "dev-api-change-me")
    with pytest.raises(RuntimeError, match="Default MediaMTX API credentials"):
        validate_startup_settings()


def test_wildcard_cors_rejected_in_production(
    production_settings: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wildcard CORS origin aborts startup, even alongside real origins."""
    monkeypatch.setattr(
        config_module.settings,
        "cors_origins",
        "https://app.example.com, *",
    )
    with pytest.raises(RuntimeError, match="Wildcard CORS origin"):
        validate_startup_settings()


def test_development_settings_are_not_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Outside production the insecure development defaults are allowed.

    The local stack runs on ``minioadmin``/``minioadmin`` with debug enabled;
    the guardrail must not make development impossible.
    """
    settings = config_module.settings
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "jwt_secret", "dev-jwt-secret-change-me")
    monkeypatch.setattr(settings, "s3_access_key", "minioadmin")
    monkeypatch.setattr(settings, "s3_secret_key", "minioadmin")
    monkeypatch.setattr(settings, "cors_origins", "*")
    monkeypatch.setattr(settings, "mtx_api_password", "dev-api-change-me")

    validate_startup_settings()
