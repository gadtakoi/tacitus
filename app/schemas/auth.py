"""Authentication schemas."""

from pydantic import BaseModel


class RegisterRequest(BaseModel):
    """User registration request."""

    username: str
    password: str


class LoginRequest(BaseModel):
    """User login request."""

    username: str
    password: str


class TokenResponse(BaseModel):
    """JWT token response."""

    access_token: str
    token_type: str


class UserResponse(BaseModel):
    """User information response."""

    username: str
    message: str | None = None
