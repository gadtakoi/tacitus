"""Authentication and authorization service."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# In-memory user storage for MVP
_users: dict[str, dict[str, Any]] = {}


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
    to_encode.update({"exp": expire})
    token = jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Decode and validate JWT access token.

    Args:
        token: JWT token string.

    Returns:
        Decoded payload or None if invalid.
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return payload
    except JWTError as e:
        logger.warning("JWT decode failed", extra={"error": str(e)})
        return None


def register_user(username: str, password: str) -> dict[str, Any]:
    """Register a new user.

    Args:
        username: Unique username.
        password: Plain text password.

    Returns:
        User data dict.

    Raises:
        AuthError: If username already exists.
    """
    if username in _users:
        raise AuthError("Username already registered")
    user = {"username": username, "hashed_password": hash_password(password)}
    _users[username] = user
    logger.info("User registered", extra={"username": username})
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
