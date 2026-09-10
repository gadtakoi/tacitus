"""Tests for the runtime configuration view (``GET /settings``).

The endpoint exists to answer "which environment variables is this process
running with?" from the browser. Its whole risk is disclosure, so most of what
is asserted here is what the response must *not* contain: no credential value,
no password inside a connection URL, and nothing at all for an anonymous
caller.

The classification test is the important one — it fails when a setting is added
to ``Settings`` without being classified, which is the only way a new secret
could reach this endpoint unnoticed.
"""

import json

import pytest
from httpx import AsyncClient

from app.core.config import Settings, settings
from app.services import settings_service
from app.services.settings_service import _redact_url_credentials, list_settings

_STRONG_PASSWORD = "Str0ng!Pass"

pytestmark = pytest.mark.anyio


async def _token(client: AsyncClient, username: str) -> str:
    """Register a user and return an access token.

    Args:
        client: Async HTTP client bound to the application.
        username: Username to create.

    Returns:
        Access token for the created user.
    """
    body = {"username": username, "password": _STRONG_PASSWORD}
    await client.post("/auth/register", json=body)
    login = await client.post("/auth/login", json=body)
    return login.json()["access_token"]


class TestClassification:
    """Every setting must be deliberately classified before it is displayed."""

    def test_every_settings_field_is_classified(self) -> None:
        # Adding a field to Settings without listing it in _FIELD_GROUPS is the
        # one way a new credential could slip into the response. The service
        # still masks it (fail-closed), but the omission has to be noticed.
        unclassified = set(Settings.model_fields) - set(settings_service._FIELD_GROUPS)
        assert unclassified == set(), (
            f"Unclassified settings: {sorted(unclassified)}. Add them to "
            "_FIELD_GROUPS and, if they carry credentials, to _SECRET_FIELDS."
        )

    def test_classification_has_no_stale_entries(self) -> None:
        stale = set(settings_service._FIELD_GROUPS) - set(Settings.model_fields)
        assert stale == set(), f"Settings no longer defined: {sorted(stale)}"

    def test_secret_and_url_fields_are_real_settings(self) -> None:
        known = set(Settings.model_fields)
        assert known >= settings_service._SECRET_FIELDS
        assert known >= settings_service._URL_CREDENTIAL_FIELDS

    def test_unclassified_field_is_treated_as_secret(self, monkeypatch) -> None:
        # Simulate a setting nobody classified: it must still be masked.
        groups = dict(settings_service._FIELD_GROUPS)
        del groups["mtx_api_user"]
        monkeypatch.setattr(settings_service, "_FIELD_GROUPS", groups)
        item = next(i for i in list_settings() if i.name == "TACITUS_MTX_API_USER")
        assert item.sensitive is True
        assert item.value is None
        assert item.group == settings_service._GROUP_OTHER


class TestDisclosure:
    """No secret value leaves the process through this projection."""

    def test_secret_fields_carry_no_value(self) -> None:
        by_name = {item.name: item for item in list_settings()}
        for field in settings_service._SECRET_FIELDS:
            item = by_name[f"TACITUS_{field.upper()}"]
            assert item.sensitive is True, field
            assert item.value is None, field

    def test_secret_values_appear_nowhere_in_the_payload(self) -> None:
        payload = json.dumps([item.model_dump() for item in list_settings()])
        for field in settings_service._SECRET_FIELDS:
            secret = str(getattr(settings, field))
            if secret:
                assert secret not in payload, field

    def test_is_set_distinguishes_configured_from_empty(self) -> None:
        by_name = {item.name: item for item in list_settings()}
        # The DSN is empty in tests: the UI needs to say "not configured"
        # without ever seeing the value.
        assert by_name["TACITUS_SENTRY_DSN"].is_set is False
        assert by_name["TACITUS_JWT_SECRET"].is_set is True

    def test_public_settings_are_shown(self) -> None:
        by_name = {item.name: item for item in list_settings()}
        item = by_name["TACITUS_LOG_LEVEL"]
        assert item.sensitive is False
        assert item.value == settings.log_level


class TestUrlRedaction:
    """Connection URLs keep their host, lose their password."""

    def test_password_is_removed_and_user_kept(self) -> None:
        redacted = _redact_url_credentials(
            "postgresql+asyncpg://tacitus:s3cret@postgres:5432/tacitus"
        )
        assert redacted == "postgresql+asyncpg://tacitus:***@postgres:5432/tacitus"
        assert "s3cret" not in redacted

    def test_url_without_credentials_is_unchanged(self) -> None:
        assert _redact_url_credentials("memory://") == "memory://"
        assert _redact_url_credentials("redis://redis:6379") == "redis://redis:6379"

    def test_password_only_credentials_are_removed(self) -> None:
        redacted = _redact_url_credentials("redis://:s3cret@redis:6379")
        assert redacted == "redis://***@redis:6379"

    def test_query_string_is_dropped(self) -> None:
        # Some drivers accept credentials in the query string; the whole query
        # is dropped rather than parsed for every possible spelling.
        assert _redact_url_credentials("postgresql://db/app?password=s3cret") == (
            "postgresql://db/app"
        )

    def test_unparseable_url_is_fully_masked(self) -> None:
        assert _redact_url_credentials("postgresql://user:pass@host:notaport/db") == "***"

    def test_empty_value_stays_empty(self) -> None:
        assert _redact_url_credentials("") == ""

    def test_database_url_is_redacted_in_the_projection(self) -> None:
        by_name = {item.name: item for item in list_settings()}
        item = by_name["TACITUS_DATABASE_URL"]
        assert item.sensitive is False
        assert item.value is not None
        assert ":***@" in item.value or "@" not in item.value


class TestEndpoint:
    """HTTP contract of ``GET /settings``."""

    async def test_anonymous_access_is_rejected(self, client: AsyncClient) -> None:
        response = await client.get("/settings")
        assert response.status_code == 401

    async def test_invalid_token_is_rejected(self, client: AsyncClient) -> None:
        response = await client.get("/settings", headers={"Authorization": "Bearer nope"})
        assert response.status_code == 401

    async def test_returns_grouped_settings(self, client: AsyncClient) -> None:
        token = await _token(client, "settings_reader")
        response = await client.get("/settings", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == len(Settings.model_fields)
        names = {item["name"] for item in items}
        assert "TACITUS_LOG_LEVEL" in names
        assert all(item["name"].startswith("TACITUS_") for item in items)
        assert all(item["group"] for item in items)

    async def test_response_body_contains_no_secret(self, client: AsyncClient) -> None:
        token = await _token(client, "settings_secrets")
        response = await client.get("/settings", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        for field in settings_service._SECRET_FIELDS:
            secret = str(getattr(settings, field))
            if secret:
                assert secret not in response.text, field
