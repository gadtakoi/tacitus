"""Repositories for authentication and security-related entities."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.db.models import (
    FailedLoginAttempt,
    RefreshToken,
    RevokedToken,
    SecurityEvent,
    User,
)
from app.db.repositories.base import BaseRepository, RepositoryError


class AuthRepositoryError(RepositoryError):
    """Error raised by authentication repositories."""


class UserRepository(BaseRepository):
    """Repository for user account persistence."""

    async def create(self, username: str, password_hash: str) -> User:
        """Create a new user.

        Args:
            username: Unique login name.
            password_hash: Passlib hash of the user's password.

        Returns:
            Created user instance.

        Raises:
            AuthRepositoryError: If username is already taken.
        """
        user = User(username=username, password_hash=password_hash)
        self._session.add(user)
        try:
            # Flush rather than commit: the INSERT has to reach the database so
            # the unique constraint speaks up, but the transaction belongs to
            # the caller. The session is left needing a rollback on failure,
            # which the service does.
            await self._session.flush()
        except IntegrityError as exc:
            raise AuthRepositoryError(f"Username '{username}' already exists") from exc
        return user

    async def get_by_username(self, username: str) -> User | None:
        """Find a user by login name.

        Args:
            username: Login name to look up.

        Returns:
            User instance or ``None`` if not found.
        """
        result = await self._session.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def exists(self, username: str) -> bool:
        """Check whether a user with the given username exists.

        Args:
            username: Login name to check.

        Returns:
            ``True`` if a user exists, ``False`` otherwise.
        """
        result = await self._session.execute(
            select(func.count(User.id)).where(User.username == username),
        )
        return bool(result.scalar_one())

    async def is_active(self, username: str) -> bool:
        """Check whether the account exists and may still authenticate.

        Selects the single column instead of loading the ``User`` row: this
        runs on every authenticated request, and ``User`` pulls its refresh
        tokens along through ``lazy="selectin"``.

        A username with no row answers ``False`` as well — a token outliving
        the account it names must stop working for the same reason a disabled
        account's token does.

        Args:
            username: Login name to check.

        Returns:
            ``True`` if the account exists and is active.
        """
        result = await self._session.execute(
            select(User.is_active).where(User.username == username),
        )
        return bool(result.scalar_one_or_none())

    async def update_password(self, username: str, password_hash: str) -> bool:
        """Replace the stored password hash of a user.

        Args:
            username: Login name of the account.
            password_hash: New passlib hash.

        Returns:
            ``True`` if a row was updated, ``False`` if no such user exists.
        """
        result = await self._session.execute(
            update(User).where(User.username == username).values(password_hash=password_hash),
        )
        return cast(CursorResult[Any], result).rowcount > 0

    async def set_active(self, username: str, is_active: bool) -> bool:
        """Enable or disable an account.

        Args:
            username: Login name of the account.
            is_active: Whether the account may authenticate.

        Returns:
            ``True`` if a row was updated, ``False`` if no such user exists.
        """
        result = await self._session.execute(
            update(User).where(User.username == username).values(is_active=is_active),
        )
        return cast(CursorResult[Any], result).rowcount > 0


class RefreshTokenRepository(BaseRepository):
    """Repository for refresh token lifecycle management."""

    async def create(
        self,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> RefreshToken:
        """Persist a new refresh token for a user.

        Args:
            user_id: UUID of the token owner.
            token_hash: SHA-256 hash of the raw token string.
            expires_at: UTC timestamp when the token expires.

        Returns:
            Created refresh token instance.

        Raises:
            AuthRepositoryError: If the same token hash already exists.
        """
        token = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        self._session.add(token)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise AuthRepositoryError("Refresh token hash already exists") from exc
        return token

    async def get_by_token_hash(self, token_hash: str) -> RefreshToken | None:
        """Find a refresh token by its hash.

        Args:
            token_hash: SHA-256 hash of the raw token string.

        Returns:
            Refresh token instance or ``None`` if not found.
        """
        result = await self._session.execute(
            select(RefreshToken)
            .options(selectinload(RefreshToken.user))
            .where(RefreshToken.token_hash == token_hash),
        )
        return result.scalar_one_or_none()

    async def is_valid(self, token_hash: str) -> bool:
        """Check whether a refresh token exists, is not revoked and not expired.

        Args:
            token_hash: SHA-256 hash of the raw token string.

        Returns:
            ``True`` if the token can be used for refresh.
        """
        now = datetime.now(UTC)
        result = await self._session.execute(
            select(func.count(RefreshToken.id)).where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > now,
            ),
        )
        return bool(result.scalar_one())

    async def revoke(self, token_hash: str) -> bool:
        """Mark an active refresh token as revoked.

        The update is conditional on ``revoked_at IS NULL`` and applied in a
        single statement, so it doubles as a mutex: when several requests
        present the same token concurrently, exactly one of them observes a
        non-zero ``rowcount``. Callers rely on that to distinguish a genuine
        rotation from replay of a token that was already spent.

        Args:
            token_hash: SHA-256 hash of the raw token string.

        Returns:
            ``True`` if this call revoked the token, ``False`` if it was
            unknown or had already been revoked.
        """
        result = await self._session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.token_hash == token_hash,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC)),
        )
        return cast(CursorResult[Any], result).rowcount > 0

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        """Revoke every active refresh token of a user.

        Used for token-reuse detection: when a revoked token is presented
        again, the whole token family is presumed compromised and disabled.

        Args:
            user_id: UUID of the token owner.

        Returns:
            Number of tokens that were revoked.
        """
        result = await self._session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC)),
        )
        return cast(CursorResult[Any], result).rowcount

    async def cleanup_expired(self, cutoff: datetime | None = None) -> int:
        """Delete refresh tokens that expired before the cutoff time.

        Args:
            cutoff: UTC timestamp; tokens with ``expires_at <= cutoff`` are
                removed. Defaults to the current time.

        Returns:
            Number of deleted records.
        """
        if cutoff is None:
            cutoff = datetime.now(UTC)
        result = await self._session.execute(
            delete(RefreshToken).where(RefreshToken.expires_at <= cutoff),
        )
        return cast(CursorResult[Any], result).rowcount


class TokenBlacklistRepository(BaseRepository):
    """Repository for revoked access tokens (JWT blacklist)."""

    async def add(self, jti: str, expires_at: datetime) -> RevokedToken:
        """Add a JWT ID to the blacklist.

        Args:
            jti: JWT ID claim of the revoked access token.
            expires_at: Original expiration time of the access token.

        Returns:
            Created blacklist record, or the existing one if already revoked.
        """
        # Try an upsert: if the JTI is already blacklisted keep the existing
        # row and do not fail.
        stmt = (
            insert(RevokedToken)
            .values(jti=jti, expires_at=expires_at)
            .on_conflict_do_nothing(index_elements=["jti"])
            .returning(RevokedToken)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is not None:
            return row

        # Conflict happened: fetch the existing row.
        existing = await self._session.execute(
            select(RevokedToken).where(RevokedToken.jti == jti),
        )
        return existing.scalar_one()

    async def is_revoked(self, jti: str) -> bool:
        """Check whether a JWT ID is blacklisted.

        Args:
            jti: JWT ID to check.

        Returns:
            ``True`` if the token has been revoked.
        """
        result = await self._session.execute(
            select(func.count(RevokedToken.id)).where(RevokedToken.jti == jti),
        )
        return bool(result.scalar_one())

    async def cleanup_expired(self, cutoff: datetime | None = None) -> int:
        """Remove blacklist records for tokens that already expired.

        Args:
            cutoff: UTC timestamp; records with ``expires_at <= cutoff`` are
                removed. Defaults to the current time.

        Returns:
            Number of deleted records.
        """
        if cutoff is None:
            cutoff = datetime.now(UTC)
        result = await self._session.execute(
            delete(RevokedToken).where(RevokedToken.expires_at <= cutoff),
        )
        return cast(CursorResult[Any], result).rowcount


class FailedLoginAttemptRepository(BaseRepository):
    """Repository for brute-force protection records."""

    async def record(self, username: str, ip_address: str | None = None) -> FailedLoginAttempt:
        """Persist a failed login attempt.

        Args:
            username: Login name used in the failed attempt.
            ip_address: Client IP address, if available.

        Returns:
            Created record.
        """
        attempt = FailedLoginAttempt(username=username, ip_address=ip_address)
        self._session.add(attempt)
        await self._session.flush()
        return attempt

    async def count_recent(
        self,
        username: str,
        cutoff: datetime,
        ip_address: str | None = None,
    ) -> int:
        """Count failed login attempts for a user since the cutoff time.

        When ``ip_address`` is provided only attempts from that address are
        counted, so a distributed attacker cannot lock out a legitimate user
        by spraying failures from other IPs.

        Args:
            username: Login name to check.
            cutoff: UTC timestamp; older attempts are ignored.
            ip_address: Optional client IP to narrow the count to.

        Returns:
            Number of matching attempts.
        """
        conditions = [
            FailedLoginAttempt.username == username,
            FailedLoginAttempt.created_at > cutoff,
        ]
        if ip_address is not None:
            conditions.append(FailedLoginAttempt.ip_address == ip_address)
        result = await self._session.execute(
            select(func.count(FailedLoginAttempt.id)).where(*conditions),
        )
        return result.scalar_one()

    async def cleanup_old(self, cutoff: datetime) -> int:
        """Delete failed login attempts older than the cutoff.

        Args:
            cutoff: UTC timestamp; records with ``created_at <= cutoff`` are
                removed.

        Returns:
            Number of deleted records.
        """
        result = await self._session.execute(
            delete(FailedLoginAttempt).where(
                FailedLoginAttempt.created_at <= cutoff,
            ),
        )
        return cast(CursorResult[Any], result).rowcount


class SecurityEventRepository(BaseRepository):
    """Repository for security event auditing."""

    async def record(
        self,
        event_type: str,
        ip_address: str | None = None,
        username: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> SecurityEvent:
        """Persist a security event.

        Args:
            event_type: Event classifier, e.g. ``auth.login.failed``.
            ip_address: Client IP address, if available.
            username: Related username, if available.
            details: Arbitrary structured context stored as JSONB.

        Returns:
            Created security event record.
        """
        event = SecurityEvent(
            event_type=event_type,
            ip_address=ip_address,
            username=username,
            details=details,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def count_recent_by_ip(self, ip_address: str, cutoff: datetime) -> int:
        """Count security events from a single IP since the cutoff time.

        Args:
            ip_address: Client IP address to check.
            cutoff: UTC timestamp; older events are ignored.

        Returns:
            Number of matching events.
        """
        result = await self._session.execute(
            select(func.count(SecurityEvent.id)).where(
                SecurityEvent.ip_address == ip_address,
                SecurityEvent.created_at > cutoff,
            ),
        )
        return result.scalar_one()

    async def cleanup_old(self, cutoff: datetime) -> int:
        """Delete security events older than the cutoff.

        Args:
            cutoff: UTC timestamp; records with ``created_at <= cutoff`` are
                removed.

        Returns:
            Number of deleted records.
        """
        result = await self._session.execute(
            delete(SecurityEvent).where(SecurityEvent.created_at <= cutoff),
        )
        return cast(CursorResult[Any], result).rowcount
