"""Authentication router."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.ip import get_client_ip
from app.core.rate_limit import limiter
from app.core.security_logging import log_security_event
from app.db.engine import get_db_session
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.services.auth_service import (
    AuthError,
    authenticate_user,
    clear_refresh_token,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    get_refresh_token,
    is_brute_force_locked,
    record_failed_login,
    register_user,
    revoke_access_token,
    store_refresh_token,
    username_from_access_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
@limiter.limit("3/hour")
async def auth_register(
    request: Request,
    body: RegisterRequest,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> UserResponse:
    """Register a new user.

    Args:
        request: FastAPI request.
        body: Registration credentials.
        session: Database session.

    Returns:
        Registration confirmation.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    client_ip = get_client_ip(request)
    logger.info("Registering user", extra={"request_id": request_id, "username": body.username})
    try:
        await register_user(session, body.username, body.password, client_ip)
    except AuthError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=e.message) from e
    return UserResponse(username=body.username, message="User registered successfully")


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def auth_login(
    request: Request,
    body: LoginRequest,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> TokenResponse:
    """Authenticate user and return JWT tokens.

    Args:
        request: FastAPI request.
        body: Login credentials.
        session: Database session.

    Returns:
        Access and refresh tokens.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    client_ip = get_client_ip(request)
    logger.info("Login attempt", extra={"request_id": request_id, "username": body.username})

    if await is_brute_force_locked(session, body.username):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Account temporarily locked due to too many failed attempts",
        )

    user = await authenticate_user(session, body.username, body.password)
    if not user:
        await record_failed_login(session, body.username, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    access_token = create_access_token({"sub": user["username"]})
    refresh_token = create_refresh_token({"sub": user["username"]})
    await store_refresh_token(session, refresh_token, user["username"])
    log_security_event(
        "auth.login.success",
        ip=client_ip,
        user=user["username"],
        extra={"request_id": request_id},
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
    )


@router.post("/refresh", response_model=TokenResponse)
async def auth_refresh(
    request: Request,
    body: RefreshRequest,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> TokenResponse:
    """Refresh JWT access token using a refresh token.

    Args:
        request: FastAPI request.
        body: Refresh request containing refresh token.
        session: Database session.

    Returns:
        New access token.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info("Token refresh attempt", extra={"request_id": request_id})

    payload = await decode_refresh_token(body.refresh_token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    stored = await get_refresh_token(session, body.refresh_token)
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    username = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    access_token = create_access_token({"sub": username})
    return TokenResponse(
        access_token=access_token,
        refresh_token=body.refresh_token,
        token_type="bearer",
    )


@router.post("/logout")
async def auth_logout(
    request: Request,
    body: LogoutRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, str]:
    """Logout user by revoking access and refresh tokens.

    Both tokens issued at login must be invalidated on logout:

    - The access token is revoked by putting its ``jti`` on the blacklist so
      it stops working immediately (access tokens are short-lived anyway).
    - The refresh token is revoked in the ``refresh_tokens`` table so it can
      no longer be exchanged for new access tokens. Without this step the
      refresh token would remain valid for ``jwt_refresh_expiration_days``.

    The username is extracted from the access token *before* revocation:
    ``decode_access_token`` checks the blacklist and would return ``None``
    for an already revoked token.

    Args:
        request: FastAPI request.
        body: Logout request containing the refresh token to revoke.
        credentials: Authorization header credentials.
        session: Database session.

    Returns:
        Logout confirmation.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Extract the username before revocation — after the jti is blacklisted
    # decode_access_token would reject the very same token.
    username = username_from_access_token(credentials.credentials)

    await revoke_access_token(session, credentials.credentials)
    refresh_revoked = await clear_refresh_token(session, body.refresh_token)
    if not refresh_revoked:
        # Already revoked, expired or unknown token: nothing to do, but log
        # it for auditing purposes.
        logger.warning(
            "Refresh token not found or already revoked during logout",
            extra={"request_id": request_id, "user": username},
        )

    log_security_event(
        "auth.token.revoked",
        ip=get_client_ip(request),
        user=username,
        extra={"request_id": request_id, "refresh_revoked": refresh_revoked},
    )
    return {"message": "Logged out successfully"}


@router.get("/me")
async def auth_me(
    request: Request,
    current_user: str = Depends(get_current_user),
) -> UserResponse:
    """Get current authenticated user.

    Args:
        request: FastAPI request.
        current_user: Authenticated username from JWT.

    Returns:
        Current username.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info("Fetching current user", extra={"request_id": request_id, "username": current_user})
    return UserResponse(username=current_user)
