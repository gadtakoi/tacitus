"""Custom middleware for the application."""

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging_config import get_request_id


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

        Args:
            request: Incoming request.
            call_next: Next middleware/handler in chain.

        Returns:
            Response object.
        """
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_size:
            return JSONResponse(
                status_code=413,
                content={"error": "Request entity too large"},
            )
        return await call_next(request)
