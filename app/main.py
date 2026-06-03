"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.core.config import settings

app = FastAPI(title=settings.app_name)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}
