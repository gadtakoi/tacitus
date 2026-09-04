"""Tests for password length bounds and for not echoing submitted values.

An unbounded password field handed the raw string to passlib, which refuses
anything over its own ``MAX_PASSWORD_SIZE`` with ``PasswordSizeError``. Nothing
caught it, so ``POST /auth/login`` answered **500** for an oversized password.

The validation error itself was the second problem: FastAPI's default handler
returns an ``input`` field carrying what the client sent — for these two
endpoints, the password in plain text.
"""

import pytest
from httpx import AsyncClient
from passlib.utils import MAX_PASSWORD_SIZE
from pydantic import ValidationError

from app.schemas.auth import (
    LOGIN_PASSWORD_MAX_LENGTH,
    PASSWORD_MAX_LENGTH,
    LoginRequest,
    RegisterRequest,
)

_STRONG_PASSWORD = "Str0ng!Pass"


def test_login_bound_matches_passlib_limit() -> None:
    """The login bound tracks the largest password passlib will ever accept.

    Below it, no account created before the bound existed is locked out. Above
    it, passlib would raise ``PasswordSizeError`` — so rejecting at the schema
    turns a 500 into a 422.
    """
    assert LOGIN_PASSWORD_MAX_LENGTH == MAX_PASSWORD_SIZE


def test_registration_bound_is_stricter_than_login() -> None:
    """New passwords are capped tighter than what login must keep accepting."""
    assert PASSWORD_MAX_LENGTH < LOGIN_PASSWORD_MAX_LENGTH


@pytest.mark.parametrize(
    ("model", "limit"),
    [(RegisterRequest, PASSWORD_MAX_LENGTH), (LoginRequest, LOGIN_PASSWORD_MAX_LENGTH)],
    ids=["register", "login"],
)
def test_password_at_the_bound_is_accepted(model: type, limit: int) -> None:
    """A password of exactly the maximum length passes.

    Args:
        model: Schema under test.
        limit: Maximum length that schema allows.
    """
    password = "Aa1!" + "x" * (limit - 4)
    assert len(model(username="someone", password=password).password) == limit


@pytest.mark.parametrize(
    ("model", "limit"),
    [(RegisterRequest, PASSWORD_MAX_LENGTH), (LoginRequest, LOGIN_PASSWORD_MAX_LENGTH)],
    ids=["register", "login"],
)
def test_password_over_the_bound_is_rejected(model: type, limit: int) -> None:
    """One character past the maximum is a validation error, not a crash.

    Args:
        model: Schema under test.
        limit: Maximum length that schema allows.
    """
    password = "Aa1!" + "x" * (limit - 3)
    with pytest.raises(ValidationError):
        model(username="someone", password=password)


@pytest.mark.anyio
async def test_oversized_login_password_is_rejected_not_a_server_error(
    client: AsyncClient,
) -> None:
    """An oversized login password answers 422 instead of 500.

    Before the bound existed this reached ``verify_password`` and passlib
    raised ``PasswordSizeError``, which nothing handled.
    """
    response = await client.post(
        "/auth/login",
        json={"username": "someone", "password": "A" * (MAX_PASSWORD_SIZE + 1)},
    )

    assert response.status_code == 422


@pytest.mark.anyio
@pytest.mark.parametrize(
    "endpoint",
    ["/auth/register", "/auth/login"],
)
async def test_validation_errors_do_not_echo_the_password(
    client: AsyncClient,
    endpoint: str,
) -> None:
    """The rejected password never appears in the response body.

    Args:
        client: Async HTTP client bound to the application.
        endpoint: Auth endpoint that takes a password.
    """
    secret = "SuperSecret-DoNotEcho-1!"
    response = await client.post(
        endpoint,
        json={"username": "x", "password": secret * 500},
    )

    assert response.status_code == 422
    assert secret not in response.text
    assert "input" not in response.text


@pytest.mark.anyio
async def test_validation_errors_still_say_what_went_wrong(client: AsyncClient) -> None:
    """Dropping the echo keeps the error actionable: field and reason remain."""
    response = await client.post(
        "/auth/register",
        json={"username": "someone", "password": "short"},
    )

    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert error["loc"] == ["body", "password"]
    assert "at least 8 characters" in error["msg"]
    assert "short" not in response.text


@pytest.mark.anyio
async def test_a_valid_registration_still_works(client: AsyncClient) -> None:
    """The bounds do not get in the way of a normal password."""
    response = await client.post(
        "/auth/register",
        json={"username": "boundsok", "password": _STRONG_PASSWORD},
    )

    assert response.status_code == 201
