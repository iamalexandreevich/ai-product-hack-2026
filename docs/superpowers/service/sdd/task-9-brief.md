### Task 9: Хранилище Postgres

**Files:**
- Create: `service/agentgate/store/__init__.py`, `service/agentgate/store/models.py`, `service/agentgate/store/db.py`, `service/agentgate/store/repo.py`, `service/alembic.ini`, `service/migrations/env.py`, `service/migrations/script.py.mako`, `service/migrations/versions/0001_init.py`, `service/docker-compose.yml` (только Postgres на этом шаге; сервис добавится в Task 12)
- Test: `service/tests/test_store.py`, правка `service/tests/conftest.py`

**Interfaces:**
- Produces (`agentgate.store.models`): `Base`; `class SessionRow(Base)` таблица `sessions`: `id: str PK`, `harness`, `profile_id`, `workspace`, `created_at`, `last_seen_at`, `deny_consecutive`, `deny_total`, `decisions_total`, `recent_decisions: JSONB`; `class DecisionRow(Base)` таблица `decisions`: `id: str PK (ULID)`, `session_id: str | None FK`, `ts`, `harness`, `tool`, `raw`, `normalized: JSONB`, `user_request`, `profile_id`, `profile_hash`, `decision`, `reason`, `suggest`, `stage: int`, `rule_id`, `model`, `model_raw_response: JSONB | None`, `latency_stage1_ms: int | None`, `latency_stage2_ms: int | None`, `latency_total_ms: int`, `error`, `cached: bool`, `metadata_: JSONB` (колонка `metadata`); `class AllowCacheRow(Base)` таблица `allow_cache`: `session_id FK`, `action_hash`, `decision_id FK`, `expires_at`; PK `(session_id, action_hash)`. Индексы из спеки §7.
- Produces (`agentgate.store.db`): `make_engine(db_url) -> AsyncEngine`; `make_session_factory(engine) -> async_sessionmaker`.
- Produces (`agentgate.store.repo`): `@dataclass class DecisionRecord` (поля 1:1 с `DecisionRow`, `metadata: dict`); `class DecisionRepo(session_factory)`: `async insert(rec: DecisionRecord) -> None`; `async list(session_id: str | None, model: str | None, limit: int, before: str | None) -> list[DecisionRecord]` (по `id` убыванию; `before` — курсор по `id`); `class SessionRepo(session_factory)`: `async upsert(state: SessionState) -> None`; `async load_all() -> list[SessionState]`; `async cache_put(session_id, action_hash, decision_id, expires_at: datetime)`; `async cache_load_valid() -> list[tuple[str, str, str, datetime]]`.
- Тесты требуют `AGENTGATE_TEST_DB_URL` (например `postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test`); без переменной помечаются `skip`.

- [ ] **Step 1: docker-compose с Postgres и conftest**

`service/docker-compose.yml` (первая версия):

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: agentgate
      POSTGRES_PASSWORD: agentgate
      POSTGRES_DB: agentgate
    ports: ["5433:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U agentgate"]
      interval: 2s
      timeout: 2s
      retries: 20
volumes:
  pgdata: {}
```

`service/tests/conftest.py`:

```python
import os

import pytest
import pytest_asyncio
from sqlalchemy import text

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
```

Run: `cd service && docker compose up -d db && docker compose exec db psql -U agentgate -c "CREATE DATABASE agentgate_test;"`
Expected: контейнер запущен, база `agentgate_test` создана. Экспортировать `AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test`.

- [ ] **Step 2: Failing tests**

`service/tests/test_store.py`:

```python
from datetime import datetime, timedelta, timezone

from ulid import ULID

from agentgate.api.schemas import DecisionKind
from agentgate.session.state import SessionState
from agentgate.store.repo import DecisionRecord, DecisionRepo, SessionRepo
from tests.conftest import requires_db

pytestmark = requires_db


def rec(**over) -> DecisionRecord:
    base = dict(
        id=str(ULID()), session_id="s1", ts=datetime.now(timezone.utc), harness="t", tool="shell", raw="ls",
        normalized={"tool": "shell"}, user_request="x", profile_id="default", profile_hash="h" * 64,
        decision="allow", reason="", suggest="", stage=1, rule_id="allowlist.readonly", model=None,
        model_raw_response=None, latency_stage1_ms=1, latency_stage2_ms=None, latency_total_ms=1,
        error=None, cached=False, metadata={"run_id": "r1"},
    )
    base.update(over)
    return DecisionRecord(**base)


async def test_session_upsert_and_load(session_factory):
    repo = SessionRepo(session_factory)
    s = SessionState(session_id="s1", harness="t", profile_id="default", workspace="/w")
    s.record(DecisionKind.deny)
    await repo.upsert(s)
    s.record(DecisionKind.allow)
    await repo.upsert(s)
    loaded = await repo.load_all()
    assert len(loaded) == 1
    assert loaded[0].deny_total == 1 and loaded[0].decisions_total == 2
    assert list(loaded[0].recent) == ["deny", "allow"]


