"""Tests that login does not leak whether an account exists.

``authenticate_user`` used to return early when the username was unknown,
skipping the password check entirely. pbkdf2 is deliberately slow, so that
early return made the response measurably faster and let an attacker
enumerate valid usernames without ever guessing a password.

The property under test is the one that produces the timing behaviour: every
login attempt performs exactly one password verification, whatever the
outcome. Asserting that directly is deterministic, whereas asserting on
wall-clock durations would flake on a loaded machine.
"""

from unittest.mock import patch

import pytest

from app.db.repositories.auth import UserRepository
from app.services.auth_service import (
    _DUMMY_PASSWORD_HASH,
    authenticate_user,
    hash_password,
    verify_password,
)

_PASSWORD = "Str0ng!Pass"


async def _create_user(session, username: str, *, active: bool = True) -> None:
    """Create a user with the shared test password.

    Args:
        session: Database session.
        username: Username to create.
        active: Whether the account is enabled.
    """
    user = await UserRepository(session).create(username, hash_password(_PASSWORD))
    if not active:
        user.is_active = False
        await session.flush()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("username", "password", "setup", "expected"),
    [
        ("ghost", _PASSWORD, None, None),
        ("disabled", _PASSWORD, "inactive", None),
        ("active", "Wr0ng!Pass", "active", None),
        ("active", _PASSWORD, "active", {"username": "active"}),
    ],
    ids=["unknown account", "disabled account", "wrong password", "successful login"],
)
async def test_every_outcome_costs_one_password_verification(
    db_session,
    username: str,
    password: str,
    setup: str | None,
    expected: dict[str, str] | None,
) -> None:
    """Failure and success alike perform a single password check.

    ``wraps`` keeps the real pbkdf2 work happening, so the call is not just
    counted but actually paid for — a mock that returned instantly would count
    the same while leaving the timing gap wide open.

    Args:
        db_session: Test database session.
        username: Username to authenticate.
        password: Password to present.
        setup: Which account to create beforehand, if any.
        expected: Expected return value of ``authenticate_user``.
    """
    if setup is not None:
        await _create_user(db_session, username, active=setup == "active")

    with patch(
        "app.services.auth_service.verify_password",
        wraps=verify_password,
    ) as verification:
        result = await authenticate_user(db_session, username, password)

    assert result == expected
    assert verification.call_count == 1, (
        "login must verify a password on every path, otherwise the response "
        "time reveals whether the account exists"
    )


@pytest.mark.anyio
async def test_unknown_account_verifies_against_the_dummy_hash(db_session) -> None:
    """The throwaway hash is what an unknown account is checked against."""
    with patch(
        "app.services.auth_service.verify_password",
        wraps=verify_password,
    ) as verification:
        await authenticate_user(db_session, "nobody-at-all", _PASSWORD)

    verification.assert_called_once_with(_PASSWORD, _DUMMY_PASSWORD_HASH)


def test_dummy_hash_shares_the_scheme_and_cost_of_real_hashes() -> None:
    """The decoy costs the same to verify as a stored password.

    A hardcoded or cheaper hash would still leave a timing gap, and would drift
    silently the day the hashing parameters change.
    """
    real = hash_password(_PASSWORD)

    scheme_dummy, rounds_dummy = _DUMMY_PASSWORD_HASH.split("$")[1:3]
    scheme_real, rounds_real = real.split("$")[1:3]

    assert scheme_dummy == scheme_real
    assert rounds_dummy == rounds_real
