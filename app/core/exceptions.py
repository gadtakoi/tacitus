"""Custom exceptions and error handlers.

This module keeps application-level exceptions and their FastAPI handlers in
one place. Database-related handlers live here so the API layer can register
a small, predictable set of error responses without leaking SQL details to
clients.
"""

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.config import settings
from app.core.ip import get_client_ip
from app.core.security_logging import log_security_event


class AppError(Exception):
    """Base application exception.

    Attributes:
        message: Human-readable error description.
        status_code: HTTP status code to return to the client.
    """

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
        JSON response with error details and request id.
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

    In production returns a generic safe message.
    In debug mode includes the exception message.

    Args:
        request: FastAPI request object.
        exc: Generic exception.

    Returns:
        JSON response with error message and request id.
    """
    if settings.debug:
        return JSONResponse(
            status_code=500,
            content={
                "error": str(exc),
                "request_id": getattr(request.state, "request_id", "unknown"),
            },
        )
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "request_id": getattr(request.state, "request_id", "unknown"),
        },
    )


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """Handle database integrity errors without exposing SQL details.

    Typical causes are unique constraint violations (duplicate username,
    duplicate token hash, etc.). Returns HTTP 409 Conflict.

    Args:
        request: FastAPI request object.
        exc: SQLAlchemy integrity error.

    Returns:
        JSON response with a safe conflict message and request id.
    """
    log_security_event(
        "db.integrity_error",
        severity="error",
        ip=get_client_ip(request),
        extra={"endpoint": request.url.path},
    )
    return JSONResponse(
        status_code=409,
        content={
            "error": "Conflict with existing data",
            "request_id": getattr(request.state, "request_id", "unknown"),
        },
    )


async def dbapi_error_handler(request: Request, exc: DBAPIError) -> JSONResponse:
    """Handle unexpected database driver errors without exposing SQL details.

    This catches low-level driver failures (connection lost, transaction
    aborted, etc.) and returns HTTP 500 with a generic database error message.

    Args:
        request: FastAPI request object.
        exc: SQLAlchemy DBAPI error.

    Returns:
        JSON response with a safe database error message and request id.
    """
    log_security_event(
        "db.driver_error",
        severity="error",
        ip=get_client_ip(request),
        extra={"endpoint": request.url.path},
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "Database error",
            "request_id": getattr(request.state, "request_id", "unknown"),
        },
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return validation errors without echoing the submitted value.

    FastAPI's default handler includes an ``input`` field carrying whatever the
    client sent. For ``POST /auth/register`` and ``POST /auth/login`` that is
    the password, returned in plain text in the response body — where it
    reaches browser devtools, client-side logs, proxies and error trackers.

    ``loc`` and ``msg`` already tell the caller which field failed and why, so
    the echo is dropped for every field rather than maintained as a list of
    sensitive names: a new secret-bearing field would otherwise start leaking
    the day it is added. ``ctx`` goes too — it can carry the original value in
    its error payload.

    Args:
        request: FastAPI request object.
        exc: Validation error raised while parsing the request.

    Returns:
        JSON response with HTTP 422 and the sanitized error list.
    """
    sanitized = [
        {"type": error.get("type"), "loc": error.get("loc"), "msg": error.get("msg")}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "detail": sanitized,
            "request_id": getattr(request.state, "request_id", "unknown"),
        },
    )


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Handle rate limit exceeded with security logging.

    Logs a ``rate_limit.exceeded`` security event and returns HTTP 429.

    Args:
        request: FastAPI request object.
        exc: slowapi rate-limit exceeded exception.

    Returns:
        JSON response indicating the rate limit was exceeded.
    """
    log_security_event(
        "rate_limit.exceeded",
        severity="warning",
        ip=get_client_ip(request),
        extra={"endpoint": request.url.path},
    )
    return JSONResponse(
        status_code=429,
        content={"error": "Rate limit exceeded"},
    )
