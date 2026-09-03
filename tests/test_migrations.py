"""Integration tests for Alembic migrations.

The test runs ``alembic upgrade head`` and ``alembic downgrade base`` against
the configured test database and checks that the expected tables appear and
disappear. It is skipped automatically when PostgreSQL is not reachable.
"""

import os
import subprocess
import sys

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models import Base
from tests.conftest import TEST_DATABASE_URL


@pytest.mark.anyio
async def test_alembic_upgrade_and_downgrade() -> None:
    """Apply and revert all migrations on a clean test database."""
    engine = create_async_engine(TEST_DATABASE_URL, future=True)

    # Skip the test gracefully when PostgreSQL is unavailable.
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - depends on external service
        await engine.dispose()
        pytest.skip(f"PostgreSQL test database is not available: {exc}")

    try:
        # Start from a clean state.
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

        env = os.environ.copy()
        env["TACITUS_DATABASE_URL"] = TEST_DATABASE_URL

        # Apply all migrations. Alembic is invoked through ``sys.executable``
        # rather than as a bare ``alembic`` command: the console script lives in
        # the virtualenv's Scripts/bin directory, which is not on PATH unless the
        # environment was activated. Running ``python -m pytest`` from the
        # interpreter directly is the normal case, and there the bare command
        # fails with FileNotFoundError instead of testing anything.
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0, f"alembic upgrade failed:\n{result.stderr}"

        # Verify core tables were created.
        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'",
                ),
            )
            table_names = {row[0] for row in rows}

        assert "users" in table_names
        assert "refresh_tokens" in table_names
        assert "revoked_tokens" in table_names
        assert "failed_login_attempts" in table_names
        assert "security_events" in table_names
        assert "alembic_version" in table_names

        # Revert all migrations.
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "base"],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0, f"alembic downgrade failed:\n{result.stderr}"

        # Core tables should be removed; alembic_version is gone as well.
        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'",
                ),
            )
            table_names = {row[0] for row in rows}

        assert "users" not in table_names
        assert "refresh_tokens" not in table_names
        assert "revoked_tokens" not in table_names
        assert "failed_login_attempts" not in table_names
        assert "security_events" not in table_names
    finally:
        await engine.dispose()
