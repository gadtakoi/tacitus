"""Authentication router."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.dependencies import get_current_user
from app.core.rate_limit import limiter
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.services.auth_service import (
    AuthError,
    authenticate_user,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    get_refresh_token,
    is_brute_force_locked,
    record_failed_login,
    register_user,
    revoke_access_token,
    store_refresh_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)


def _get_client_ip(request: Request) -> str:
    return request.headers.get("x-forwarded-for") or (
        request.client.host if request.client else "unknown"
    )


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
@limiter.limit("3/hour")
async def auth_register(request: Request, body: RegisterRequest) -> UserResponse:
    """Register a new user.

    Args:
        request: FastAPI request.
        body: Registration credentials.

    Returns:
        Registration confirmation.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info("Registering user", extra={"request_id": request_id, "username": body.username})
    try:
        register_user(body.username, body.password)
    except AuthError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=e.message) from e
    return UserResponse(username=body.username, message="User registered successfully")


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def auth_login(request: Request, body: LoginRequest) -> TokenResponse:
    """Authenticate user and return JWT tokens.

    Args:
        request: FastAPI request.
        body: Login credentials.

    Returns:
        Access and refresh tokens.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    client_ip = _get_client_ip(request)
    logger.info("Login attempt", extra={"request_id": request_id, "username": body.username})

    if is_brute_force_locked(body.username):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Account temporarily locked due to too many failed attempts",
        )

    user = authenticate_user(body.username, body.password)
    if not user:
        record_failed_login(body.username, client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    access_token = create_access_token({"sub": user["username"]})
    refresh_token = create_refresh_token({"sub": user["username"]})
    store_refresh_token(refresh_token, user["username"])
    logger.info(
        "Login successful",
        extra={
            "request_id": request_id,
            "username": body.username,
            "event_type": "auth.login.success",
        },
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
    )


@router.post("/refresh", response_model=TokenResponse)
async def auth_refresh(request: Request, body: RefreshRequest) -> TokenResponse:
    """Refresh JWT access token using a refresh token.

    Args:
        request: FastAPI request.
        body: Refresh request containing refresh token.

    Returns:
        New access token.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info("Token refresh attempt", extra={"request_id": request_id})

    payload = decode_refresh_token(body.refresh_token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    stored = get_refresh_token(body.refresh_token)
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
    credentials: HTTPAuthorizationCredentials | None = Depends(security),  # noqa: B008
) -> dict[str, str]:
    """Logout user by revoking access token.

    Args:
        request: FastAPI request.
        credentials: Authorization header credentials.

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
    revoke_access_token(credentials.credentials)
    logger.info(
        "Token revoked",
        extra={"request_id": request_id, "event_type": "auth.token.revoked"},
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
