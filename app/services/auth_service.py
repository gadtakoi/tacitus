"""Authentication and authorization service."""

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings
from app.core.security_logging import log_security_event

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# In-memory user storage for MVP
_users: dict[str, dict[str, Any]] = {}

# Brute-force protection
_brute_lock = threading.Lock()
_failed_attempts: dict[str, list[datetime]] = {}
BRUTE_FORCE_WINDOW_MINUTES = 15
BRUTE_FORCE_MAX_ATTEMPTS = 5

# Token blacklist
_blacklist_lock = threading.Lock()
_token_blacklist: set[str] = set()

# Refresh token storage
_refresh_lock = threading.Lock()
_refresh_tokens: dict[str, dict[str, Any]] = {}


class AuthError(Exception):
    """Authentication error."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def hash_password(password: str) -> str:
    """Hash a plain text password."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain text password against a hash."""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict[str, Any]) -> str:
    """Create JWT access token.

    Args:
        data: Payload to encode.

    Returns:
        JWT token string.
    """
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_expiration_minutes)
    to_encode.update({"exp": expire, "type": "access"})
    token = jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token


def create_refresh_token(data: dict[str, Any]) -> str:
    """Create JWT refresh token.

    Args:
        data: Payload to encode.

    Returns:
        JWT refresh token string.
    """
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_expiration_days)
    to_encode.update({"exp": expire, "type": "refresh"})
    token = jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Decode and validate JWT access token.

    Args:
        token: JWT token string.

    Returns:
        Decoded payload or None if invalid or revoked.
    """
    if is_token_revoked(token):
        return None
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "access":
            return None
        return payload
    except JWTError as e:
        logger.warning("JWT decode failed", extra={"error": str(e)})
        return None


def decode_refresh_token(token: str) -> dict[str, Any] | None:
    """Decode and validate JWT refresh token.

    Args:
        token: JWT refresh token string.

    Returns:
        Decoded payload or None if invalid.
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "refresh":
            return None
        return payload
    except JWTError as e:
        logger.warning("Refresh token decode failed", extra={"error": str(e)})
        return None


def revoke_access_token(token: str) -> None:
    """Add access token to blacklist."""
    with _blacklist_lock:
        _token_blacklist.add(token)


def is_token_revoked(token: str) -> bool:
    """Check if access token is revoked."""
    with _blacklist_lock:
        return token in _token_blacklist


def store_refresh_token(token: str, username: str) -> None:
    """Store refresh token with expiration."""
    expire = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_expiration_days)
    with _refresh_lock:
        _refresh_tokens[token] = {"sub": username, "exp": expire}


def get_refresh_token(token: str) -> dict[str, Any] | None:
    """Retrieve refresh token data if valid."""
    with _refresh_lock:
        data = _refresh_tokens.get(token)
        if not data:
            return None
        if datetime.now(UTC) > data["exp"]:
            del _refresh_tokens[token]
            return None
        return data


def clear_refresh_token(token: str) -> None:
    """Remove refresh token from storage."""
    with _refresh_lock:
        _refresh_tokens.pop(token, None)


# Brute-force helpers


def is_brute_force_locked(username: str) -> bool:
    """Check if account is locked due to too many failed login attempts.

    Args:
        username: Username to check.

    Returns:
        True if account is temporarily locked.
    """
    now = datetime.now(UTC)
    with _brute_lock:
        attempts = _failed_attempts.get(username, [])
        cutoff = now - timedelta(minutes=BRUTE_FORCE_WINDOW_MINUTES)
        attempts = [t for t in attempts if t > cutoff]
        _failed_attempts[username] = attempts
        return len(attempts) >= BRUTE_FORCE_MAX_ATTEMPTS


def record_failed_login(username: str, client_ip: str) -> None:
    """Record a failed login attempt.

    Args:
        username: Username that failed to log in.
        client_ip: Client IP address.
    """
    now = datetime.now(UTC)
    with _brute_lock:
        _failed_attempts.setdefault(username, []).append(now)
    log_security_event(
        "auth.login.failed",
        severity="warning",
        ip=client_ip,
        user=username,
    )


def register_user(username: str, password: str, client_ip: str) -> dict[str, Any]:
    """Register a new user.

    Args:
        username: Unique username.
        password: Plain text password.
        client_ip: Client IP address.

    Returns:
        User data dict.

    Raises:
        AuthError: If username already exists.
    """
    if username in _users:
        raise AuthError("Username already registered")
    user = {"username": username, "hashed_password": hash_password(password)}
    _users[username] = user
    log_security_event("auth.register", ip=client_ip, user=username)
    return {"username": username}


def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    """Authenticate user by username and password.

    Args:
        username: Username.
        password: Plain text password.

    Returns:
        User data if authenticated, None otherwise.
    """
    user = _users.get(username)
    if not user:
        return None
    if not verify_password(password, user["hashed_password"]):
        return None
    return user


def get_user(username: str) -> dict[str, Any] | None:
    """Get user by username.

    Args:
        username: Username.

    Returns:
        User data or None.
    """
    return _users.get(username)
