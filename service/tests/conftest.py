import os

import pytest
import pytest_asyncio


@pytest.fixture(autouse=True)
def _clear_agentgate_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("AGENTGATE_"):
            monkeypatch.delenv(key, raising=False)


TEST_DB_URL = os.environ.get("AGENTGATE_TEST_DB_URL")

requires_db = pytest.mark.skipif(not TEST_DB_URL, reason="AGENTGATE_TEST_DB_URL not set")


@pytest_asyncio.fixture
async def db_engine():
    from agentgate.store.db import make_engine
    from agentgate.store.models import Base

    engine = make_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine):
    from agentgate.store.db import make_session_factory

    return make_session_factory(db_engine)
