"""Runtime configuration router.

Exposes the environment-variable settings the process is running with, so the
current configuration can be checked from the web UI instead of by shelling
into the container. The endpoints are read-only: nothing here changes settings.

Secrets are withheld by ``app.services.settings_service``; this layer only
authenticates, rate-limits and logs the access.
"""

import logging

from fastapi import APIRouter, Depends, Request

from app import __version__
from app.api.dependencies import get_current_user
from app.core.rate_limit import get_user_or_ip, limiter
from app.schemas.settings import SettingsResponse
from app.schemas.version import VersionResponse
from app.services import settings_service

router = APIRouter(prefix="/settings", tags=["settings"])
logger = logging.getLogger(__name__)


@router.get("", response_model=SettingsResponse)
@limiter.limit("30/minute", key_func=get_user_or_ip)
async def list_settings(
    request: Request,
    current_user: str = Depends(get_current_user),  # noqa: B008
) -> SettingsResponse:
    """List the effective application settings with secrets withheld.

    Args:
        request: FastAPI request.
        current_user: Authenticated username from JWT.

    Returns:
        Every setting of the running process, grouped for display. Credentials
        are returned with a null value and ``sensitive=true``.

    Raises:
        HTTPException: 401 if the caller is not authenticated; 429 if the rate
            limit is exceeded.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    # Reading the configuration is a security-relevant action even when no
    # value is disclosed: it reveals the shape of the deployment.
    logger.info(
        "Listing application settings",
        extra={"request_id": request_id, "user": current_user},
    )
    return SettingsResponse(items=settings_service.list_settings())


@router.get("/version", response_model=VersionResponse)
@limiter.limit("60/minute", key_func=get_user_or_ip)
async def get_version(
    request: Request,
    current_user: str = Depends(get_current_user),  # noqa: B008
) -> VersionResponse:
    """Return the version of the running application.

    Unlike the settings dump above this is not logged: the UI reads it on every
    page it renders, and a version number reveals nothing about the deployment
    beyond what the caller already had to authenticate to see.

    Args:
        request: FastAPI request. Unused here, but ``limiter.limit`` requires
            the decorated endpoint to declare it.
        current_user: Authenticated username from JWT.

    Returns:
        The version the process was built from.

    Raises:
        HTTPException: 401 if the caller is not authenticated; 429 if the rate
            limit is exceeded.
    """
    return VersionResponse(version=__version__)
