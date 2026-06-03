"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.api.routers.hello import router as hello_router
from app.core.config import settings
from app.core.exceptions import AppError, app_exception_handler, generic_exception_handler
from app.core.logging_config import setup_logging
from app.core.middleware import RequestIdMiddleware

setup_logging()

app = FastAPI(title=settings.app_name)
app.add_middleware(RequestIdMiddleware)
app.add_exception_handler(AppError, app_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)
app.include_router(hello_router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
