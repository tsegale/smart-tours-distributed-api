from collections.abc import AsyncIterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
)


@event.listens_for(engine, "connect")
def _set_lock_timeout(dbapi_connection, _record) -> None:
    # Bound how long a booking can queue behind a FOR UPDATE lock, so a stuck transaction
    # can't pile up every request for a popular departure.
    with dbapi_connection.cursor() as cursor:
        cursor.execute(f"SET lock_timeout = {int(settings.lock_timeout_ms)}")
    dbapi_connection.commit()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncIterator[Session]:
    """One session per request.

    Deliberately an async dependency even though the session is synchronous: FastAPI runs the
    teardown of a *sync* yield-dependency in the threadpool, so under load every thread can end
    up blocked in pool.connect() waiting for connections that only those teardowns can return,
    and nothing moves until the pool timeout. Endpoints finish their transaction before they
    return (see routers), so close() here does no I/O on the event loop.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
