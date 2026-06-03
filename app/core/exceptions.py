"""Custom exceptions and error handlers."""

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base application exception."""

    def __init__(self, message: str, status_code: int = 500) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


async def app_exception_handler(request: Request, exc: AppError) -> JSONResponse:
    """Handle application-specific exceptions.

    Args:
        request: FastAPI request object.
        exc: Application exception.

    Returns:
        JSON response with error details.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.message,
            "request_id": getattr(request.state, "request_id", "unknown"),
        },
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions without leaking internal details.

    Args:
        request: FastAPI request object.
        exc: Generic exception.

    Returns:
        JSON response with generic error message.
    """
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "request_id": getattr(request.state, "request_id", "unknown"),
        },
    )
