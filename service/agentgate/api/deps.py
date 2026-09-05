"""Authentication seam for the HTTP API.

`make_require_token` is the single place bearer-token verification happens;
every route depends on the dependency it returns rather than comparing
headers inline.

Controller override on top of docs/superpowers/service/specs/api-keys.md:
that spec's stricter posture -- non-localhost binds ignore `AGENTGATE_TOKEN`
entirely and accept only issued keys -- is deliberately NOT implemented
here. `validate_token_for_bind` (agentgate.config.Settings) is untouched.
Instead, keys are ADDITIVE: a bearer authenticates if it matches the static
`AGENTGATE_TOKEN` (the original `secrets.compare_digest` path, unchanged) OR
a currently valid issued API key. This keeps every existing auth test
green while adding keys as an extra accepted credential. The stricter
"non-localhost ignores the static token" posture is deferred as optional
hardening for a later task.

Comparison uses `secrets.compare_digest`, never `==`: a plain string
comparison leaks the length of the matching prefix by timing.

Verification on the hot path: bearer -> SHA-256 -> lookup among
non-revoked, non-expired keys, cached in-process with a short TTL (see
`_KeyVerifier`) so a valid key does not hit Postgres on every `/v1/decide`.
Fail-closed throughout: any error verifying a key (DB down, or any other
exception) is treated as "not a match", never an authenticated pass -- see
`_KeyVerifier.verify`. A cache entry can therefore serve a now-revoked key
for up to the TTL window; this is a deliberate, documented trade-off (see
the spec's "Отзыв ограничен временем жизни кэша").

The dependency's value is the resolved `key_id` (None for the static
token), which api/app.py attaches to the outcome so a decision can be
attributed to the credential that asked for it. The key itself and its
hash never leave this module.
"""

import logging
import secrets
import time
from collections.abc import Callable

from fastapi import BackgroundTasks, Header, HTTPException

from agentgate.config import Settings
from agentgate.store.keys import ApiKeyRepo, hash_key

log = logging.getLogger(__name__)

# Spec: "TTL надо держать коротким (30-60 с)."
DEFAULT_KEY_CACHE_TTL_SECONDS = 45.0

_BEARER_PREFIX = "Bearer "

BEARER_SCHEME = {
    "type": "http",
    "scheme": "bearer",
    "description": """\
`Authorization: Bearer <credential>`. The credential is EITHER the static
`AGENTGATE_TOKEN` (set in the service's environment) OR an issued API key. A
request authenticates if the bearer matches either one; both are checked in
constant time.

**API keys** are minted by the operator from the CLI, never over HTTP:
`python -m agentgate keys create --label <name>` (also `keys list`,
`keys revoke <key_id>`). A key is shown exactly once at creation, looks like
`agk_` followed by 43 URL-safe characters, and is stored only as a SHA-256 hash
— the plaintext is never persisted or logged. A revoked or expired key stops
working within a short in-process cache TTL (revocation is not instant, up to a
minute). Hand each integrator their own key so it can be revoked individually.

OpenAPI cannot express the conditional rule, so it is stated here: on a
non-localhost bind a credential is required (the service will not start without a
token, and issued keys are also accepted); on a localhost bind with no token and
no keys, every request passes (dev mode) — which is why the scheme is declared
optional in `security`. `GET /healthz` never requires a credential. A missing,
wrong, expired or revoked credential all return the same opaque HTTP 401 (the
body does not say which); every other failure is a 200 `ask`, not a 401.
""",
}


class _KeyVerifier:
    """Verifies a bearer token as an API key, with a short in-process TTL cache.

    The cache is keyed by the SHA-256 hash of the token (never the plaintext)
    and stores the resolved `key_id` (or `None` for "not currently valid" --
    absent, revoked, or expired all collapse to the same cached outcome, so
    the 401 they produce cannot be distinguished from cache state either).

    Fail-closed: `verify` never raises. Any exception from the repository
    (a DB outage, a timeout) is treated as "not a match" for this call only
    -- it is deliberately NOT cached, so the next request retries against
    the store rather than being locked out (or let in) for the TTL window
    by a transient failure.
    """

    def __init__(
        self, key_repo: ApiKeyRepo, ttl_seconds: float, now_fn: Callable[[], float]
    ) -> None:
        self._repo = key_repo
        self._ttl = ttl_seconds
        self._now = now_fn
        self._cache: dict[str, tuple[str | None, float]] = {}

    async def verify(self, token: str) -> str | None:
        key_hash = hash_key(token)
        now = self._now()

        cached = self._cache.get(key_hash)
        if cached is not None:
            key_id, cached_at = cached
            if now - cached_at < self._ttl:
                return key_id

        try:
            record = await self._repo.get_by_hash(key_hash)
        except Exception as exc:  # noqa: BLE001 - fail closed: a store error is never a match
            log.error("api key verification failed (treating as no match): %s", exc)
            return None

        key_id = record.id if (record is not None and record.is_valid()) else None
        self._cache[key_hash] = (key_id, now)
        return key_id


async def _touch_last_used_safe(key_repo: ApiKeyRepo, key_id: str) -> None:
    """Best-effort `last_used_at` update, run after the response is sent.

    Mirrors the "persist after response, swallow the error" discipline
    already used for decision persistence in agentgate.api.app -- a failure
    here must never surface to a client that already has its answer.
    """
    try:
        await key_repo.touch_last_used(key_id)
    except Exception as exc:  # noqa: BLE001 - background write, must never propagate
        log.error("failed to update last_used_at for key %s: %s", key_id, exc)


def make_require_token(
    settings: Settings,
    key_repo: ApiKeyRepo | None = None,
    cache_ttl_seconds: float = DEFAULT_KEY_CACHE_TTL_SECONDS,
    now_fn: Callable[[], float] = time.monotonic,
):
    """Build the `require_token` dependency for one app instance.

    - `settings.token` unset -> allow all. Unchanged from before keys
      existed: `Settings.validate_token_for_bind()` already refuses to
      start the service on a non-localhost bind without a token (see
      `__main__.main`), so an unset token here only ever occurs on
      localhost, and this dev-mode fallback does not consult `key_repo`.
    - `settings.token` set -> require a bearer that matches it
      (`secrets.compare_digest`, unchanged) OR a currently valid API key
      when `key_repo` is given. `key_repo=None` (no DB, unit tests) falls
      back to the static-token-only behavior exactly as before.
    """
    expected = f"Bearer {settings.token}" if settings.token else None
    verifier = _KeyVerifier(key_repo, cache_ttl_seconds, now_fn) if key_repo is not None else None

    async def require_token(
        background: BackgroundTasks,
        authorization: str | None = Header(default=None, include_in_schema=False),
    ) -> str | None:
        """The `key_id` this call is attributed to, or None.

        None means "authenticated, but not by an issued key": the static
        `AGENTGATE_TOKEN`, or a localhost dev bind with no token at all.
        The static token is checked first, so a bearer that somehow matches
        both loses its attribution rather than gaining someone else's.
        """
        if expected is None:
            return None
        if authorization is not None and secrets.compare_digest(authorization, expected):
            return None
        if verifier is not None and authorization is not None and authorization.startswith(_BEARER_PREFIX):
            token = authorization[len(_BEARER_PREFIX):]
            key_id = await verifier.verify(token)
            if key_id is not None:
                background.add_task(_touch_last_used_safe, key_repo, key_id)
                return key_id
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    return require_token
