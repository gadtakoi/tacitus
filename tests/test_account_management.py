"""Tests for account lifecycle: registration switch, disabling, password change.

The three belong together: closing registration makes the CLI the only way an
account appears, disabling is what takes one away, and a password change is what
ends the sessions of an account that stays. Each of them is only worth anything
if it takes effect on sessions that already exist — which is what most of the
tests here check.
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.db.repositories.auth import UserRepository
from app.services.auth_service import set_user_active, verify_password

_PASSWORD = "Str0ng!Pass"
_NEW_PASSWORD = "An0ther!Pass"


async def _register_and_login(client: AsyncClient, username: str) -> dict[str, str]:
    """Create an account and return its token pair."""
    await client.post("/auth/register", json={"username": username, "password": _PASSWORD})
    login = await client.post("/auth/login", json={"username": username, "password": _PASSWORD})
    assert login.status_code == 200, login.text
    return login.json()


class TestRegistrationSwitch:
    """``TACITUS_REGISTRATION_ENABLED`` closes the anonymous entry point."""

    @pytest.mark.anyio
    async def test_registration_is_open_by_default(self, client: AsyncClient) -> None:
        response = await client.post(
            "/auth/register",
            json={"username": "openuser", "password": _PASSWORD},
        )
        assert response.status_code == 201

    @pytest.mark.anyio
    async def test_disabled_registration_is_refused(
        self,
        client: AsyncClient,
        db_session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """403 and no account: the request must not half-succeed."""
        monkeypatch.setattr(settings, "registration_enabled", False)

        response = await client.post(
            "/auth/register",
            json={"username": "blockeduser", "password": _PASSWORD},
        )

        assert response.status_code == 403
        assert await UserRepository(db_session).get_by_username("blockeduser") is None

    @pytest.mark.anyio
    async def test_refusal_is_audited(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An attempt to register against a closed door is worth recording."""
        monkeypatch.setattr(settings, "registration_enabled", False)

        with patch("app.api.routers.auth.log_security_event") as mock_log:
            await client.post(
                "/auth/register",
                json={"username": "blockeduser", "password": _PASSWORD},
            )

        assert mock_log.call_args.args[0] == "auth.register.disabled"
        assert mock_log.call_args.kwargs["severity"] == "warning"

    @pytest.mark.anyio
    async def test_cli_path_still_creates_accounts(
        self,
        db_session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Closing the endpoint must not close the way accounts are made.

        ``scripts/init_project.py`` calls ``register_user`` directly; if the
        switch lived in the service, a deployed stack with registration off
        would have no way to create its first user at all.
        """
        from app.services.auth_service import register_user

        monkeypatch.setattr(settings, "registration_enabled", False)

        await register_user(db_session, "cliuser", _PASSWORD, None)

        assert await UserRepository(db_session).get_by_username("cliuser") is not None


class TestDisabledAccount:
    """``is_active`` has to hold for the whole session, not just at login."""

    @pytest.mark.anyio
    async def test_existing_access_token_stops_working(
        self,
        client: AsyncClient,
        db_session,
    ) -> None:
        tokens = await _register_and_login(client, "victim")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        assert (await client.get("/auth/me", headers=headers)).status_code == 200

        await set_user_active(db_session, "victim", False)

        response = await client.get("/auth/me", headers=headers)
        assert response.status_code == 401
        assert "disabled" in response.json()["detail"].lower()

    @pytest.mark.anyio
    async def test_rotation_cannot_extend_a_disabled_session(
        self,
        client: AsyncClient,
        db_session,
    ) -> None:
        """Refresh is what makes a session outlive its access token."""
        tokens = await _register_and_login(client, "victim")
        await set_user_active(db_session, "victim", False)

        response = await client.post(
            "/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )

        assert response.status_code == 401
        assert "disabled" in response.json()["detail"].lower()

    @pytest.mark.anyio
    async def test_disabling_revokes_the_token_family(
        self,
        client: AsyncClient,
        db_session,
    ) -> None:
        """Re-enabling must not resurrect the sessions that were cut off."""
        tokens = await _register_and_login(client, "victim")
        await set_user_active(db_session, "victim", False)
        await set_user_active(db_session, "victim", True)

        response = await client.post(
            "/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )

        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_login_is_possible_again_after_enabling(
        self,
        client: AsyncClient,
        db_session,
    ) -> None:
        await _register_and_login(client, "victim")
        await set_user_active(db_session, "victim", False)
        refused = await client.post(
            "/auth/login",
            json={"username": "victim", "password": _PASSWORD},
        )
        assert refused.status_code == 401

        await set_user_active(db_session, "victim", True)

        accepted = await client.post(
            "/auth/login",
            json={"username": "victim", "password": _PASSWORD},
        )
        assert accepted.status_code == 200

    @pytest.mark.anyio
    async def test_token_of_a_deleted_account_is_refused(
        self,
        client: AsyncClient,
        db_session,
    ) -> None:
        """A token can outlive the row it names; it must not outlive its access."""
        tokens = await _register_and_login(client, "ghost")
        user = await UserRepository(db_session).get_by_username("ghost")
        assert user is not None
        await db_session.delete(user)
        await db_session.commit()

        response = await client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_refusal_is_audited(self, client: AsyncClient, db_session) -> None:
        tokens = await _register_and_login(client, "victim")
        await set_user_active(db_session, "victim", False)

        with patch("app.api.dependencies.log_security_event") as mock_log:
            await client.get(
                "/auth/me",
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )

        assert mock_log.call_args.args[0] == "auth.inactive_user"

    @pytest.mark.anyio
    async def test_set_user_active_reports_a_missing_account(self, db_session) -> None:
        assert await set_user_active(db_session, "nobody", False) is False


class TestPasswordChange:
    """Changing a password ends every session the old one opened."""

    @pytest.mark.anyio
    async def test_password_is_replaced(self, client: AsyncClient, db_session) -> None:
        tokens = await _register_and_login(client, "changer")

        response = await client.post(
            "/auth/password",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
            json={"current_password": _PASSWORD, "new_password": _NEW_PASSWORD},
        )

        assert response.status_code == 200
        user = await UserRepository(db_session).get_by_username("changer")
        assert user is not None
        assert verify_password(_NEW_PASSWORD, user.password_hash) is True
        assert verify_password(_PASSWORD, user.password_hash) is False

    @pytest.mark.anyio
    async def test_old_credentials_stop_working(self, client: AsyncClient) -> None:
        tokens = await _register_and_login(client, "changer")
        await client.post(
            "/auth/password",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
            json={"current_password": _PASSWORD, "new_password": _NEW_PASSWORD},
        )

        old_login = await client.post(
            "/auth/login",
            json={"username": "changer", "password": _PASSWORD},
        )
        assert old_login.status_code == 401

        new_login = await client.post(
            "/auth/login",
            json={"username": "changer", "password": _NEW_PASSWORD},
        )
        assert new_login.status_code == 200

    @pytest.mark.anyio
    async def test_sessions_are_revoked(self, client: AsyncClient) -> None:
        """Both halves of the session that made the change are retired."""
        tokens = await _register_and_login(client, "changer")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        await client.post(
            "/auth/password",
            headers=headers,
            json={"current_password": _PASSWORD, "new_password": _NEW_PASSWORD},
        )

        assert (await client.get("/auth/me", headers=headers)).status_code == 401
        refresh = await client.post(
            "/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert refresh.status_code == 401

    @pytest.mark.anyio
    async def test_wrong_current_password_changes_nothing(
        self,
        client: AsyncClient,
        db_session,
    ) -> None:
        """A stolen token alone must not be enough to take over the account."""
        tokens = await _register_and_login(client, "changer")

        response = await client.post(
            "/auth/password",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
            json={"current_password": "Wr0ng!Pass", "new_password": _NEW_PASSWORD},
        )

        assert response.status_code == 400
        user = await UserRepository(db_session).get_by_username("changer")
        assert user is not None
        assert verify_password(_PASSWORD, user.password_hash) is True
        # The session that failed the check is still usable: a wrong guess is
        # not a reason to log the owner out.
        assert (
            await client.get(
                "/auth/me",
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
        ).status_code == 200

    @pytest.mark.anyio
    async def test_weak_new_password_is_refused(self, client: AsyncClient) -> None:
        """The change obeys the same policy as registration."""
        tokens = await _register_and_login(client, "changer")

        response = await client.post(
            "/auth/password",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
            json={"current_password": _PASSWORD, "new_password": "short"},
        )

        assert response.status_code == 422

    @pytest.mark.anyio
    async def test_unauthenticated_request_is_refused(self, client: AsyncClient) -> None:
        response = await client.post(
            "/auth/password",
            json={"current_password": _PASSWORD, "new_password": _NEW_PASSWORD},
        )
        assert response.status_code == 401

    @pytest.mark.anyio
    async def test_change_is_audited(self, client: AsyncClient) -> None:
        tokens = await _register_and_login(client, "changer")

        with patch("app.services.auth_service.log_security_event") as mock_log:
            await client.post(
                "/auth/password",
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
                json={"current_password": _PASSWORD, "new_password": _NEW_PASSWORD},
            )

        assert mock_log.call_args.args[0] == "auth.password.changed"
