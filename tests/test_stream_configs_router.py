"""Tests for the /stream-configs endpoints.

The stream configuration layer existed as a repository and a service for a long
time with no router wired to it. These tests cover the HTTP contract that was
added to expose it: status codes, the response shape, and the crop rules the
video crop worker depends on.

The endpoints share a prefix boundary with the MediaMTX proxy in
``test_streams_router.py``; that the two do not shadow each other is asserted
here explicitly.
"""

import pytest
from httpx import AsyncClient

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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestAuthentication:
    """Every endpoint is behind the same bearer token as the rest of the API."""

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/stream-configs"),
            ("POST", "/stream-configs"),
            ("GET", "/stream-configs/cam1"),
            ("PATCH", "/stream-configs/cam1"),
            ("DELETE", "/stream-configs/cam1"),
        ],
    )
    async def test_anonymous_access_is_rejected(
        self, client: AsyncClient, method: str, path: str
    ) -> None:
        response = await client.request(method, path, json={})
        assert response.status_code == 401


class TestCreate:
    """POST /stream-configs."""

    async def test_creates_with_crop_disabled_by_default(self, client: AsyncClient) -> None:
        """A new configuration must never start rewriting recordings by itself."""
        token = await _token(client, "creator")

        response = await client.post("/stream-configs", json={"name": "cam1"}, headers=_auth(token))

        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "cam1"
        assert body["source"] == "publisher"
        assert body["crop_enabled"] is False
        assert body["crop_top_percent"] == 0
        assert body["crop_bottom_percent"] == 0

    async def test_creates_with_crop_settings(self, client: AsyncClient) -> None:
        token = await _token(client, "creator2")

        response = await client.post(
            "/stream-configs",
            json={
                "name": "cam1",
                "crop_enabled": True,
                "crop_top_percent": 15,
                "crop_bottom_percent": 20,
            },
            headers=_auth(token),
        )

        assert response.status_code == 201
        body = response.json()
        assert body["crop_enabled"] is True
        assert body["crop_top_percent"] == 15
        assert body["crop_bottom_percent"] == 20

    async def test_duplicate_name_conflicts(self, client: AsyncClient) -> None:
        token = await _token(client, "creator3")
        await client.post("/stream-configs", json={"name": "cam1"}, headers=_auth(token))

        response = await client.post("/stream-configs", json={"name": "cam1"}, headers=_auth(token))

        assert response.status_code == 409

    @pytest.mark.parametrize(
        "payload",
        [
            {"name": "cam1", "crop_top_percent": 91},
            {"name": "cam1", "crop_bottom_percent": 91},
            {"name": "cam1", "crop_top_percent": -1},
            {"name": "cam1", "crop_top_percent": 50, "crop_bottom_percent": 50},
        ],
    )
    async def test_percentages_outside_the_allowed_range_are_rejected(
        self, client: AsyncClient, payload: dict
    ) -> None:
        token = await _token(client, "creator4")

        response = await client.post("/stream-configs", json=payload, headers=_auth(token))

        assert response.status_code == 422

    async def test_crop_leaving_too_little_of_the_frame_is_rejected(
        self, client: AsyncClient
    ) -> None:
        """Each edge is within its own bound; together they take too much."""
        token = await _token(client, "creator5")

        response = await client.post(
            "/stream-configs",
            json={"name": "cam1", "crop_top_percent": 60, "crop_bottom_percent": 30},
            headers=_auth(token),
        )
        assert response.status_code == 201, "90% removed is the documented limit"

        response = await client.post(
            "/stream-configs",
            json={"name": "cam2", "crop_top_percent": 60, "crop_bottom_percent": 31},
            headers=_auth(token),
        )
        assert response.status_code == 422


class TestList:
    """GET /stream-configs."""

    async def test_lists_stored_configurations(self, client: AsyncClient) -> None:
        token = await _token(client, "lister")
        await client.post("/stream-configs", json={"name": "cam1"}, headers=_auth(token))
        await client.post("/stream-configs", json={"name": "cam2"}, headers=_auth(token))

        response = await client.get("/stream-configs", headers=_auth(token))

        assert response.status_code == 200
        names = [item["name"] for item in response.json()]
        assert names == ["cam1", "cam2"]

    async def test_response_carries_no_unexpected_fields(self, client: AsyncClient) -> None:
        """Guards against a model change leaking internals to clients."""
        token = await _token(client, "lister2")
        await client.post("/stream-configs", json={"name": "cam1"}, headers=_auth(token))

        response = await client.get("/stream-configs", headers=_auth(token))

        assert set(response.json()[0]) == {
            "name",
            "source",
            "record_enabled",
            "crop_enabled",
            "crop_top_percent",
            "crop_bottom_percent",
            "created_at",
            "updated_at",
        }


