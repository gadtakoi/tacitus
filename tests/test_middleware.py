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
        body = response.json()
        assert body["error"] == "Request entity too large"
        # No RequestIdMiddleware in this minimal app, so the id falls back.
        assert body["request_id"] == "unknown"


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


@pytest.mark.anyio
async def test_size_rejection_on_the_real_app_carries_a_correlation_id() -> None:
    """The 413 from the real application is traceable.

    ``RequestIdMiddleware`` used to be registered first, which made it the
    innermost wrapper. ``MaxRequestSizeMiddleware`` answers without calling
    downstream, so its rejection never passed through the id middleware and
    went out with neither the ``x-request-id`` header nor a ``request_id`` in
    the body — leaving the client with a failure they could not quote in a
    bug report.

    No database is involved: the middleware short-circuits before any handler.
    """
    from app.main import app as real_app

    transport = ASGITransport(app=real_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/files/upload",
            content=b"x" * 16,
            headers={"Content-Length": str(12 * 1024 * 1024)},
        )

    assert response.status_code == 413
    assert "x-request-id" in response.headers
    assert response.json()["request_id"] == response.headers["x-request-id"]


@pytest.mark.anyio
async def test_middleware_order_keeps_cors_outermost() -> None:
    """CORS wraps everything, so even rejected requests answer with its headers.

    Registration order is reverse-nesting, which makes it easy to break by
    moving a line; this pins the two ends of the chain.
    """
    from app.main import app as real_app

    transport = ASGITransport(app=real_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/files/upload",
            content=b"x" * 16,
            headers={
                "Content-Length": str(12 * 1024 * 1024),
                "Origin": "http://localhost:3000",
            },
        )

    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
