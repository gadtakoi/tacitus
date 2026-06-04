"""FastAPI application entrypoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers.auth import router as auth_router
from app.api.routers.files import router as files_router
from app.api.routers.hello import router as hello_router
from app.core.config import settings
from app.core.exceptions import AppError, app_exception_handler, generic_exception_handler
from app.core.logging_config import setup_logging
from app.core.middleware import RequestIdMiddleware

setup_logging()

app = FastAPI(title=settings.app_name)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_exception_handler(AppError, app_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)
app.include_router(hello_router)
app.include_router(auth_router)
app.include_router(files_router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
