"""Shared test fixtures."""

import pytest

from app.core.rate_limit import limiter
from app.services import auth_service


@pytest.fixture(autouse=True)
def reset_global_state() -> None:
    """Reset in-memory state between tests."""
    limiter.reset()
    auth_service._users.clear()
    auth_service._failed_attempts.clear()
    auth_service._token_blacklist.clear()
    auth_service._refresh_tokens.clear()
