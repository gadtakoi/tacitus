"""Tests for transaction boundaries around multi-step auth operations.

Repositories used to commit on every call, which made each step of a
multi-step operation durable on its own. Refresh-token rotation revoked the
old token in one transaction and stored the new one in another: a failure in
between left the user with a revoked token, no replacement, and nothing to
roll back to.

These tests use their own sessions with real commits rather than the
savepoint-wrapped ``db_session`` fixture — the property under test *is* commit
behaviour, and a fixture that rolls everything back cannot observe it. The
``async_engine`` fixture creates the schema per test and drops it afterwards,
so committing here stays isolated.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import AppError
from app.db.repositories.auth import (
    AuthRepositoryError,
    FailedLoginAttemptRepository,
    RefreshTokenRepository,
    UserRepository,
)
from app.services.auth_service import (
    _hash_token,
    create_refresh_token,
    hash_password,
    record_failed_login,
    rotate_refresh_token,
    store_refresh_token,
)


@pytest.fixture
def sessions(async_engine):
    """Return a sessionmaker bound to the test engine.

    Args:
        async_engine: Function-scoped engine with the schema created.

    Returns:
        Async sessionmaker producing independent, committing sessions.
    """
    return async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)


async def _seed_user_with_token(sessions, username: str) -> str:
    """Create a user and one stored refresh token, committed.

    Args:
        sessions: Sessionmaker fixture.
        username: Username to create.

    Returns:
        The raw refresh token belonging to the user.
    """
    async with sessions() as session:
        await UserRepository(session).create(username, hash_password("Str0ng!Pass"))
        await session.commit()

    token = create_refresh_token({"sub": username})
    async with sessions() as session:
        await store_refresh_token(session, token, username)
    return token


@pytest.mark.anyio
async def test_repository_writes_are_not_durable_until_the_service_commits(sessions) -> None:
    """A repository call alone leaves nothing behind after a rollback."""
    async with sessions() as session:
        await UserRepository(session).create("staged", "hash")
        await session.rollback()

    async with sessions() as session:
        assert await UserRepository(session).get_by_username("staged") is None


@pytest.mark.anyio
async def test_rotation_keeps_the_old_token_usable_when_the_new_one_cannot_be_stored(
    sessions,
) -> None:
    """Rotation is all-or-nothing.

    If the replacement token cannot be persisted, the presented token must
    survive: otherwise the user is logged out by an internal error, with no
    way to recover short of signing in again.
    """
    old_token = await _seed_user_with_token(sessions, "rotator")
    old_hash = _hash_token(old_token)

    async with sessions() as session:
        failing_create = AsyncMock(side_effect=AuthRepositoryError("storage failed"))
        with (
            patch.object(RefreshTokenRepository, "create", failing_create),
            pytest.raises(AppError),
        ):
            await rotate_refresh_token(session, old_token)

    # A fresh session sees only committed state.
    async with sessions() as session:
        assert await RefreshTokenRepository(session).is_valid(old_hash) is True


@pytest.mark.anyio
async def test_successful_rotation_retires_the_old_token(sessions) -> None:
    """The happy path still spends the presented token exactly once."""
    old_token = await _seed_user_with_token(sessions, "happy")
    old_hash = _hash_token(old_token)

    async with sessions() as session:
        username, new_token = await rotate_refresh_token(session, old_token)

    assert username == "happy"
    assert new_token != old_token

    async with sessions() as session:
        repo = RefreshTokenRepository(session)
        assert await repo.is_valid(old_hash) is False
        assert await repo.is_valid(_hash_token(new_token)) is True


@pytest.mark.anyio
async def test_failed_login_survives_the_rejected_request(sessions) -> None:
    """A failed attempt is durable even though the request ends as a 401.

    Brute-force protection counts rows in ``failed_login_attempts``. If the
    attempt were left to the request's transaction, the 401 would discard it
    and the lockout would never trigger.
    """
    async with sessions() as session:
        await record_failed_login(session, "victim", "10.0.0.9")
        # No commit here on purpose: the service is responsible for it.

    async with sessions() as session:
        cutoff = datetime.now(UTC) - timedelta(minutes=15)
        count = await FailedLoginAttemptRepository(session).count_recent("victim", cutoff)

    assert count == 1
