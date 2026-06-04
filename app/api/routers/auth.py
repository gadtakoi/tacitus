"""Authentication router."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import get_current_user
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from app.services.auth_service import (
    AuthError,
    authenticate_user,
    create_access_token,
    register_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
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
async def auth_login(request: Request, body: LoginRequest) -> TokenResponse:
    """Authenticate user and return JWT token.

    Args:
        request: FastAPI request.
        body: Login credentials.

    Returns:
        Access token.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info("Login attempt", extra={"request_id": request_id, "username": body.username})
    user = authenticate_user(body.username, body.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    access_token = create_access_token({"sub": user["username"]})
    return TokenResponse(access_token=access_token, token_type="bearer")


@router.post("/refresh")
async def auth_refresh(request: Request, username: str, password: str) -> dict[str, str]:
    """Refresh JWT token.

    Args:
        request: FastAPI request.
        username: Username.
        password: User password.

    Returns:
        New access token.
    """
    return await auth_login(request, username, password)


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


@router.post("/logout")
async def auth_logout() -> dict[str, str]:
    """Logout user (client-side token removal).

    Returns:
        Logout confirmation.
    """
    return {"message": "Logged out successfully"}
