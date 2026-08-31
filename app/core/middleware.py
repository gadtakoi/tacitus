"""Custom middleware for the application."""

import logging
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging_config import get_request_id

logger = logging.getLogger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Middleware to attach request_id to each request."""

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        """Add request_id to request state and process request.

        Args:
            request: Incoming request.
            call_next: Next middleware/handler in chain.

        Returns:
            Response object.
        """
        request.state.request_id = get_request_id(request)
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        return response


class MaxRequestSizeMiddleware(BaseHTTPMiddleware):
    """Middleware to limit maximum request body size."""

    def __init__(self, app: Any, max_size: int) -> None:
        super().__init__(app)
        self.max_size = max_size

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        """Reject requests exceeding max body size.

        The ``Content-Length`` header is untrusted client input: it may be
        missing (chunked uploads) or contain a non-numeric value. A malformed
        value must not crash the middleware, so parsing failures are treated
        as "no limit information" and the request is passed downstream.

        Args:
            request: Incoming request.
            call_next: Next middleware/handler in chain.

        Returns:
            Response object.
        """
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                size = int(content_length)
            except ValueError:
                logger.warning(
                    "Malformed Content-Length header received",
                    extra={"content_length": content_length[:64]},
                )
            else:
                if size > self.max_size:
                    return JSONResponse(
                        status_code=413,
                        content={"error": "Request entity too large"},
                    )
        return await call_next(request)
