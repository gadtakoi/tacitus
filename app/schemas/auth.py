"""Authentication schemas."""

from pydantic import BaseModel, field_validator


class RegisterRequest(BaseModel):
    """User registration request."""

    username: str
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

    username: str
    password: str


class TokenResponse(BaseModel):
    """JWT token response."""

    access_token: str
    refresh_token: str
    token_type: str


class RefreshRequest(BaseModel):
    """Token refresh request."""

    refresh_token: str


class UserResponse(BaseModel):
    """User information response."""

    username: str
    message: str | None = None
