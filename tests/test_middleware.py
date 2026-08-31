"""Tests for MaxRequestSizeMiddleware."""

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.core.middleware import MaxRequestSizeMiddleware


def _build_app(max_size: int) -> FastAPI:
    """Create a minimal app wrapped with the middleware under test.

    A fresh application per test is required because middleware cannot be
    added to an already-started Starlette app.

    Args:
        max_size: Request body limit in bytes for the middleware.

    Returns:
        FastAPI application instance with the middleware installed.
    """
    test_app = FastAPI()
    test_app.add_middleware(MaxRequestSizeMiddleware, max_size=max_size)

    @test_app.post("/echo")
    async def echo(request: Request) -> dict[str, str]:
        """Consume the body and acknowledge it."""
        body = await request.body()
        return {"received": str(len(body))}

    @test_app.get("/health")
    async def health() -> dict[str, str]:
        """Trivial endpoint for requests without a body."""
        return {"status": "ok"}

    return test_app


@pytest.mark.anyio
async def test_oversized_content_length_rejected() -> None:
    """Content-Length above the limit must produce 413."""
    transport = ASGITransport(app=_build_app(max_size=10))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/echo",
            content=b"x" * 20,
            headers={"Content-Length": "20"},
        )
        assert response.status_code == 413
        assert response.json() == {"error": "Request entity too large"}


@pytest.mark.anyio
async def test_malformed_content_length_does_not_crash() -> None:
    """A non-numeric Content-Length must not raise; request proceeds normally."""
    transport = ASGITransport(app=_build_app(max_size=10))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/echo",
            content=b"hello",
            headers={"Content-Length": "abc"},
        )
        assert response.status_code == 200
        assert response.json() == {"received": "5"}


@pytest.mark.anyio
async def test_valid_content_length_passes_through() -> None:
    """A body within the limit must reach the endpoint unchanged."""
    transport = ASGITransport(app=_build_app(max_size=1024))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/echo", content=b"hello")
        assert response.status_code == 200
        assert response.json() == {"received": "5"}


@pytest.mark.anyio
async def test_request_without_body_passes_through() -> None:
    """GET requests without a body must not be affected."""
    transport = ASGITransport(app=_build_app(max_size=10))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
