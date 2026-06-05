"""Rate limiting configuration."""

from fastapi import Request
from slowapi import Limiter

from app.core.ip import get_client_ip
from app.services.auth_service import decode_access_token


def get_user_or_ip(request: Request) -> str:
    """Return user identifier for rate limiting.

    Prefers authenticated username from JWT, falls back to client IP.
    """
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1]
        payload = decode_access_token(token)
        if payload:
            user = payload.get("sub")
            if user:
                return f"user:{user}"
    return f"ip:{get_client_ip(request)}"


limiter = Limiter(key_func=get_client_ip)
