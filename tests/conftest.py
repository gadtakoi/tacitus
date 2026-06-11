"""Shared test fixtures."""

import os
import tempfile

os.environ.setdefault("TACITUS_S3_ACCESS_KEY", "minioadmin")
os.environ.setdefault("TACITUS_S3_SECRET_KEY", "minioadmin")
os.environ.setdefault("TACITUS_JWT_SECRET", "change-me-in-production")
os.environ.setdefault("TACITUS_RATE_LIMIT_STORAGE_URI", "memory://")

_log_dir = tempfile.mkdtemp()
os.environ.setdefault("TACITUS_LOG_FILE_PATH", os.path.join(_log_dir, "app.log"))
os.environ.setdefault("TACITUS_LOG_DESTINATION", "both")

import pytest  # noqa: E402

from app.core.rate_limit import limiter  # noqa: E402


def _reset_limiter_state() -> None:
    """Reset rate-limit counters for the configured storage backend.

    ``MemoryStorage`` is cleared with ``limiter.reset()``. For Redis the same
    method is available on the underlying storage object and flushes all
    rate-limit keys.
    """
    # Import inside the helper to avoid importing the module before the
    # environment variables above are set.
    from limits.storage import MemoryStorage, RedisStorage

    storage = limiter._storage
    if isinstance(storage, (MemoryStorage, RedisStorage)):
        storage.reset()
    else:
        # Fallback for any other supported storage backend.
        storage.reset()


@pytest.fixture(autouse=True)
def reset_global_state() -> None:
    """Reset external state between tests.

    The previous in-memory auth stores have been replaced by PostgreSQL
    repositories; those are reset per-test via the database transaction
    rollback in the ``db_session`` fixture (see tests added in stage 11).
    """
    _reset_limiter_state()
    yield
    _reset_limiter_state()
