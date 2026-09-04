"""MediaMTX streams monitoring router."""

import logging
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ValidationError

from app.api.dependencies import get_current_user
from app.core.config import settings
from app.core.rate_limit import get_user_or_ip, limiter
from app.schemas.streams import StreamItem, StreamsListResponse

router = APIRouter(prefix="/streams", tags=["streams"])
logger = logging.getLogger(__name__)


async def _fetch_from_mediamtx[ModelT: BaseModel](path: str, model: type[ModelT]) -> ModelT:
    """Fetch and validate a response from the MediaMTX Control API.

    The base URL comes from ``settings.mtx_api_url`` (``TACITUS_MTX_API_URL``)
    so it is validated by pydantic-settings and consistent with the rest of
    the configuration.

    The Control API is protected by MediaMTX's internal authentication, so the
    request carries HTTP Basic credentials for a user that holds the ``api``
    action only. Without them MediaMTX answers 401 for every call: its default
    rule grants ``api`` to loopback addresses, and the backend reaches MediaMTX
    over the Docker network.

    Everything MediaMTX can throw at us is mapped to a deliberate status code.
    Anything left uncaught would surface as a generic 500, which reads as "the
    application is broken" for what is really an upstream or configuration
    problem — and hides the cause from whoever has to fix it.

    Args:
        path: API path to fetch (e.g. /paths/list).
        model: Schema the response must satisfy.

    Returns:
        The validated response model.

    Raises:
        HTTPException: 404 when the stream is unknown; 502 when MediaMTX
            answers with an error, malformed body or an unexpected shape, or
            when its URL/credentials are misconfigured; 503 when it cannot be
            reached; 504 on timeout.
    """
    url = f"{settings.mtx_api_url}{path}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                url,
                auth=httpx.BasicAuth(settings.mtx_api_user, settings.mtx_api_password),
            )
    # Ordering matters: TimeoutException and NetworkError are both subclasses
    # of TransportError, which in turn is an HTTPError, so the specific cases
    # have to precede the catch-all below.
    except httpx.TimeoutException as exc:
        logger.warning("MediaMTX API timeout")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="MediaMTX API timeout",
        ) from exc
    except httpx.NetworkError as exc:
        logger.warning("MediaMTX API network error", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MediaMTX API is unreachable",
        ) from exc
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        # Protocol violations (RemoteProtocolError), redirect loops, and a
        # malformed or unsupported TACITUS_MTX_API_URL. ``InvalidURL`` is not
        # an ``HTTPError`` in httpx, hence the explicit pairing.
        logger.error(
            "MediaMTX API request failed",
            extra={"error_type": type(exc).__name__, "mtx_api_url": settings.mtx_api_url},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="MediaMTX API request failed",
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

    try:
        payload = response.json()
    except ValueError as exc:
        # A 200 with a body that is not JSON at all — typically something else
        # answering on the configured port.
        logger.error("MediaMTX API returned a non-JSON body")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="MediaMTX API returned a malformed response",
        ) from exc

    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        # Valid JSON in an unexpected shape: a MediaMTX version whose contract
        # drifted from ours. Validating here rather than in the endpoint keeps
        # it from escaping as a 500 through the response model.
        logger.error(
            "MediaMTX API response does not match the expected schema",
            extra={"schema": model.__name__, "errors": exc.error_count()},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="MediaMTX API returned an unexpected response",
        ) from exc


@router.get("", response_model=StreamsListResponse)
@limiter.limit("60/minute", key_func=get_user_or_ip)
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

    Raises:
        HTTPException: 429 if the rate limit is exceeded; see
            ``_fetch_from_mediamtx`` for upstream failures.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info("Listing MediaMTX streams", extra={"request_id": request_id, "user": current_user})
    return await _fetch_from_mediamtx("/paths/list", StreamsListResponse)


@router.get("/{path:path}", response_model=StreamItem)
@limiter.limit("60/minute", key_func=get_user_or_ip)
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

    Raises:
        HTTPException: 404 if the stream is unknown; 429 if the rate limit is
            exceeded; see ``_fetch_from_mediamtx`` for upstream failures.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info(
        "Fetching MediaMTX stream",
        extra={"request_id": request_id, "user": current_user, "stream": path},
    )
    encoded_path = quote(path, safe="")
    return await _fetch_from_mediamtx(f"/paths/get/{encoded_path}", StreamItem)
