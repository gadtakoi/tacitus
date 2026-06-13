"""Tests for error handling."""

from unittest.mock import MagicMock, patch

import pytest

from app.core.exceptions import generic_exception_handler


@pytest.mark.anyio
async def test_generic_exception_handler_production() -> None:
    """Test production mode returns safe generic message."""
    request = MagicMock()
    request.state.request_id = "test-id"
    with patch("app.core.exceptions.settings.debug", False):
        response = await generic_exception_handler(request, ValueError("secret detail"))
    assert response.status_code == 500
    body = bytes(response.body).decode()
    assert "Internal server error" in body
    assert "secret detail" not in body


@pytest.mark.anyio
async def test_generic_exception_handler_debug() -> None:
    """Test debug mode returns exception details."""
    request = MagicMock()
    request.state.request_id = "test-id"
    with patch("app.core.exceptions.settings.debug", True):
        response = await generic_exception_handler(request, ValueError("secret detail"))
    assert response.status_code == 500
    body = bytes(response.body).decode()
    assert "secret detail" in body
