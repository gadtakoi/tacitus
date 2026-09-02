"""Authentication schemas."""

from typing import Annotated

from pydantic import BaseModel, Field, field_validator

# Usernames are stored in VARCHAR(64) columns (``users.username`` and the
# audit tables). Without an upper bound the database rejects the INSERT with a
# DataError, which surfaces to the client as a 500 instead of a validation
# error, so the limit is enforced here.
USERNAME_MAX_LENGTH = 64

Username = Annotated[
    str,
    Field(
        min_length=3,
        max_length=USERNAME_MAX_LENGTH,
        pattern=r"^[a-zA-Z0-9._-]+$",
        description="Letters, digits, dot, underscore and hyphen only.",
    ),
]

# Login must not apply the registration charset rules: that would lock out any
# account created before those rules existed. Only the length is bounded, so a
# failed attempt can always be recorded in ``failed_login_attempts``.
LoginUsername = Annotated[str, Field(min_length=1, max_length=USERNAME_MAX_LENGTH)]


class RegisterRequest(BaseModel):
    """User registration request."""

    username: Username
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate password strength."""
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        if not any(c in r"!@#$%^&*()_+-=[]{}|;:,.<>?" for c in v):
            raise ValueError("Password must contain at least one special character")
        return v


class LoginRequest(BaseModel):
    """User login request."""

    username: LoginUsername
    password: str


class TokenResponse(BaseModel):
    """JWT token response."""

    access_token: str
    refresh_token: str
    token_type: str


class RefreshRequest(BaseModel):
    """Token refresh request."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """Logout request.

    Carries the refresh token so the server can revoke it alongside the
    access token. Without revocation the refresh token would remain usable
    until its natural expiration.
    """

    refresh_token: str


class UserResponse(BaseModel):
    """User information response."""

    username: str
    message: str | None = None
