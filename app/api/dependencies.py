"""API dependencies."""

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ip import get_client_ip
from app.core.security_logging import log_security_event
from app.db.engine import get_db_session
from app.services.auth_service import decode_access_token, is_user_active

security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> str:
    """Verify JWT token and return current username.

    The token is decoded and then checked against the database-backed
    revocation list. Requires an async database session because blacklist
    lookups now hit PostgreSQL.

    Args:
        request: FastAPI request.
        credentials: Authorization header credentials.
        session: Database session.

    Returns:
        Username from token.

    Raises:
        HTTPException: 401 if token is missing or invalid.
        AppError: If a database error occurs while checking the token status.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    payload = await decode_access_token(session, token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username: str | None = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # A valid signature only proves the token was issued, not that the account
    # behind it still exists and may be used. Without this check disabling an
    # account would take effect no earlier than its access token expired — and
    # never at all, since rotation keeps issuing fresh ones. The row is read
    # through the session already open for the revocation check above, and only
    # one column of it.
    if not await is_user_active(session, username):
        log_security_event(
            "auth.inactive_user",
            severity="warning",
            ip=get_client_ip(request),
            user=username,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is disabled",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return username
