"""Authentication router."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import get_current_user
from app.services.auth_service import (
    AuthError,
    authenticate_user,
    create_access_token,
    register_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def auth_register(request: Request, username: str, password: str) -> dict[str, str]:
    """Register a new user.

    Args:
        request: FastAPI request.
        username: Unique username.
        password: User password.

    Returns:
        Registration confirmation.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info("Registering user", extra={"request_id": request_id, "username": username})
    try:
        register_user(username, password)
    except AuthError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=e.message) from e
    return {"username": username, "message": "User registered successfully"}


@router.post("/login")
async def auth_login(request: Request, username: str, password: str) -> dict[str, str]:
    """Authenticate user and return JWT token.

    Args:
        request: FastAPI request.
        username: Username.
        password: User password.

    Returns:
        Access token.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info("Login attempt", extra={"request_id": request_id, "username": username})
    user = authenticate_user(username, password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    access_token = create_access_token({"sub": user["username"]})
    return {"access_token": access_token, "token_type": "bearer"}


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
) -> dict[str, str]:
    """Get current authenticated user.

    Args:
        request: FastAPI request.
        current_user: Authenticated username from JWT.

    Returns:
        Current username.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info("Fetching current user", extra={"request_id": request_id, "username": current_user})
    return {"username": current_user}


@router.post("/logout")
async def auth_logout() -> dict[str, str]:
    """Logout user (client-side token removal).

    Returns:
        Logout confirmation.
    """
    return {"message": "Logged out successfully"}
