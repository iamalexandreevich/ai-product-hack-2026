"""Process entrypoint: `uv run python -m agentgate`.

`build_app()` does all the wiring -- read Settings, validate the token/bind
combination, load profiles, build the engine and repositories, restore
session state and the allow-cache from Postgres into
`InMemorySessionStateStore`, and assemble the `Gate` and the FastAPI `app`.
It is deliberately kept separate from `main()`'s `uvicorn.run` call so it can
be exercised in a test (see tests/test_main.py) without booting a real
server -- `uvicorn.run` blocks the calling thread running its own event loop
and cannot be driven from inside a unit test.
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
import uvicorn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from agentgate.api.app import create_app
from agentgate.config import Settings, get_settings
from agentgate.log.jsonl import JsonlLogger
from agentgate.pipeline import Gate
from agentgate.profiles.loader import load_profiles
from agentgate.session.memory import InMemorySessionStateStore
from agentgate.store.db import make_engine, make_session_factory
from agentgate.store.keys import ApiKeyRepo
from agentgate.store.repo import DecisionRepo, SessionRepo

log = logging.getLogger(__name__)


def make_db_probe(engine: AsyncEngine):
    async def db_probe() -> bool:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001 - a broken probe just reports "not ok"
            return False

    return db_probe


async def build_app(settings: Settings | None = None):
    """Build the FastAPI app plus everything wired into it.

    Returns `(app, settings)`. Raises `SystemExit` if the configured
    default profile does not exist, and whatever `validate_token_for_bind`
    raises if the token/bind combination is unsafe -- both are startup
    failures, not something the HTTP layer should ever see.
    """
    settings = settings or get_settings()
    settings.validate_token_for_bind()
    profiles = load_profiles(settings.profiles_dir)
    if settings.default_profile not in profiles:
        raise SystemExit(f"default profile '{settings.default_profile}' not found in {settings.profiles_dir}")

    engine = make_engine(settings.db_url)
    sf = make_session_factory(engine)
    decision_repo, session_repo = DecisionRepo(sf), SessionRepo(sf)
    key_repo = ApiKeyRepo(sf)

    store = InMemorySessionStateStore()
    store.preload(await session_repo.load_all())
    now = datetime.now(timezone.utc)
    for session_id, action_hash, decision_id, expires_at in await session_repo.cache_load_valid():
        await store.cache_put(session_id, action_hash, decision_id, int((expires_at - now).total_seconds()))

    gate = Gate(profiles, settings.default_profile, store, httpx.AsyncClient())
    app = create_app(
        settings, gate, decision_repo, session_repo, profiles, JsonlLogger(settings.log_path),
        db_probe=make_db_probe(engine), key_repo=key_repo,
    )
    return app, settings


def main() -> None:
    """Process entrypoint.

    ``python -m agentgate keys ...`` dispatches to the key-management CLI
    (agentgate.cli) instead of starting the server -- see that module's
    docstring for why key issuance is a CLI concern, not an HTTP endpoint.
    Any other (or no) argument starts the server as before.
    """
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "keys":
        from agentgate.cli import run_keys_cli

        sys.exit(run_keys_cli(sys.argv[2:]))

    logging.basicConfig(level=logging.INFO)
    app, settings = asyncio.run(build_app())
    uvicorn.run(app, host=settings.bind_host, port=settings.bind_port)


if __name__ == "__main__":
    main()
