"""Rate limiting configuration.

Rate-limit counters are stored in the backend configured by
``TACITUS_RATE_LIMIT_STORAGE_URI``:

- ``memory://`` — per-process memory storage (default for local development).
  Counters are lost on restart and are not shared across instances.
- ``redis://host:port`` — shared Redis storage (recommended for production and
  multi-instance deployments). Persistence and cross-instance consistency are
  provided by Redis.

The key function prefers the authenticated user (JWT ``sub``) and falls back to
client IP so limits apply per user or per IP.
"""

from fastapi import Request
from slowapi import Limiter

from app.core.config import settings
from app.core.ip import get_client_ip


def get_user_or_ip(request: Request) -> str:
    """Return user identifier for rate limiting.

    Prefers authenticated username from JWT, falls back to client IP.

    Note:
        This helper is synchronous because ``Limiter.key_func`` must be a
        regular callable. JWT decoding here is intentionally independent of
        the database-backed blacklist; for rate-limiting purposes only the
        token signature and ``sub`` claim are checked.
    """
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        # Import locally to avoid circular dependencies at module load time.
        from jose import JWTError, jwt

        token = auth.split(" ", 1)[1]
        try:
            payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        except JWTError:
            payload = None
        if payload:
            user = payload.get("sub")
            if user:
                return f"user:{user}"
    return f"ip:{get_client_ip(request)}"


# ``storage_uri`` is resolved by the ``limits`` library. For Redis make sure
# the ``redis`` package is installed (declared in ``pyproject.toml``).
limiter = Limiter(
    key_func=get_user_or_ip,
    storage_uri=settings.rate_limit_storage_uri,
)
