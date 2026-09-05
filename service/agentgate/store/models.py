"""SQLAlchemy ORM models for the Postgres store: sessions, decisions, allow cache.

Postgres only (asyncpg driver, JSONB columns). No SQLite fallback.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Computed, DateTime, ForeignKey, Index, Integer, String, Text, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from agentgate.api.schemas import IDEMPOTENCY_KEY_MAX_CHARS, PROTOCOL
from agentgate.domain.principal import KEY_ID_PATTERN, STATIC_PRINCIPAL


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
    protocol: Mapped[int] = mapped_column(Integer, default=PROTOCOL)
    idempotency_key: Mapped[str | None] = mapped_column(String(IDEMPOTENCY_KEY_MAX_CHARS), nullable=True)
    history: Mapped[list] = mapped_column(JSONB, default=list)
    history_omitted: Mapped[int] = mapped_column(Integer, default=0)
    history_digest: Mapped[str] = mapped_column(String(64), default="")
    request_digest: Mapped[str] = mapped_column(String(64), default="")
    kind: Mapped[str] = mapped_column(String(8), default="decide")
    call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rules_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rules_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provenance: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    replacement: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    spans: Mapped[list] = mapped_column(JSONB, default=list)
    redacted: Mapped[int] = mapped_column(Integer, default=0)
    spans_rejected: Mapped[int] = mapped_column(Integer, default=0)
    key_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    # Generated, not written: the replay uniqueness key must have no NULLs
    # (in Postgres NULL <> NULL, so a unique index over a nullable key_id
    # would let every static-token caller reuse one idempotency key), and a
    # generated column cannot drift from key_id the way a second writable
    # column could.
    principal: Mapped[str] = mapped_column(
        String(26), Computed(f"coalesce(key_id, '{STATIC_PRINCIPAL}')", persisted=True)
    )

    __table_args__ = (
        Index("ix_decisions_session_ts", "session_id", "ts"),
        Index("ix_decisions_model_ts", "model", "ts"),
        Index("ix_decisions_decision_ts", "decision", "ts"),
        Index("ix_decisions_harness_ts", "harness", "ts"),
        Index("ix_decisions_metadata", "metadata", postgresql_using="gin"),
        Index("ix_decisions_kind_id", "kind", "id"),
        Index("ix_decisions_call_id", "call_id"),
        Index("ix_decisions_key_id_id", "key_id", "id"),
        # The triple, not the pair: one integrator running two sessions under
        # one key lost the second session's audit row to the pair. NULLS NOT
        # DISTINCT because session_id is nullable and a sessionless call must
        # still compete for its key -- in Postgres NULL <> NULL otherwise.
        Index(
            "ux_decisions_principal_session_idempotency_key",
            "principal", "session_id", "idempotency_key", unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
            postgresql_nulls_not_distinct=True,
        ),
        # The generated `principal` above is only a namespace while no key id
        # can spell STATIC_PRINCIPAL. This is where that stops being a habit.
        CheckConstraint(
            f"key_id IS NULL OR key_id ~ '{KEY_ID_PATTERN}'", name="ck_decisions_key_id_ulid"
        ),
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
    and is never written here. ``id`` is a ULID and is the public ``key_id``
    used by the CLI (``keys list`` / ``keys revoke``, see ``agentgate/cli.py``)
    and for revocation -- it never encodes or derives from the key itself.

    ``id`` is what a decision row stores in its ``key_id`` column, so a
    decision can be attributed to the key that asked for it.
    """

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(f"id ~ '{KEY_ID_PATTERN}'", name="ck_api_keys_id_ulid"),
    )
