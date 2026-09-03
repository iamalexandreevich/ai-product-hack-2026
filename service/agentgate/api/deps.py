"""Authentication seam for the HTTP API.

`make_require_token` is the single place bearer-token verification happens;
every route depends on the dependency it returns rather than comparing
headers inline. That makes the follow-on API-key design (see
docs/superpowers/service/specs/api-keys.md, "Проверка на горячем пути") a
change to this one function -- hash the bearer, look it up, check
revocation/expiry, cache the result briefly in-process -- not a rewrite of
every route.

Comparison uses `secrets.compare_digest`, never `==`: a plain string
comparison leaks the length of the matching prefix by timing, and the
API-key design keeps this same discipline for its hash comparison.
"""

import secrets

from fastapi import Header, HTTPException

from agentgate.config import Settings


def make_require_token(settings: Settings):
    """Build the `require_token` dependency for one app instance.

    - `settings.token` set -> require `Authorization: Bearer <token>`.
    - `settings.token` unset -> allow all. This is safe because
      `Settings.validate_token_for_bind()` already refuses to start the
      service on a non-localhost bind without a token (see `__main__.main`),
      so an unset token here only ever occurs on localhost.
    """
    expected = f"Bearer {settings.token}" if settings.token else None

    async def require_token(authorization: str | None = Header(default=None)) -> None:
        if expected is None:
            return
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    return require_token
