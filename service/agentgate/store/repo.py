"""Repositories for decisions, sessions and the allow-cache.

Each repository method opens and commits its own session — no transaction is
held open across calls, so a caller can fire a write after already responding
to the client without an implicit transaction spanning the response.
"""

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
