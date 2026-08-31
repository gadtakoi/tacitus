"""Unit/integration tests for the PostgreSQL-backed repositories.

These tests run against the async test database configured in ``conftest.py``
and cover CRUD operations, uniqueness constraints and cleanup helpers.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.db.repositories.auth import (
    AuthRepositoryError,
    FailedLoginAttemptRepository,
    RefreshTokenRepository,
    SecurityEventRepository,
    TokenBlacklistRepository,
    UserRepository,
)


@pytest.mark.anyio
async def test_user_repository_create_and_get(db_session) -> None:
    """User can be created and retrieved by username."""
    repo = UserRepository(db_session)
    user = await repo.create("alice", "fake_hash")

    fetched = await repo.get_by_username("alice")
    assert fetched is not None
    assert fetched.id == user.id
    assert fetched.username == "alice"
    assert fetched.password_hash == "fake_hash"


@pytest.mark.anyio
async def test_user_repository_duplicate_username(db_session) -> None:
    """Creating a user with a duplicate username raises AuthRepositoryError."""
    repo = UserRepository(db_session)
    await repo.create("dup", "hash1")

    with pytest.raises(AuthRepositoryError):
        await repo.create("dup", "hash2")


@pytest.mark.anyio
async def test_user_repository_exists(db_session) -> None:
    """exists() reflects whether a username is already taken."""
    repo = UserRepository(db_session)
    assert await repo.exists("missing") is False

    await repo.create("bob", "hash")
    assert await repo.exists("bob") is True


@pytest.mark.anyio
async def test_refresh_token_repository_lifecycle(db_session) -> None:
    """Refresh tokens can be created, looked up, revoked and cleaned up."""
    user_repo = UserRepository(db_session)
    user = await user_repo.create("refresh_user", "hash")

    repo = RefreshTokenRepository(db_session)
    expires = datetime.now(UTC) + timedelta(days=7)
    await repo.create(user.id, "token_hash_1", expires)

    stored = await repo.get_by_token_hash("token_hash_1")
    assert stored is not None
    assert stored.user_id == user.id
    assert stored.user.username == "refresh_user"
    assert await repo.is_valid("token_hash_1") is True

    revoked = await repo.revoke("token_hash_1")
    assert revoked is True
    assert await repo.is_valid("token_hash_1") is False

    # Cleanup removes expired tokens.
    old = datetime.now(UTC) - timedelta(days=1)
    await repo.create(user.id, "token_hash_2", old)
    cleaned = await repo.cleanup_expired()
    assert cleaned == 1


@pytest.mark.anyio
async def test_refresh_token_repository_duplicate_hash(db_session) -> None:
    """Creating two tokens with the same hash raises AuthRepositoryError."""
    user_repo = UserRepository(db_session)
    user = await user_repo.create("dup_refresh", "hash")
    repo = RefreshTokenRepository(db_session)
    expires = datetime.now(UTC) + timedelta(days=7)

    await repo.create(user.id, "same_hash", expires)
    with pytest.raises(AuthRepositoryError):
        await repo.create(user.id, "same_hash", expires)


@pytest.mark.anyio
async def test_token_blacklist_repository(db_session) -> None:
    """Token blacklist supports add, idempotency, lookup and cleanup."""
    repo = TokenBlacklistRepository(db_session)
    expires = datetime.now(UTC) + timedelta(minutes=15)

    await repo.add("jti-1", expires)
    assert await repo.is_revoked("jti-1") is True
    assert await repo.is_revoked("jti-2") is False

    # Adding the same JTI again must be idempotent.
    await repo.add("jti-1", expires)
    assert await repo.is_revoked("jti-1") is True

    # Expired records can be cleaned up.
    past = datetime.now(UTC) - timedelta(minutes=1)
    await repo.add("jti-3", past)
    cleaned = await repo.cleanup_expired()
    assert cleaned == 1
    assert await repo.is_revoked("jti-3") is False


@pytest.mark.anyio
async def test_failed_login_attempt_repository(db_session) -> None:
    """Failed login attempts are recorded, counted and cleaned up."""
    repo = FailedLoginAttemptRepository(db_session)
    await repo.record("target", "10.0.0.1")
    await repo.record("target", "10.0.0.2")

    recent = datetime.now(UTC) - timedelta(minutes=10)
    assert await repo.count_recent("target", recent) == 2

    old = datetime.now(UTC) - timedelta(minutes=1)
    cleaned = await repo.cleanup_old(old)
    assert cleaned == 2

    assert await repo.count_recent("target", recent) == 0


@pytest.mark.anyio
async def test_failed_login_count_recent_filters_by_ip(db_session) -> None:
    """count_recent with ip_address counts only attempts from that IP."""
    repo = FailedLoginAttemptRepository(db_session)
    await repo.record("victim", "10.0.0.1")
    await repo.record("victim", "10.0.0.1")
    await repo.record("victim", "10.0.0.2")

    recent = datetime.now(UTC) - timedelta(minutes=10)

    # Per-IP count narrows to attempts from a single address.
    assert await repo.count_recent("victim", recent, ip_address="10.0.0.1") == 2
    assert await repo.count_recent("victim", recent, ip_address="10.0.0.2") == 1
    assert await repo.count_recent("victim", recent, ip_address="10.0.0.3") == 0
    # Without an IP the global count includes every attempt.
    assert await repo.count_recent("victim", recent) == 3


@pytest.mark.anyio
async def test_refresh_token_repository_revoke_all_for_user(db_session) -> None:
    """revoke_all_for_user disables every active token of the user."""
    user_repo = UserRepository(db_session)
    user = await user_repo.create("bulk_user", "hash")
    other = await user_repo.create("other_user", "hash")

    repo = RefreshTokenRepository(db_session)
    expires = datetime.now(UTC) + timedelta(days=7)
    await repo.create(user.id, "bulk_hash_1", expires)
    await repo.create(user.id, "bulk_hash_2", expires)
    await repo.create(other.id, "other_hash_1", expires)

    revoked = await repo.revoke_all_for_user(user.id)
    assert revoked == 2

    assert await repo.is_valid("bulk_hash_1") is False
    assert await repo.is_valid("bulk_hash_2") is False
    # Tokens of other users are untouched.
    assert await repo.is_valid("other_hash_1") is True

    # Already-revoked tokens are not counted on the second call.
    assert await repo.revoke_all_for_user(user.id) == 0


@pytest.mark.anyio
async def test_security_event_repository(db_session) -> None:
    """Security events are recorded, counted by IP and cleaned up."""
    repo = SecurityEventRepository(db_session)
    await repo.record(
        "auth.login.failed",
        ip_address="10.0.0.1",
        username="alice",
        details={"status_code": 401},
    )

    recent = datetime.now(UTC) - timedelta(minutes=10)
    assert await repo.count_recent_by_ip("10.0.0.1", recent) == 1

    old = datetime.now(UTC) - timedelta(minutes=1)
    cleaned = await repo.cleanup_old(old)
    assert cleaned == 1

    assert await repo.count_recent_by_ip("10.0.0.1", recent) == 0
