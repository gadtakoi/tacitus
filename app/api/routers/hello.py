"""Hello world router."""

import logging

from fastapi import APIRouter, Request

from app.schemas.hello import HelloWorldResponse
from app.services.hello_service import hello_service

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/", response_model=HelloWorldResponse)
async def hello_world(request: Request) -> HelloWorldResponse:
    """Return hello world message.

    Args:
        request: FastAPI request object for extracting request_id.

    Returns:
        HelloWorldResponse with greeting message.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.info(
        "Handling hello world request",
        extra={"request_id": request_id, "route": str(request.url.path)},
    )
    return hello_service.get_hello()