class TestGet:
    """GET /stream-configs/{name}."""

    async def test_returns_the_stored_configuration(self, client: AsyncClient) -> None:
        token = await _token(client, "getter")
        await client.post(
            "/stream-configs",
            json={"name": "cam1", "crop_enabled": True, "crop_top_percent": 15},
            headers=_auth(token),
        )

        response = await client.get("/stream-configs/cam1", headers=_auth(token))

        assert response.status_code == 200
        assert response.json()["crop_top_percent"] == 15

    async def test_unknown_name_is_404(self, client: AsyncClient) -> None:
        token = await _token(client, "getter2")

        response = await client.get("/stream-configs/nope", headers=_auth(token))

        assert response.status_code == 404

    async def test_does_not_shadow_the_mediamtx_proxy(self, client: AsyncClient) -> None:
        """``/streams/{path:path}`` is a catch-all and must stay separate.

        Registering the configuration CRUD under ``/streams`` would have made
        every nested route unreachable.
        """
        token = await _token(client, "getter3")
        await client.post("/stream-configs", json={"name": "cam1"}, headers=_auth(token))

        response = await client.get("/stream-configs/cam1", headers=_auth(token))

        assert response.status_code == 200
        assert response.json()["name"] == "cam1"


class TestUpdate:
    """PATCH /stream-configs/{name}."""

    async def test_updates_crop_settings(self, client: AsyncClient) -> None:
        token = await _token(client, "updater")
        await client.post("/stream-configs", json={"name": "cam1"}, headers=_auth(token))

        response = await client.patch(
            "/stream-configs/cam1",
            json={"crop_enabled": True, "crop_top_percent": 15, "crop_bottom_percent": 20},
            headers=_auth(token),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["crop_enabled"] is True
        assert body["crop_top_percent"] == 15
        assert body["crop_bottom_percent"] == 20

    async def test_omitted_fields_keep_their_stored_value(self, client: AsyncClient) -> None:
        token = await _token(client, "updater2")
        await client.post(
            "/stream-configs",
            json={"name": "cam1", "crop_enabled": True, "crop_top_percent": 15},
            headers=_auth(token),
        )

        response = await client.patch(
            "/stream-configs/cam1", json={"crop_bottom_percent": 20}, headers=_auth(token)
        )

        assert response.json()["crop_top_percent"] == 15
        assert response.json()["crop_enabled"] is True

    async def test_partial_update_is_checked_against_stored_values(
        self, client: AsyncClient
    ) -> None:
        """The combined limit cannot be judged from the request alone."""
        token = await _token(client, "updater3")
        await client.post(
            "/stream-configs",
            json={"name": "cam1", "crop_top_percent": 60, "crop_bottom_percent": 20},
            headers=_auth(token),
        )

        # 40 is a legal value on its own, and illegal on top of a stored 60.
        response = await client.patch(
            "/stream-configs/cam1", json={"crop_bottom_percent": 40}, headers=_auth(token)
        )

        assert response.status_code == 422
        stored = await client.get("/stream-configs/cam1", headers=_auth(token))
        assert stored.json()["crop_bottom_percent"] == 20

    async def test_repeating_the_same_update_is_harmless(self, client: AsyncClient) -> None:
        """A retried request must not accumulate a change."""
        token = await _token(client, "updater4")
        await client.post("/stream-configs", json={"name": "cam1"}, headers=_auth(token))
        payload = {"crop_enabled": True, "crop_top_percent": 15, "crop_bottom_percent": 20}

        first = await client.patch("/stream-configs/cam1", json=payload, headers=_auth(token))
        second = await client.patch("/stream-configs/cam1", json=payload, headers=_auth(token))

        assert first.status_code == second.status_code == 200
        assert first.json()["crop_top_percent"] == second.json()["crop_top_percent"] == 15

    async def test_unknown_name_is_404(self, client: AsyncClient) -> None:
        token = await _token(client, "updater5")

        response = await client.patch(
            "/stream-configs/nope", json={"crop_enabled": True}, headers=_auth(token)
        )

        assert response.status_code == 404

    async def test_disabling_the_crop_keeps_the_percentages(self, client: AsyncClient) -> None:
        """The worker restores the full frame; the numbers stay for a later toggle."""
        token = await _token(client, "updater6")
        await client.post(
            "/stream-configs",
            json={"name": "cam1", "crop_enabled": True, "crop_top_percent": 15},
            headers=_auth(token),
        )

        response = await client.patch(
            "/stream-configs/cam1", json={"crop_enabled": False}, headers=_auth(token)
        )

        assert response.json()["crop_enabled"] is False
        assert response.json()["crop_top_percent"] == 15


class TestDelete:
    """DELETE /stream-configs/{name}."""

    async def test_removes_the_configuration(self, client: AsyncClient) -> None:
        token = await _token(client, "deleter")
        await client.post("/stream-configs", json={"name": "cam1"}, headers=_auth(token))

        response = await client.delete("/stream-configs/cam1", headers=_auth(token))

        assert response.status_code == 204
        assert (await client.get("/stream-configs/cam1", headers=_auth(token))).status_code == 404

    async def test_deleting_twice_is_404(self, client: AsyncClient) -> None:
        token = await _token(client, "deleter2")
        await client.post("/stream-configs", json={"name": "cam1"}, headers=_auth(token))
        await client.delete("/stream-configs/cam1", headers=_auth(token))

        response = await client.delete("/stream-configs/cam1", headers=_auth(token))

        assert response.status_code == 404
