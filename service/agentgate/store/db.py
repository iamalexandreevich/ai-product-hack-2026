"""Engine and session-factory construction for the Postgres store."""

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


def make_engine(db_url: str) -> AsyncEngine:
    return create_async_engine(db_url, pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
