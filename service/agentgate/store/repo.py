"""Repositories for decisions, sessions and the allow-cache.

Each repository method opens and commits its own session — no transaction is
held open across calls, so a caller can fire a write after already responding
to the client without an implicit transaction spanning the response.
"""

from collections import deque
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from agentgate.domain.session import RECENT_MAXLEN, SessionState
from agentgate.engine.decision import DecisionRecord
from agentgate.store.mapper import record_from_row
from agentgate.store.models import AllowCacheRow, DecisionRow, SessionRow
from agentgate.store.protocols import Stored


class DecisionRepo:
    """Repository for decision rows.

    Ordering requirement: a decision's session id, when not ``None``, is a
    foreign key to ``sessions.id``. The referenced session must already
    exist, and the caller that guarantees it is
    ``agentgate.store.writer.PostgresDecisionWriter``: it upserts the
    session row, then inserts the decision, then writes the allow-cache
    row, all after the response has been sent. Called out of that order,
    `insert` raises ``sqlalchemy.exc.IntegrityError``.
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._sf = session_factory

    async def insert(self, stored: Stored) -> bool:
        """Insert one row (a decide or an inspect outcome); ``False`` when a
        row with this idempotency key already existed and nothing was
        inserted.

        A row whose ``idempotency_key`` is already present is silently not
        inserted: two concurrent repeats of one call must leave one row --
        the caller (``PostgresDecisionWriter``) uses the return value to
        skip the allow-cache row it would otherwise write next, since that
        row's foreign key requires this insert to have actually landed.
        Raises ``sqlalchemy.exc.IntegrityError`` if the row's session id
        is not ``None`` and does not reference an existing session (see class
        docstring for the required call ordering), or if its id collides
        with an existing decision.
        """
        # Core insert against the Table, keyed by column *names* (so `metadata`
        # is just `metadata`), not the ORM entity with its `metadata_` attribute.
        table = DecisionRow.__table__
        values = stored.to_record().model_dump(exclude={"decision_id"})
        stmt = pg_insert(table).values(**values).on_conflict_do_nothing(
            index_elements=[table.c.idempotency_key],
            index_where=table.c.idempotency_key.isnot(None),
        )
        async with self._sf() as s:
            result = await s.execute(stmt)
            await s.commit()
        return result.rowcount == 1

    async def load_replayable(self, newer_than: datetime, limit: int = 100_000) -> list[DecisionRecord]:
        """Decisions that carried an ``Idempotency-Key`` and are recent enough to replay.

        Ordered newest first and capped at ``limit`` (clamped the same way
        ``list`` clamps its own limit): a populated table could otherwise
        materialize every keyed row of the whole window at once, and
        newest-first keeps the freshest keys when the window has more of
        them than the cap.
        """
        limit = max(1, min(limit, 1_000_000))
        stmt = (
            select(DecisionRow)
            .where(DecisionRow.idempotency_key.isnot(None), DecisionRow.ts > newer_than)
            .order_by(DecisionRow.ts.desc())
            .limit(limit)
        )
        async with self._sf() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [record_from_row(r) for r in rows]

    async def list(
        self, session_id: str | None, model: str | None, limit: int, before: str | None, kind: str | None = None
    ) -> list[DecisionRecord]:
        """List decisions ordered by ``id`` descending (newest first).

        ``before`` pages backwards: only rows with ``id < before`` are
        returned. ``limit`` is clamped to ``[1, 1000]`` regardless of the
        input value, so a caller does not need to validate it and a
        pathological value (zero, negative, or unbounded) cannot turn this
        into a database error or an unbounded read. ``kind`` filters to
        ``"decide"`` or ``"inspect"`` rows; omitted, both kinds are returned.
        """
        limit = max(1, min(limit, 1000))
        stmt = select(DecisionRow).order_by(DecisionRow.id.desc()).limit(limit)
        if session_id is not None:
            stmt = stmt.where(DecisionRow.session_id == session_id)
        if model is not None:
            stmt = stmt.where(DecisionRow.model == model)
        if before is not None:
            stmt = stmt.where(DecisionRow.id < before)
        if kind is not None:
            stmt = stmt.where(DecisionRow.kind == kind)
        async with self._sf() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [record_from_row(r) for r in rows]


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

    async def ensure(self, session_id: str, workspace: str) -> None:
        """Make sure a bare session row exists for `session_id`, without
        touching the counters or workspace of a row that already exists.

        Used by the writer for an `Inspection`: it needs
        `decisions.session_id`'s foreign key satisfied, but it never decided
        anything itself, so it must not invent counters for -- or overwrite
        the workspace of -- a session `upsert` (above) already owns.
        """
        now = datetime.now(timezone.utc)
        stmt = pg_insert(SessionRow).values(
            id=session_id, harness="", profile_id="", workspace=workspace,
            created_at=now, last_seen_at=now, deny_consecutive=0, deny_total=0,
            decisions_total=0, recent_decisions=[],
        ).on_conflict_do_nothing(index_elements=[SessionRow.id])
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
        """Upsert one allow-cache entry.

        ``decision_id`` is a foreign key to ``decisions.id`` and ``session_id``
        to ``sessions.id`` — both rows must already exist (see
        ``DecisionRepo``'s docstring for the required ordering), or this
        raises ``sqlalchemy.exc.IntegrityError``.

        ``expires_at`` must be timezone-aware. A naive value is not treated
        as UTC — the driver reinterprets it through the connection's session
        timezone — which would silently shift when the cache entry expires
        relative to the tz-aware ``now`` used by ``cache_load_valid``.
        """
        if expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware (got a naive datetime)")
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
