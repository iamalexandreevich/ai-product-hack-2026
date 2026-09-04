"""Where the service is assembled.

Everything above this module depends on protocols; this is the one place
that knows which implementations are used in production. Tests and the
CLI call it with substitutes rather than assembling their own variants.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from agentgate.api.app import create_app
from agentgate.classify.llm import build_classifiers
from agentgate.config import Settings
from agentgate.domain.replay import RestorableReplayStore
from agentgate.domain.session import RestorableSessionStateStore
from agentgate.engine.gate import Gate
from agentgate.log.jsonl import JsonlLogger
from agentgate.profiles.loader import load_profiles
from agentgate.rules.chain import STAGE1
from agentgate.session.memory import InMemorySessionStateStore
from agentgate.session.persistent import PersistentSessionStateStore
from agentgate.session.replay import InMemoryReplayStore, PersistentReplayStore
from agentgate.store.db import make_engine, make_session_factory
from agentgate.store.keys import ApiKeyRepo
from agentgate.store.repo import DecisionRepo, SessionRepo
from agentgate.store.writer import (
    CompositeDecisionWriter,
    DecisionWriter,
    JsonlDecisionWriter,
    PostgresDecisionWriter,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Service:
    app: FastAPI
    gate: Gate
    state_store: RestorableSessionStateStore
    replay_store: RestorableReplayStore
    engine: AsyncEngine
    settings: Settings


async def build_service(
    settings: Settings,
    *,
    http: httpx.AsyncClient | None = None,
    state_store: RestorableSessionStateStore | None = None,
    writer: DecisionWriter | None = None,
    replay_store: RestorableReplayStore | None = None,
) -> Service:
    """Assemble the service. Every collaborator can be substituted, so a
    test never has to reproduce this wiring to change one piece of it.

    Raises SystemExit if the configured default profile does not exist,
    and whatever `validate_token_for_bind` raises for an unsafe
    token/bind combination -- both are startup failures.
    """
    settings.validate_token_for_bind()
    profiles = load_profiles(settings.profiles_dir)
    if settings.default_profile not in profiles:
        raise SystemExit(
            f"default profile '{settings.default_profile}' not found in {settings.profiles_dir}"
        )

    engine = make_engine(settings.db_url)
    session_factory = make_session_factory(engine)
    decisions, sessions = DecisionRepo(session_factory), SessionRepo(session_factory)

    store = state_store or PersistentSessionStateStore(InMemorySessionStateStore(), sessions)
    await store.restore()

    replay = replay_store or PersistentReplayStore(
        InMemoryReplayStore(), decisions, settings.allow_cache_ttl_seconds
    )
    await replay.restore()

    http = http or httpx.AsyncClient()
    classifiers = {name: build_classifiers(profile, http) for name, profile in profiles.items()}
    gate = Gate(
        profiles, settings.default_profile, classifiers, STAGE1, store,
        settings.allow_cache_ttl_seconds,
    )

    writer = writer or CompositeDecisionWriter([
        JsonlDecisionWriter(JsonlLogger(settings.log_path)),
        PostgresDecisionWriter(decisions, sessions, settings.allow_cache_ttl_seconds),
    ])
    app = create_app(
        settings, gate, writer, decisions, profiles,
        db_probe=_make_db_probe(engine), key_repo=ApiKeyRepo(session_factory), replay=replay,
    )
    return Service(
        app=app, gate=gate, state_store=store, replay_store=replay, engine=engine, settings=settings,
    )


def build_key_repo(settings: Settings) -> tuple[ApiKeyRepo, AsyncEngine]:
    """The store the keys CLI needs, without assembling the HTTP service."""
    engine = make_engine(settings.db_url)
    return ApiKeyRepo(make_session_factory(engine)), engine


def _make_db_probe(engine: AsyncEngine) -> Callable[[], Awaitable[bool]]:
    async def db_probe() -> bool:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001 - a broken probe reports "not ok", never a 500
            log.warning("database probe failed", exc_info=True)
            return False

    return db_probe
