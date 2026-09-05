"""Database engine and session management."""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    future=True,
    # Проверять живость соединения перед выдачей из пула. Без этого после
    # перезапуска PostgreSQL — обслуживание, обновление, failover — пул
    # продолжает раздавать соединения к уже несуществующему процессу, и
    # клиенты получают 500 вместо прозрачного переподключения. Воспроизводится
    # как `docker restart tacitus-postgres` + любой запрос к БД.
    #
    # `pool_recycle` не задан намеренно: pre-ping закрывает и случай мёртвого
    # соединения, и случай соединения, разорванного по таймауту, а
    # профилактическое пересоздание по возрасту решало бы ту же задачу ценой
    # лишних переподключений без наблюдаемой причины.
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db_session():
    """Yield an async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
