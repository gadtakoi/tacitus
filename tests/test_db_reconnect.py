"""Tests that the connection pool survives a database restart.

The application engine used the default pool without ``pool_pre_ping``, so
after PostgreSQL restarted — maintenance, an upgrade, a failover — the pool
kept handing out connections to a backend that no longer existed. Clients got
500 instead of a transparent reconnect.

Reproduced before the fix as ``docker restart tacitus-postgres`` followed by
any request touching the database. Here the same condition is created without
restarting anything: the server is asked to terminate the backend behind a
pooled connection, which is exactly what a restart does to every connection at
once.
"""

import os
import pathlib

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture
def database_url(_db_available: bool) -> str:
    """URL of the test database, skipping when PostgreSQL is unreachable.

    Args:
        _db_available: Session-wide availability flag from ``conftest.py``.

    Returns:
        Async SQLAlchemy URL of the test database.
    """
    if not _db_available:
        pytest.skip("PostgreSQL is not available")
    return os.environ["TACITUS_DATABASE_URL"]


async def _kill_pooled_backend(engine, killer_url: str) -> int:
    """Check out a connection, then terminate its backend from another one.

    Args:
        engine: Engine whose pool should be poisoned.
        killer_url: URL used for the separate connection issuing the kill.

    Returns:
        PID of the terminated backend.
    """
    async with engine.connect() as conn:
        pid = (await conn.execute(text("SELECT pg_backend_pid()"))).scalar_one()

    # The connection above is back in the pool now. Kill it from the outside,
    # the way a server restart would.
    killer = create_async_engine(killer_url)
    try:
        async with killer.connect() as conn:
            await conn.execute(
                text("SELECT pg_terminate_backend(:pid)").bindparams(pid=pid),
            )
    finally:
        await killer.dispose()
    return pid


@pytest.mark.anyio
async def test_pool_recovers_from_a_terminated_backend(database_url: str) -> None:
    """A dead pooled connection is replaced instead of being handed out.

    Args:
        database_url: URL of the test database.
    """
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        await _kill_pooled_backend(engine, database_url)

        # Without pre-ping this checkout returns the dead connection and the
        # query raises.
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_without_pre_ping_the_dead_connection_surfaces(database_url: str) -> None:
    """The failure mode is real, not hypothetical.

    Pins the behaviour the fix removes: with pre-ping off, the very next use of
    the pool raises. Without this test the one above would pass on an engine
    that never pooled anything and prove nothing.

    Args:
        database_url: URL of the test database.
    """
    engine = create_async_engine(database_url, pool_pre_ping=False)
    try:
        await _kill_pooled_backend(engine, database_url)

        with pytest.raises(DBAPIError):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


def test_application_engine_is_configured_with_pre_ping() -> None:
    """The application engine is created with the setting.

    Asserted against the source rather than the live object: ``conftest.py``
    deliberately replaces ``app.db.engine.engine`` with a ``NullPool`` engine
    for the whole suite (each test runs in its own event loop and a pooled
    asyncpg connection cannot cross loops), so the running engine here is not
    the one production uses. Same approach as the compose and Dockerfile
    pinning tests.
    """
    source = pathlib.Path("app/db/engine.py").read_text(encoding="utf-8")
    call = source[source.index("create_async_engine(") : source.index("AsyncSessionLocal")]

    assert "pool_pre_ping=True" in call, (
        "app/db/engine.py must create the engine with pool_pre_ping=True, "
        "otherwise a database restart turns into 500s for the client"
    )
