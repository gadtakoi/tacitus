"""MediaMTX streams monitoring router."""

import logging
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import get_current_user
from app.core.config import settings
from app.schemas.streams import StreamItem, StreamsListResponse

router = APIRouter(prefix="/streams", tags=["streams"])
logger = logging.getLogger(__name__)


async def _fetch_from_mediamtx(path: str) -> dict:
    """Fetch JSON from MediaMTX Control API.

    The base URL comes from ``settings.mtx_api_url`` (``TACITUS_MTX_API_URL``)
    so it is validated by pydantic-settings and consistent with the rest of
    the configuration.

    The Control API is protected by MediaMTX's internal authentication, so the
    request carries HTTP Basic credentials for a user that holds the ``api``
    action only. Without them MediaMTX answers 401 for every call: its default
    rule grants ``api`` to loopback addresses, and the backend reaches MediaMTX
    over the Docker network.

    Args:
        path: API path to fetch (e.g. /paths/list).

    Returns:
        Parsed JSON response.

    Raises:
        HTTPException: If MediaMTX API is unavailable, rejects our credentials
            or returns an error.
    """
    url = f"{settings.mtx_api_url}{path}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                url,
                auth=httpx.BasicAuth(settings.mtx_api_user, settings.mtx_api_password),
            )
    except httpx.NetworkError as exc:
        logger.warning("MediaMTX API network error", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MediaMTX API is unreachable",
        ) from exc
    except httpx.TimeoutException as exc:
        logger.warning("MediaMTX API timeout")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="MediaMTX API timeout",
        ) from exc

    if response.status_code == status.HTTP_404_NOT_FOUND:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stream not found",
        )
    if response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
        # Our own credentials were rejected: a deployment problem, not a
        # client one. Logged distinctly because the generic 502 below reads
        # like MediaMTX is broken, while the actual fix is to align
        # TACITUS_MTX_API_USER/PASSWORD with authInternalUsers in mediamtx.yml.
        logger.error(
            "MediaMTX API rejected backend credentials",
            extra={"status": response.status_code, "mtx_api_user": settings.mtx_api_user},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="MediaMTX API authentication failed",
        )
    if response.status_code >= status.HTTP_400_BAD_REQUEST:
        # The response body is not logged: it comes from an external system and
        # may carry arbitrary content, which does not belong in our log stream.
        logger.warning("MediaMTX API error", extra={"status": response.status_code})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"MediaMTX API returned {response.status_code}",
        )

    return response.json()


@router.get("", response_model=StreamsListResponse)
async def list_streams(
    request: Request,
    current_user: str = Depends(get_current_user),
) -> StreamsListResponse:
    """List active MediaMTX streams.

    Args:
        request: FastAPI request.
        current_user: Authenticated username from JWT.

    Returns:
        Paginated list of active streams.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info("Listing MediaMTX streams", extra={"request_id": request_id, "user": current_user})
    data = await _fetch_from_mediamtx("/paths/list")
    return StreamsListResponse(**data)


@router.get("/{path:path}", response_model=StreamItem)
async def get_stream(
    request: Request,
    path: str,
    current_user: str = Depends(get_current_user),
) -> StreamItem:
    """Get information about a specific MediaMTX stream.

    Args:
        request: FastAPI request.
        path: Stream path name.
        current_user: Authenticated username from JWT.

    Returns:
        Stream details.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info(
        "Fetching MediaMTX stream",
        extra={"request_id": request_id, "user": current_user, "stream": path},
    )
    encoded_path = quote(path, safe="")
    data = await _fetch_from_mediamtx(f"/paths/get/{encoded_path}")
    return StreamItem(**data)
