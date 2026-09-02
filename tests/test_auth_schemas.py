"""Tests for authentication request schema validation.

These exercise the pydantic layer directly, without a database, because that
is where the constraints live. The username bounds in particular are load
bearing: ``users.username`` and the audit tables are ``VARCHAR(64)``, so an
unbounded value reaches PostgreSQL and fails with a DataError, which the error
handlers turn into a 500 instead of a 422.
"""

import pytest
from pydantic import ValidationError

from app.schemas.auth import USERNAME_MAX_LENGTH, LoginRequest, RegisterRequest

_STRONG_PASSWORD = "Str0ng!Pass"


def test_register_accepts_a_normal_username() -> None:
    """A plain alphanumeric username passes validation."""
    assert RegisterRequest(username="alice", password=_STRONG_PASSWORD).username == "alice"


def test_register_accepts_allowed_punctuation() -> None:
    """Dot, underscore and hyphen are permitted inside a username."""
    request = RegisterRequest(username="a.b_c-d", password=_STRONG_PASSWORD)
    assert request.username == "a.b_c-d"


@pytest.mark.parametrize(
    ("label", "username"),
    [
        ("empty", ""),
        ("too short", "ab"),
        ("longer than the column", "x" * (USERNAME_MAX_LENGTH + 1)),
        ("whitespace", "al ice"),
        ("quote", "al'ice"),
        ("slash", "al/ice"),
        ("non-ascii", "алиса"),
    ],
)
def test_register_rejects_invalid_username(label: str, username: str) -> None:
    """Invalid usernames fail validation instead of reaching the database."""
    with pytest.raises(ValidationError):
        RegisterRequest(username=username, password=_STRONG_PASSWORD)


def test_register_accepts_username_at_the_column_limit() -> None:
    """The boundary value itself is still valid."""
    username = "x" * USERNAME_MAX_LENGTH
    assert RegisterRequest(username=username, password=_STRONG_PASSWORD).username == username


def test_login_bounds_username_length() -> None:
    """Login also bounds the length: failed attempts are written to VARCHAR(64)."""
    with pytest.raises(ValidationError):
        LoginRequest(username="x" * (USERNAME_MAX_LENGTH + 1), password="whatever")


def test_login_does_not_apply_the_registration_charset() -> None:
    """Accounts predating the charset rules must still be able to log in."""
    assert LoginRequest(username="old style user", password="whatever").username == (
        "old style user"
    )
