"""SQLAlchemy ORM models for the Postgres store: sessions, decisions, allow cache.

Postgres only (asyncpg driver, JSONB columns). No SQLite fallback.
"""

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


class ApiKeyRow(Base):
    """Issued API keys (docs/superpowers/service/specs/api-keys.md).

    Only ``key_hash`` (SHA-256 of the plaintext key) is ever stored -- the
    plaintext exists only transiently in ``ApiKeyRepo.create``'s return value
    and is never written here. ``id`` is a ULID and doubles as the public
    ``key_id`` used in the CLI, in ``DecisionRow``/log attribution, and for
    revocation -- it never encodes or derives from the key itself.
    """

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
