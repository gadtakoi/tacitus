"""Stream configuration router.

Configuration stored by Tacitus in PostgreSQL, as opposed to the live view of
MediaMTX exposed by ``app.api.routers.streams``. The two are deliberately kept
on separate prefixes: ``/streams`` proxies a third-party monitoring API and ends
in a ``{path:path}`` catch-all that would swallow any nested route added beside
it.

Creating a configuration here does **not** provision a MediaMTX path. Which
paths exist is decided by ``mediamtx/mediamtx.yml`` (rendered from
``mediamtx_paths`` in production); a row in this table only carries the settings
Tacitus itself applies to a stream's recordings.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.rate_limit import get_user_or_ip, limiter
from app.db.engine import get_db_session
from app.schemas.streams import StreamConfig, StreamCreateRequest, StreamUpdateRequest
from app.services import stream_config_service
from app.services.stream_config_service import (
    StreamConfigConflictError,
    StreamConfigNotFoundError,
    StreamConfigValidationError,
)

router = APIRouter(prefix="/stream-configs", tags=["stream-configs"])
logger = logging.getLogger(__name__)


@router.get("", response_model=list[StreamConfig])
@limiter.limit("60/minute", key_func=get_user_or_ip)
async def list_stream_configs(
    request: Request,
    current_user: str = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[StreamConfig]:
    """List every stored stream configuration.

    Args:
        request: FastAPI request.
        current_user: Authenticated username from JWT.
        db: Database session.

    Returns:
        Stream configurations ordered by creation time.

    Raises:
        HTTPException: 429 if the rate limit is exceeded.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info(
        "Listing stream configurations",
        extra={"request_id": request_id, "user": current_user},
    )
    return await stream_config_service.list_streams(db)


@router.post("", response_model=StreamConfig, status_code=status.HTTP_201_CREATED)
@limiter.limit("60/minute", key_func=get_user_or_ip)
async def create_stream_config(
    request: Request,
    payload: StreamCreateRequest,
    current_user: str = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> StreamConfig:
    """Create a stream configuration.

    Args:
        request: FastAPI request.
        payload: Configuration to store.
        current_user: Authenticated username from JWT.
        db: Database session.

    Returns:
        The stored configuration.

    Raises:
        HTTPException: 409 if the name is already taken; 429 if the rate limit
            is exceeded.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    try:
        config = await stream_config_service.create_stream(db, payload)
    except StreamConfigConflictError as exc:
        logger.info(
            "Stream configuration already exists",
            extra={"request_id": request_id, "user": current_user, "stream": payload.name},
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.message) from exc
    logger.info(
        "Stream configuration created",
        extra={"request_id": request_id, "user": current_user, "stream": config.name},
    )
    return config


@router.get("/{name}", response_model=StreamConfig)
@limiter.limit("60/minute", key_func=get_user_or_ip)
async def get_stream_config(
    request: Request,
    name: str,
    current_user: str = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> StreamConfig:
    """Get one stream configuration by name.

    Args:
        request: FastAPI request.
        name: Stream name.
        current_user: Authenticated username from JWT.
        db: Database session.

    Returns:
        The stored configuration.

    Raises:
        HTTPException: 404 if no configuration is stored under that name; 429 if
            the rate limit is exceeded.
    """
    config = await stream_config_service.get_stream(db, name)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stream configuration not found",
        )
    return config


@router.patch("/{name}", response_model=StreamConfig)
@limiter.limit("60/minute", key_func=get_user_or_ip)
async def update_stream_config(
    request: Request,
    name: str,
    payload: StreamUpdateRequest,
    current_user: str = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> StreamConfig:
    """Update parts of a stream configuration.

    Args:
        request: FastAPI request.
        name: Stream name to update.
        payload: Fields to change; omitted fields keep their stored value.
        current_user: Authenticated username from JWT.
        db: Database session.

    Returns:
        The updated configuration.

    Raises:
        HTTPException: 404 if no configuration is stored under that name; 422 if
            the merged crop settings would remove too much of the frame; 429 if
            the rate limit is exceeded.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    try:
        config = await stream_config_service.update_stream(db, name, payload)
    except StreamConfigNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stream configuration not found",
        ) from exc
    except StreamConfigValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.message,
        ) from exc
    logger.info(
        "Stream configuration updated",
        extra={"request_id": request_id, "user": current_user, "stream": name},
    )
    return config


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute", key_func=get_user_or_ip)
async def delete_stream_config(
    request: Request,
    name: str,
    current_user: str = Depends(get_current_user),  # noqa: B008
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    """Delete a stream configuration.

    Recordings already on disk are unaffected: this removes the settings, not
    the footage.

    Args:
        request: FastAPI request.
        name: Stream name to delete.
        current_user: Authenticated username from JWT.
        db: Database session.

    Returns:
        An empty 204 response.

    Raises:
        HTTPException: 404 if no configuration is stored under that name; 429 if
            the rate limit is exceeded.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    try:
        await stream_config_service.delete_stream(db, name)
    except StreamConfigNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stream configuration not found",
        ) from exc
    logger.info(
        "Stream configuration deleted",
        extra={"request_id": request_id, "user": current_user, "stream": name},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