async def test_decision_insert_and_list(session_factory):
    await SessionRepo(session_factory).upsert(SessionState(session_id="s1", harness="t", profile_id="default", workspace="/w"))
    repo = DecisionRepo(session_factory)
    r1, r2, r3 = rec(), rec(model="m"), rec(session_id=None)
    for r in (r1, r2, r3):
        await repo.insert(r)
    all_rows = await repo.list(session_id=None, model=None, limit=10, before=None)
    assert [r.id for r in all_rows] == sorted([r1.id, r2.id, r3.id], reverse=True)
    assert all_rows[0].metadata == {"run_id": "r1"}
    only_s1 = await repo.list(session_id="s1", model=None, limit=10, before=None)
    assert {r.id for r in only_s1} == {r1.id, r2.id}
    only_m = await repo.list(session_id=None, model="m", limit=10, before=None)
    assert [r.id for r in only_m] == [r2.id]
    page = await repo.list(session_id=None, model=None, limit=1, before=all_rows[0].id)
    assert page[0].id == all_rows[1].id


async def test_allow_cache_roundtrip(session_factory):
    srepo = SessionRepo(session_factory)
    await srepo.upsert(SessionState(session_id="s1", harness="t", profile_id="default", workspace="/w"))
    d = rec()
    await DecisionRepo(session_factory).insert(d)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    await srepo.cache_put("s1", "hash-live", d.id, future)
    await srepo.cache_put("s1", "hash-dead", d.id, past)
    rows = await srepo.cache_load_valid()
    assert [(r[0], r[1]) for r in rows] == [("s1", "hash-live")]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest tests/test_store.py -v`
Expected: FAIL, `ModuleNotFoundError: agentgate.store`.

- [ ] **Step 4: models.py и db.py**

`service/agentgate/store/__init__.py`: пустой.

`service/agentgate/store/models.py`:

```python
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    harness: Mapped[str] = mapped_column(String(64))
    profile_id: Mapped[str] = mapped_column(String(64))
    workspace: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deny_consecutive: Mapped[int] = mapped_column(Integer, default=0)
    deny_total: Mapped[int] = mapped_column(Integer, default=0)
    decisions_total: Mapped[int] = mapped_column(Integer, default=0)
    recent_decisions: Mapped[list] = mapped_column(JSONB, default=list)


class DecisionRow(Base):
    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(String(128), ForeignKey("sessions.id"), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    harness: Mapped[str] = mapped_column(String(64))
    tool: Mapped[str] = mapped_column(String(32))
    raw: Mapped[str] = mapped_column(Text)
    normalized: Mapped[dict] = mapped_column(JSONB)
    user_request: Mapped[str] = mapped_column(Text)
    profile_id: Mapped[str] = mapped_column(String(64))
    profile_hash: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(8))
    reason: Mapped[str] = mapped_column(Text, default="")
    suggest: Mapped[str] = mapped_column(Text, default="")
    stage: Mapped[int] = mapped_column(Integer)
    rule_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_raw_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    latency_stage1_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_stage2_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_total_ms: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)

    __table_args__ = (
        Index("ix_decisions_session_ts", "session_id", "ts"),
        Index("ix_decisions_model_ts", "model", "ts"),
        Index("ix_decisions_decision_ts", "decision", "ts"),
        Index("ix_decisions_harness_ts", "harness", "ts"),
        Index("ix_decisions_metadata", "metadata", postgresql_using="gin"),
    )


