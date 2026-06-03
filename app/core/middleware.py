"""Custom middleware for the application."""

from typing import Any

from fastapi import Request
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
