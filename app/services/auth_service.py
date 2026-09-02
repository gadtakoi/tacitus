"""Authentication and authorization service.

This module contains the business logic for user registration, login,
brute-force protection, JWT lifecycle and token revocation. All persistent
state is stored in PostgreSQL through the repository layer.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.security_logging import log_security_event
from app.db.repositories.auth import (
    AuthRepositoryError,
    FailedLoginAttemptRepository,
    RefreshTokenRepository,
    TokenBlacklistRepository,
    UserRepository,
)

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# Brute-force protection constants.
BRUTE_FORCE_WINDOW_MINUTES = 15
BRUTE_FORCE_MAX_ATTEMPTS = 5
# Global backstop: attempts for one username from ANY IP within the window.
# Prevents credential stuffing across many IPs while the per-(username, IP)
# lock stays narrow enough not to lock out legitimate users.
BRUTE_FORCE_GLOBAL_MAX_ATTEMPTS = 20


class AuthError(Exception):
    """Business-level authentication error."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def hash_password(password: str) -> str:
    """Hash a plain text password using pbkdf2_sha256.

    Args:
        password: Plain text password.

    Returns:
        Passlib hash string.
    """
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain text password against a stored hash.

    Args:
        plain_password: Password provided by the user.
        hashed_password: Hash stored in the database.

    Returns:
        True if the password matches the hash.
    """
    return pwd_context.verify(plain_password, hashed_password)


def _hash_token(token: str) -> str:
    """Return a deterministic SHA-256 hash of a token string.

    Args:
        token: Raw JWT string.

    Returns:
        Hex digest of the token hash.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _extract_jti(payload: dict[str, Any]) -> str | None:
    """Extract the JWT ID from a decoded payload.

    Args:
        payload: Decoded JWT payload.

    Returns:
        JTI value or None if missing.
    """
    return payload.get("jti")


def create_access_token(data: dict[str, Any]) -> str:
    """Create a JWT access token.

    The token includes a unique ``jti`` claim so it can be individually
    revoked before its natural expiration.

    Args:
        data: Payload to encode. Typically contains ``sub`` (username).

    Returns:
        JWT token string.
    """
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_expiration_minutes)
    to_encode.update(
        {
            "exp": expire,
            "type": "access",
            "jti": str(uuid.uuid4()),
        },
    )
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(data: dict[str, Any]) -> str:
    """Create a JWT refresh token.

    The token carries a unique ``jti``. Without it the payload would be just
    ``sub``/``exp``/``type``, which is fully determined by the user and the
    second the token was minted: rotating twice within the same second
    produced a byte-identical token, its hash collided with the row that had
    just been revoked, and the client was handed a token that was already
    dead.

    Args:
        data: Payload to encode. Typically contains ``sub`` (username).

    Returns:
        JWT refresh token string.
    """
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_expiration_days)
    to_encode.update(
        {
            "exp": expire,
            "type": "refresh",
            "jti": str(uuid.uuid4()),
        },
    )
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


async def decode_access_token(session: AsyncSession, token: str) -> dict[str, Any] | None:
    """Decode and validate a JWT access token.

    Args:
        session: Database session for blacklist lookup.
        token: JWT token string.

    Returns:
        Decoded payload or None if the token is invalid, expired or revoked.
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        logger.warning("JWT decode failed", extra={"error": str(exc)})
        return None

    if payload.get("type") != "access":
        return None

    jti = _extract_jti(payload)
    if jti is None:
        # Access tokens created after this refactor must contain a jti claim.
        return None

    if await is_token_revoked(session, jti):
        return None

    return payload


def username_from_access_token(token: str) -> str | None:
    """Extract the ``sub`` claim from an access token without revocation checks.

    Unlike :func:`decode_access_token` this helper does NOT consult the
    token blacklist. It is intended for cases where the username is needed
    from a token that is about to be revoked (e.g. logout): after the ``jti``
    is blacklisted, the blacklist-aware decoder would reject the same token.

    Args:
        token: JWT access token string.

    Returns:
        Username or None if the token cannot be decoded.
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        logger.warning("JWT decode failed", extra={"error": str(exc)})
        return None

    if payload.get("type") != "access":
        return None

    username: str | None = payload.get("sub")
    return username


