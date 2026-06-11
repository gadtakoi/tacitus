"""Repositories for authentication and security-related entities."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
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
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AuthRepositoryError(f"Username '{username}' already exists") from exc
        await self._session.refresh(user)
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
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AuthRepositoryError("Refresh token hash already exists") from exc
        await self._session.refresh(token)
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
        """Mark a refresh token as revoked.

        Args:
            token_hash: SHA-256 hash of the raw token string.

        Returns:
            ``True`` if a token was found and revoked, ``False`` otherwise.
        """
        token = await self.get_by_token_hash(token_hash)
        if not token:
            return False
        token.revoked_at = datetime.now(UTC)
        await self._session.commit()
        return True

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
            RefreshToken.__table__.delete().where(RefreshToken.expires_at <= cutoff),
        )
        await self._session.commit()
        return result.rowcount


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
        await self._session.commit()
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
            RevokedToken.__table__.delete().where(RevokedToken.expires_at <= cutoff),
        )
        await self._session.commit()
        return result.rowcount


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
        await self._session.commit()
        await self._session.refresh(attempt)
        return attempt

    async def count_recent(self, username: str, cutoff: datetime) -> int:
        """Count failed login attempts for a user since the cutoff time.

        Args:
            username: Login name to check.
            cutoff: UTC timestamp; older attempts are ignored.

        Returns:
            Number of matching attempts.
        """
        result = await self._session.execute(
            select(func.count(FailedLoginAttempt.id)).where(
                FailedLoginAttempt.username == username,
                FailedLoginAttempt.created_at > cutoff,
            ),
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
            FailedLoginAttempt.__table__.delete().where(
                FailedLoginAttempt.created_at <= cutoff,
            ),
        )
        await self._session.commit()
        return result.rowcount


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
        await self._session.commit()
        await self._session.refresh(event)
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
            SecurityEvent.__table__.delete().where(SecurityEvent.created_at <= cutoff),
        )
        await self._session.commit()
        return result.rowcount
