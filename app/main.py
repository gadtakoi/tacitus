"""FastAPI application entrypoint."""

import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.exc import DBAPIError, IntegrityError

from app import __version__
from app.api.routers.auth import router as auth_router
from app.api.routers.files import router as files_router
from app.api.routers.settings import router as settings_router
from app.api.routers.stream_configs import router as stream_configs_router
from app.api.routers.streams import router as streams_router
from app.core.config import api_docs_enabled, settings, validate_startup_settings
from app.core.exceptions import (
    AppError,
    app_exception_handler,
    dbapi_error_handler,
    generic_exception_handler,
    integrity_error_handler,
    rate_limit_exceeded_handler,
    validation_error_handler,
)
from app.core.logging_config import setup_logging
from app.core.middleware import MaxRequestSizeMiddleware, RequestIdMiddleware
from app.core.rate_limit import limiter
from app.core.security_logging import SuspiciousActivityMiddleware
from app.core.sentry import init_sentry

setup_logging()
validate_startup_settings()
init_sentry()

# The build a container runs is otherwise invisible: images are built on the
# deployment host, so an operator comparing behaviour across environments has
# nothing to compare but this line.
logging.getLogger(__name__).info(
    "Starting %s %s in %s environment",
    settings.app_name,
    __version__,
    settings.environment,
)

# Swagger, ReDoc and the OpenAPI document are anonymous, and nginx proxies
# /api/ onto the application root, so in a deployed stack they answer at
# /api/docs and /api/openapi.json. All three are gated by the same call: leaving
# openapi_url on while clearing docs_url hides the viewer, not the document —
# and the document is what carries the version and the full API surface.
_docs_enabled = api_docs_enabled()
app = FastAPI(
    title=settings.app_name,
    version=__version__,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)
app.state.limiter = limiter
# Starlette wraps middleware in reverse order of registration: the one added
# last ends up outermost. The order below therefore reads inside-out, and the
# resulting chain is:
#
#   CORS -> RequestId -> MaxRequestSize -> SlowAPI -> SuspiciousActivity -> routes
#
# ``RequestIdMiddleware`` used to be registered first, which made it the
# innermost: ``MaxRequestSizeMiddleware`` returns its 413 without calling
# downstream, so that response never passed through it and went out with no
# ``x-request-id`` — the one rejection a client most needs to be able to quote.
# CORS stays outermost so even rejected requests carry CORS headers.
app.add_middleware(SuspiciousActivityMiddleware)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(MaxRequestSizeMiddleware, max_size=11 * 1024 * 1024)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_exception_handler(AppError, app_exception_handler)  # type: ignore[arg-type]
app.add_exception_handler(IntegrityError, integrity_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(DBAPIError, dbapi_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # type: ignore[arg-type]
app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(Exception, generic_exception_handler)
app.include_router(auth_router)
app.include_router(files_router)
app.include_router(settings_router)
# Registered before the streams router: that one ends in a ``{path:path}``
# catch-all, and FastAPI matches routes in registration order.
app.include_router(stream_configs_router)
app.include_router(streams_router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