async def decode_refresh_token(token: str) -> dict[str, Any] | None:
    """Decode and validate a JWT refresh token.

    Args:
        token: JWT refresh token string.

    Returns:
        Decoded payload or None if invalid or expired.
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        logger.warning("Refresh token decode failed", extra={"error": str(exc)})
        return None

    if payload.get("type") != "refresh":
        return None

    return payload


# Run revoked-token cleanup approximately every N logout operations per
# process. Keeps the blacklist table bounded without a separate scheduler.
_REVOKE_CLEANUP_INTERVAL = 50
_revoke_counter = 0


async def revoke_access_token(session: AsyncSession, token: str) -> None:
    """Revoke an access token before its expiration.

    Periodically also removes blacklist entries whose tokens have already
    expired naturally — they can no longer be replayed, so keeping them only
    grows the table.

    Args:
        session: Database session.
        token: Access token to revoke.

    Raises:
        AuthError: If the token does not contain a ``jti`` claim.
    """
    global _revoke_counter

    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        logger.warning("Cannot revoke token: decode failed", extra={"error": str(exc)})
        return

    jti = _extract_jti(payload)
    if jti is None:
        raise AuthError("Token does not contain a jti claim and cannot be revoked")

    expires_at_raw = payload.get("exp")
    if expires_at_raw is None:
        expires_at = datetime.now(UTC) + timedelta(minutes=settings.jwt_expiration_minutes)
    else:
        expires_at = datetime.fromtimestamp(expires_at_raw, tz=UTC)

    repo = TokenBlacklistRepository(session)
    try:
        await repo.add(jti, expires_at)

        # Best-effort periodic cleanup of blacklist entries for tokens whose
        # lifetime has already passed. Process-local counter: in multi-instance
        # deployments each worker cleans up at its own pace.
        _revoke_counter += 1
        if _revoke_counter >= _REVOKE_CLEANUP_INTERVAL:
            _revoke_counter = 0
            cleaned = await repo.cleanup_expired()
            if cleaned:
                logger.info(
                    "Cleaned up expired revoked tokens",
                    extra={"count": cleaned},
                )
    except (IntegrityError, DBAPIError) as exc:
        await session.rollback()
        logger.exception("Database error while revoking access token")
        raise AppError("Failed to revoke token", status_code=500) from exc


async def is_token_revoked(session: AsyncSession, jti: str) -> bool:
    """Check whether an access token JTI is revoked.

    Args:
        session: Database session.
        jti: JWT ID to check.

    Returns:
        True if the JTI is present in the blacklist.
    """
    repo = TokenBlacklistRepository(session)
    try:
        return await repo.is_revoked(jti)
    except DBAPIError as exc:
        logger.exception("Database error while checking token revocation")
        raise AppError("Failed to validate token", status_code=500) from exc


async def store_refresh_token(session: AsyncSession, token: str, username: str) -> None:
    """Persist a refresh token for the given user.

    Args:
        session: Database session.
        token: Raw refresh token string.
        username: Username of the token owner.

    Raises:
        AuthError: If the user does not exist.
        AppError: On unexpected database errors.
    """
    user_repo = UserRepository(session)
    user = await user_repo.get_by_username(username)
    if user is None:
        raise AuthError(f"User '{username}' not found")

    payload = await decode_refresh_token(token)
    if payload is None:
        raise AuthError("Invalid refresh token")

    expires_at_raw = payload.get("exp")
    if expires_at_raw is None:
        expires_at = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_expiration_days)
    else:
        expires_at = datetime.fromtimestamp(expires_at_raw, tz=UTC)

    token_hash = _hash_token(token)
    refresh_repo = RefreshTokenRepository(session)
    try:
        await refresh_repo.create(user.id, token_hash, expires_at)
        # Best-effort cleanup of every token past its expiry, not just this
        # user's: an expired token can no longer be exchanged, so keeping the
        # row only grows the table.
        await refresh_repo.cleanup_expired()
    except AuthRepositoryError as exc:
        # Every token carries a unique jti, so a hash collision means the token
        # is not what we think it is. Continuing would hand the client a token
        # that is not persisted — and therefore unusable for refresh — so this
        # must fail loudly instead of being logged and ignored.
        logger.error("Refresh token hash collision", extra={"error": str(exc)})
        raise AppError("Failed to store refresh token", status_code=500) from exc
    except (IntegrityError, DBAPIError) as exc:
        await session.rollback()
        logger.exception("Database error while storing refresh token")
        raise AppError("Failed to store refresh token", status_code=500) from exc


async def get_refresh_token(
    session: AsyncSession,
    token: str,
) -> dict[str, Any] | None:
    """Retrieve refresh token data if it exists and is still valid.

    Args:
        session: Database session.
        token: Raw refresh token string.

    Returns:
        Dictionary with ``sub`` (username), ``exp`` (expiration) and
        ``already_revoked`` (True when the token was previously revoked —
        a strong indicator of token theft) if the token exists, otherwise
        None for unknown tokens.

    Raises:
        AppError: On unexpected database errors.
    """
    token_hash = _hash_token(token)
    repo = RefreshTokenRepository(session)
    try:
        stored = await repo.get_by_token_hash(token_hash)
    except DBAPIError as exc:
        logger.exception("Database error while reading refresh token")
        raise AppError("Failed to validate refresh token", status_code=500) from exc

    if stored is None:
        return None
    if stored.revoked_at is not None:
        # Known but revoked: return a marker so the caller can treat this as
        # token reuse rather than a plain invalid token.
        return {"sub": stored.user.username, "exp": stored.expires_at, "already_revoked": True}
    if datetime.now(UTC) > stored.expires_at:
        return None

    return {"sub": stored.user.username, "exp": stored.expires_at, "already_revoked": False}


async def rotate_refresh_token(
    session: AsyncSession,
    old_token: str,
) -> tuple[str, str]:
    """Rotate a refresh token: revoke the old one and issue a new pair.

    Refresh token rotation limits the lifetime of any single refresh token
    to one use. Reuse detection is built in:

    - If the presented token is already revoked, it is assumed stolen: ALL
      refresh tokens of the user are revoked (token family disabled), a
      security event is recorded and an ``AuthError`` is raised.
    - On success a brand new access + refresh token pair is returned and
      persisted.

    Args:
        session: Database session.
        old_token: Raw refresh token string presented by the client.

    Returns:
        Tuple ``(username, new_refresh_token)``.

    Raises:
        AuthError: If the token is invalid, expired, or reuse was detected.
        AppError: On unexpected database errors.
    """
    stored = await get_refresh_token(session, old_token)
    if stored is None:
        raise AuthError("Invalid or expired refresh token")

    username = stored["sub"]
    if stored.get("already_revoked"):
        # Reuse of a revoked token: assume compromise, kill the whole family.
        logger.warning(
            "Refresh token reuse detected; revoking all tokens for user",
            extra={"user": username},
        )
        await _revoke_all_user_tokens(session, username)
        log_security_event(
            "auth.refresh.reuse_detected",
            severity="warning",
            user=username,
        )
        raise AuthError("Refresh token reuse detected; all sessions revoked")

    # Retire the old refresh token FIRST. ``revoke`` is a conditional update on
    # ``revoked_at IS NULL``, so of several requests racing with the same token
    # exactly one gets ``True`` — and only that one is allowed to mint a new
    # pair. Issuing the new token first would hand every racer a valid session.
    token_hash = _hash_token(old_token)
    repo = RefreshTokenRepository(session)
    try:
        revoked = await repo.revoke(token_hash)
    except (IntegrityError, DBAPIError) as exc:
        await session.rollback()
        logger.exception("Database error while rotating refresh token")
        raise AppError("Failed to rotate refresh token", status_code=500) from exc

    if not revoked:
        # The token was spent between the lookup above and this update: another
        # request rotated it, or it is being replayed. Treat it as compromise.
        logger.warning("Concurrent refresh token rotation detected", extra={"user": username})
        await _revoke_all_user_tokens(session, username)
        log_security_event(
            "auth.refresh.reuse_detected",
            severity="warning",
            user=username,
        )
        raise AuthError("Refresh token reuse detected; all sessions revoked")

    new_refresh_token = create_refresh_token({"sub": username})
    await store_refresh_token(session, new_refresh_token, username)

    return username, new_refresh_token


async def _revoke_all_user_tokens(session: AsyncSession, username: str) -> None:
    """Revoke every active refresh token belonging to the user.

    Args:
        session: Database session.
        username: Owner of the tokens.

    Raises:
        AppError: On unexpected database errors.
    """
    user_repo = UserRepository(session)
    try:
        user = await user_repo.get_by_username(username)
        if user is None:
            return
        refresh_repo = RefreshTokenRepository(session)
        revoked = await refresh_repo.revoke_all_for_user(user.id)
        logger.warning(
            "Revoked all refresh tokens for user",
            extra={"user": username, "count": revoked},
        )
    except (IntegrityError, DBAPIError) as exc:
        await session.rollback()
        logger.exception("Database error while revoking user tokens")
        raise AppError("Failed to revoke tokens", status_code=500) from exc


async def clear_refresh_token(session: AsyncSession, token: str) -> bool:
    """Revoke a refresh token (logout).

    Args:
        session: Database session.
        token: Raw refresh token string.

    Returns:
        True if this call revoked an active token, False if the token was
        unknown or had already been revoked.
    """
    token_hash = _hash_token(token)
    repo = RefreshTokenRepository(session)
    try:
        return await repo.revoke(token_hash)
    except (IntegrityError, DBAPIError) as exc:
        await session.rollback()
        logger.exception("Database error while clearing refresh token")
        raise AppError("Failed to clear refresh token", status_code=500) from exc


async def is_brute_force_locked(
    session: AsyncSession,
    username: str,
    client_ip: str | None = None,
) -> bool:
    """Check if an account is temporarily locked due to failed login attempts.

    Two thresholds protect against different attack shapes:

    - Per ``(username, ip)``: ``BRUTE_FORCE_MAX_ATTEMPTS`` failures from the
      same address lock that pair. A distributed attacker cannot lock out a
      legitimate user logging in from their own IP.
    - Per ``username`` (global): ``BRUTE_FORCE_GLOBAL_MAX_ATTEMPTS`` failures
      from any address lock the account entirely — a backstop against
      credential stuffing spread across many IPs.

    Args:
        session: Database session.
        username: Username to check.
        client_ip: Client IP address, if known. When ``None`` only the global
            threshold applies.

    Returns:
        True if the account is temporarily locked.
    """
    repo = FailedLoginAttemptRepository(session)
    cutoff = datetime.now(UTC) - timedelta(minutes=BRUTE_FORCE_WINDOW_MINUTES)
    try:
        if client_ip is not None:
            per_ip_attempts = await repo.count_recent(username, cutoff, ip_address=client_ip)
            if per_ip_attempts >= BRUTE_FORCE_MAX_ATTEMPTS:
                return True
        global_attempts = await repo.count_recent(username, cutoff)
        if global_attempts >= BRUTE_FORCE_GLOBAL_MAX_ATTEMPTS:
            return True
    except DBAPIError as exc:
        logger.exception("Database error while checking brute-force lock")
        raise AppError("Failed to check account lock", status_code=500) from exc

    return False


async def record_failed_login(session: AsyncSession, username: str, client_ip: str) -> None:
    """Record a failed login attempt.

    Args:
        session: Database session.
        username: Username that failed to log in.
        client_ip: Client IP address.
    """
    repo = FailedLoginAttemptRepository(session)
    try:
        await repo.record(username, client_ip)
        # Cleanup attempts older than the brute-force window to keep the
        # table small.
        cutoff = datetime.now(UTC) - timedelta(minutes=BRUTE_FORCE_WINDOW_MINUTES)
        await repo.cleanup_old(cutoff)
    except (IntegrityError, DBAPIError) as exc:
        await session.rollback()
        logger.exception("Database error while recording failed login")
        raise AppError("Failed to record login attempt", status_code=500) from exc

    log_security_event(
        "auth.login.failed",
        severity="warning",
        ip=client_ip,
        user=username,
    )


async def register_user(
    session: AsyncSession,
    username: str,
    password: str,
    client_ip: str,
) -> dict[str, str]:
    """Register a new user.

    Args:
        session: Database session.
        username: Unique username.
        password: Plain text password.
        client_ip: Client IP address.

    Returns:
        User data dict with username.

    Raises:
        AuthError: If username already exists.
        AppError: On unexpected database errors.
    """
    repo = UserRepository(session)
    try:
        user = await repo.create(username, hash_password(password))
    except AuthRepositoryError as exc:
        raise AuthError(str(exc)) from exc
    except (IntegrityError, DBAPIError) as exc:
        await session.rollback()
        logger.exception("Database error while registering user")
        raise AppError("Failed to register user", status_code=500) from exc

    log_security_event("auth.register", ip=client_ip, user=username)
    return {"username": user.username}


async def authenticate_user(
    session: AsyncSession,
    username: str,
    password: str,
) -> dict[str, Any] | None:
    """Authenticate user by username and password.

    Args:
        session: Database session.
        username: Username.
        password: Plain text password.

    Returns:
        User data dict if authenticated, None otherwise.

    Raises:
        AppError: On unexpected database errors.
    """
    repo = UserRepository(session)
    try:
        user = await repo.get_by_username(username)
    except DBAPIError as exc:
        logger.exception("Database error while authenticating user")
        raise AppError("Failed to authenticate user", status_code=500) from exc

    if user is None:
        return None
    if not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None

    return {"username": user.username}


async def get_user(session: AsyncSession, username: str) -> dict[str, Any] | None:
    """Get user by username.

    Args:
        session: Database session.
        username: Username.

    Returns:
        User data dict or None if not found.

    Raises:
        AppError: On unexpected database errors.
    """
    repo = UserRepository(session)
    try:
        user = await repo.get_by_username(username)
    except DBAPIError as exc:
        logger.exception("Database error while fetching user")
        raise AppError("Failed to fetch user", status_code=500) from exc

    if user is None:
        return None
    return {"username": user.username}
