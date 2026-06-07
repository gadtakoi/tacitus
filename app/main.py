"""FastAPI application entrypoint."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.routers.auth import router as auth_router
from app.api.routers.files import router as files_router
from app.api.routers.hello import router as hello_router
from app.api.routers.streams import router as streams_router
from app.core.config import settings, validate_startup_settings
from app.core.exceptions import AppError, app_exception_handler, generic_exception_handler
from app.core.ip import get_client_ip
from app.core.logging_config import setup_logging
from app.core.middleware import MaxRequestSizeMiddleware, RequestIdMiddleware
from app.core.rate_limit import limiter
from app.core.security_logging import SuspiciousActivityMiddleware, log_security_event
from app.core.sentry import init_sentry


async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Handle rate limit exceeded with security logging."""
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


setup_logging()
validate_startup_settings()
init_sentry()

app = FastAPI(title=settings.app_name)
app.state.limiter = limiter
app.add_middleware(RequestIdMiddleware)
app.add_middleware(SuspiciousActivityMiddleware)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(MaxRequestSizeMiddleware, max_size=11 * 1024 * 1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_exception_handler(AppError, app_exception_handler)
app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
app.add_exception_handler(Exception, generic_exception_handler)
app.include_router(hello_router)
app.include_router(auth_router)
app.include_router(files_router)
app.include_router(streams_router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
