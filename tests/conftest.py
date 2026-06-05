"""Shared test fixtures."""

import os

os.environ.setdefault("TACITUS_S3_ACCESS_KEY", "minioadmin")
os.environ.setdefault("TACITUS_S3_SECRET_KEY", "minioadmin")
os.environ.setdefault("TACITUS_JWT_SECRET", "change-me-in-production")

import pytest  # noqa: E402

from app.core.rate_limit import limiter  # noqa: E402
from app.services import auth_service  # noqa: E402


@pytest.fixture(autouse=True)
def reset_global_state() -> None:
    """Reset in-memory state between tests."""
    limiter.reset()
    auth_service._users.clear()
    auth_service._failed_attempts.clear()
    auth_service._token_blacklist.clear()
    auth_service._refresh_tokens.clear()
