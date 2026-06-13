"""Shared test fixtures.

The fixtures here provide an async PostgreSQL test database, an isolated
session per test (rolled back after the test) and an async HTTP client that
injects the test session into the FastAPI application.

Tests that need the database are automatically skipped when PostgreSQL is not
reachable (e.g. in local development without Docker). This lets the rest of the
suite run without requiring a running database.
"""

import os
import tempfile

# Set sensible test defaults *before* any application module is imported.
os.environ.setdefault("TACITUS_S3_ACCESS_KEY", "minioadmin")
os.environ.setdefault("TACITUS_S3_SECRET_KEY", "minioadmin")
os.environ.setdefault("TACITUS_JWT_SECRET", "change-me-in-production")
os.environ.setdefault("TACITUS_RATE_LIMIT_STORAGE_URI", "memory://")

_log_dir = tempfile.mkdtemp()
os.environ.setdefault("TACITUS_LOG_FILE_PATH", os.path.join(_log_dir, "app.log"))
os.environ.setdefault("TACITUS_LOG_DESTINATION", "both")

# Use a dedicated test database. Override via the environment variable when
# running against a non-default PostgreSQL instance (e.g. CI or docker compose).
os.environ.setdefault(
    "TACITUS_DATABASE_URL",
    "postgresql+asyncpg://tacitus:tacitus@localhost:5432/tacitus_test",
)

import asyncio  # noqa: E402

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.rate_limit import limiter  # noqa: E402
from app.db.engine import get_db_session  # noqa: E402
from app.db.models import Base  # noqa: E402
from app.main import app  # noqa: E402

TEST_DATABASE_URL = os.environ["TACITUS_DATABASE_URL"]

# Engine shared by all DB-related fixtures. ``connect_args`` limits the
# connection timeout so tests fail fast when PostgreSQL is not running.
_test_engine = create_async_engine(
    TEST_DATABASE_URL,
    future=True,
    echo=False,
    connect_args={"timeout": 5},
)


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

    In-memory auth stores have been replaced by PostgreSQL repositories, so the
    only global state left to reset here is the rate-limit storage.
    """
    _reset_limiter_state()
    yield
    _reset_limiter_state()


@pytest.fixture(scope="session")
def _db_available() -> bool:
    """Check once per session whether the test database is reachable.

    Returns:
        ``True`` if a connection can be opened, ``False`` otherwise.
    """

    async def _check() -> bool:
        try:
            async with _test_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:  # pragma: no cover - depends on external service
            return False

    return asyncio.run(_check())


@pytest.fixture
async def async_engine(_db_available: bool):
    """Create the test database schema and yield the async engine.

    Tables are created before each test and dropped after it so every test
    starts with a clean database. If PostgreSQL is unreachable the dependent
    tests are skipped instead of failing with a connection error.

    Args:
        _db_available: Session-wide flag populated by ``_db_available``.

    Yields:
        AsyncEngine configured for the test database.
    """
    if not _db_available:
        pytest.skip("PostgreSQL test database is not available")

    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield _test_engine

    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def db_session(async_engine):
    """Open an isolated async session for a single test.

    A connection-level transaction is opened first; the session is bound to
    that connection. Even if application code calls ``session.commit()``, the
    connection transaction is rolled back after the test so no data is ever
    persisted.

    Args:
        async_engine: Function-scoped test database engine.

    Yields:
        AsyncSession tied to a transaction that will be rolled back.
    """
    async with async_engine.connect() as connection:
        # Use a nested transaction (savepoint) so application code can call
        # ``session.commit()`` without actually persisting data.
        trans = await connection.begin_nested()
        session_maker = async_sessionmaker(
            connection,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
        async with session_maker() as session:
            yield session
        await trans.rollback()


@pytest.fixture
async def client(db_session):
    """Async HTTP client that injects the test session into the app.

    ``get_db_session`` is overridden so that every request handled by the
    application uses ``db_session``. Because ``httpx.AsyncClient`` drives the
    ASGI app in the same event loop as the test, the async SQLAlchemy session
    is valid for the whole request lifecycle.

    Args:
        db_session: Test database session provided by the ``db_session`` fixture.

    Yields:
        ``httpx.AsyncClient`` instance pointed at the application.
    """

    async def override_get_db():
        """Yield the test session instead of opening a new one."""
        yield db_session

    app.dependency_overrides[get_db_session] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