class AllowCacheRow(Base):
    __tablename__ = "allow_cache"

    session_id: Mapped[str] = mapped_column(String(128), ForeignKey("sessions.id"), primary_key=True)
    action_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision_id: Mapped[str] = mapped_column(String(26), ForeignKey("decisions.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

`service/agentgate/store/db.py`:

```python
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


def make_engine(db_url: str) -> AsyncEngine:
    return create_async_engine(db_url, pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
```

- [ ] **Step 5: repo.py**

`service/agentgate/store/repo.py`:

```python
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from agentgate.session.state import RECENT_MAXLEN, SessionState
from agentgate.store.models import AllowCacheRow, DecisionRow, SessionRow


@dataclass
class DecisionRecord:
    id: str
    session_id: str | None
    ts: datetime
    harness: str
    tool: str
    raw: str
    normalized: dict
    user_request: str
    profile_id: str
    profile_hash: str
    decision: str
    reason: str
    suggest: str
    stage: int
    rule_id: str | None
    model: str | None
    model_raw_response: dict | None
    latency_stage1_ms: int | None
    latency_stage2_ms: int | None
    latency_total_ms: int
    error: str | None
    cached: bool
    metadata: dict

    def to_row(self) -> DecisionRow:
        data = self.__dict__.copy()
        data["metadata_"] = data.pop("metadata")
        return DecisionRow(**data)

    @classmethod
    def from_row(cls, row: DecisionRow) -> "DecisionRecord":
        return cls(
            id=row.id, session_id=row.session_id, ts=row.ts, harness=row.harness, tool=row.tool, raw=row.raw,
            normalized=row.normalized, user_request=row.user_request, profile_id=row.profile_id,
            profile_hash=row.profile_hash, decision=row.decision, reason=row.reason, suggest=row.suggest,
            stage=row.stage, rule_id=row.rule_id, model=row.model, model_raw_response=row.model_raw_response,
            latency_stage1_ms=row.latency_stage1_ms, latency_stage2_ms=row.latency_stage2_ms,
            latency_total_ms=row.latency_total_ms, error=row.error, cached=row.cached, metadata=row.metadata_,
        )

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["ts"] = self.ts.isoformat()
        return d


class DecisionRepo:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def insert(self, rec: DecisionRecord) -> None:
        async with self._sf() as s:
            s.add(rec.to_row())
            await s.commit()

    async def list(self, session_id: str | None, model: str | None, limit: int, before: str | None) -> list[DecisionRecord]:
        stmt = select(DecisionRow).order_by(DecisionRow.id.desc()).limit(limit)
        if session_id is not None:
            stmt = stmt.where(DecisionRow.session_id == session_id)
        if model is not None:
            stmt = stmt.where(DecisionRow.model == model)
        if before is not None:
            stmt = stmt.where(DecisionRow.id < before)
        async with self._sf() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [DecisionRecord.from_row(r) for r in rows]


class SessionRepo:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def upsert(self, state: SessionState) -> None:
        now = datetime.now(timezone.utc)
        values = dict(
            id=state.session_id, harness=state.harness, profile_id=state.profile_id, workspace=state.workspace,
            created_at=now, last_seen_at=now, deny_consecutive=state.deny_consecutive, deny_total=state.deny_total,
            decisions_total=state.decisions_total, recent_decisions=list(state.recent),
        )
        stmt = pg_insert(SessionRow).values(**values)
        update = {k: v for k, v in values.items() if k not in ("id", "created_at")}
        stmt = stmt.on_conflict_do_update(index_elements=[SessionRow.id], set_=update)
        async with self._sf() as s:
            await s.execute(stmt)
            await s.commit()

    async def load_all(self) -> list[SessionState]:
        async with self._sf() as s:
            rows = (await s.execute(select(SessionRow))).scalars().all()
        out = []
        for r in rows:
            st = SessionState(session_id=r.id, harness=r.harness, profile_id=r.profile_id, workspace=r.workspace,
                              deny_consecutive=r.deny_consecutive, deny_total=r.deny_total, decisions_total=r.decisions_total)
            st.recent = deque(r.recent_decisions or [], maxlen=RECENT_MAXLEN)
            out.append(st)
        return out

    async def cache_put(self, session_id: str, action_hash: str, decision_id: str, expires_at: datetime) -> None:
        stmt = pg_insert(AllowCacheRow).values(session_id=session_id, action_hash=action_hash, decision_id=decision_id, expires_at=expires_at)
        stmt = stmt.on_conflict_do_update(index_elements=[AllowCacheRow.session_id, AllowCacheRow.action_hash],
                                          set_={"decision_id": decision_id, "expires_at": expires_at})
        async with self._sf() as s:
            await s.execute(stmt)
            await s.commit()

    async def cache_load_valid(self) -> list[tuple[str, str, str, datetime]]:
        now = datetime.now(timezone.utc)
        stmt = select(AllowCacheRow).where(AllowCacheRow.expires_at > now)
        async with self._sf() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [(r.session_id, r.action_hash, r.decision_id, r.expires_at) for r in rows]
```

- [ ] **Step 6: Alembic**

`service/alembic.ini`:

```ini
[alembic]
script_location = migrations
prepend_sys_path = .
sqlalchemy.url = postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate

[loggers]
keys = root
[handlers]
keys = console
[formatters]
keys = generic
[logger_root]
level = WARN
handlers = console
[handler_console]
class = StreamHandler
args = (sys.stderr,)
formatter = generic
[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

`service/migrations/env.py`:

```python
import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from agentgate.store.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return os.environ.get("AGENTGATE_DB_URL") or config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as conn:
        await conn.run_sync(_run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

`service/migrations/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

Сгенерировать первую миграцию против основной базы (в `alembic.ini` она уже указана):

Run: `cd service && mkdir -p migrations/versions && uv run alembic revision --autogenerate -m "init" --rev-id 0001 && uv run alembic upgrade head`
Expected: файл `migrations/versions/0001_init.py` с тремя таблицами и пятью индексами; `upgrade head` без ошибок. Открыть файл и убедиться, что в нём `create_table("sessions")`, `create_table("decisions")`, `create_table("allow_cache")` и `postgresql_using='gin'` у индекса по `metadata`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest tests/test_store.py -v`
Expected: 3 passed. Без переменной: 3 skipped.

- [ ] **Step 8: Commit**

```bash
git add service/agentgate/store service/alembic.ini service/migrations service/docker-compose.yml service/tests/conftest.py service/tests/test_store.py
git commit -m "feat(service): postgres store, repositories and initial migration

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

