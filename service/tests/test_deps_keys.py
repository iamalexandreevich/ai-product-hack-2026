"""Tests for the API-key half of agentgate.api.deps.make_require_token.

Controller override (see the task brief, not the spec verbatim): keys are
ADDITIVE. A request authenticates if the bearer matches the static
AGENTGATE_TOKEN (unchanged `secrets.compare_digest` path) OR a currently
valid issued key. `validate_token_for_bind` is untouched -- these tests
exercise only `make_require_token`.

All tests here call `require_token` directly (no ASGI transport) so the
in-process TTL cache can be driven with a fake clock -- these are unit tests
of the caching/fail-closed logic, not of the HTTP wiring (that's covered in
tests/test_api.py's existing token tests plus the wiring test added there
for the background last-used touch).
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from agentgate.api.deps import make_require_token
from agentgate.config import Settings
from agentgate.store.keys import ApiKeyRecord, hash_key


def _settings(token="secret"):
    return Settings(db_url="postgresql+asyncpg://x", token=token, bind="127.0.0.1:8400")


def _valid_record(key_id="k1"):
    return ApiKeyRecord(id=key_id, label="l", created_at=datetime.now(timezone.utc),
                        expires_at=None, revoked_at=None, last_used_at=None)


class FakeKeyRepo:
    """A key repo whose `get_by_hash` is counted and independently scriptable
    per call, so tests can assert "no second DB hit" and simulate a
    revocation becoming visible.
    """

    def __init__(self, by_hash: dict[str, ApiKeyRecord | None] | None = None, raise_on_call: bool = False):
        self.by_hash = by_hash or {}
        self.calls = 0
        self.raise_on_call = raise_on_call
        self.touched: list[str] = []

    async def get_by_hash(self, key_hash: str):
        self.calls += 1
        if self.raise_on_call:
            raise RuntimeError("db down")
        return self.by_hash.get(key_hash)

    async def touch_last_used(self, key_id: str) -> None:
        self.touched.append(key_id)


async def _expect_401(coro):
    with pytest.raises(HTTPException) as exc_info:
        await coro
    assert exc_info.value.status_code == 401


# --- additive auth: static token OR a valid key ------------------------------


async def test_accepts_valid_api_key_when_static_token_also_set():
    plaintext = "agk_" + "a" * 20
    repo = FakeKeyRepo(by_hash={hash_key(plaintext): _valid_record()})
    require_token = make_require_token(_settings(token="secret"), key_repo=repo)
    await require_token(authorization=f"Bearer {plaintext}")  # must not raise


async def test_still_accepts_static_token_when_key_repo_present():
    repo = FakeKeyRepo()
    require_token = make_require_token(_settings(token="secret"), key_repo=repo)
    await require_token(authorization="Bearer secret")  # must not raise
    assert repo.calls == 0  # static token matched first -- no need to touch the key store


async def test_401_on_bad_bearer_with_key_repo_present():
    repo = FakeKeyRepo()
    require_token = make_require_token(_settings(token="secret"), key_repo=repo)
    await _expect_401(require_token(authorization="Bearer nope"))


async def test_401_on_missing_bearer_with_key_repo_present():
    repo = FakeKeyRepo()
    require_token = make_require_token(_settings(token="secret"), key_repo=repo)
    await _expect_401(require_token(authorization=None))


async def test_revoked_key_is_rejected():
    plaintext = "agk_" + "b" * 20
    revoked = _valid_record()
    revoked.revoked_at = datetime.now(timezone.utc)
    repo = FakeKeyRepo(by_hash={hash_key(plaintext): revoked})
    require_token = make_require_token(_settings(token="secret"), key_repo=repo)
    await _expect_401(require_token(authorization=f"Bearer {plaintext}"))


async def test_expired_key_is_rejected():
    plaintext = "agk_" + "c" * 20
    expired = _valid_record()
    expired.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    repo = FakeKeyRepo(by_hash={hash_key(plaintext): expired})
    require_token = make_require_token(_settings(token="secret"), key_repo=repo)
    await _expect_401(require_token(authorization=f"Bearer {plaintext}"))


async def test_unknown_key_is_rejected():
    repo = FakeKeyRepo(by_hash={})
    require_token = make_require_token(_settings(token="secret"), key_repo=repo)
    await _expect_401(require_token(authorization="Bearer agk_totally-unknown"))


# --- dev-mode fallback (no static token) is unchanged ------------------------


async def test_dev_mode_no_token_allows_all_even_with_key_repo_present():
    repo = FakeKeyRepo()
    require_token = make_require_token(_settings(token=None), key_repo=repo)
    await require_token(authorization=None)  # must not raise -- unchanged localhost dev mode
    assert repo.calls == 0


# --- no key_repo at all: existing tests keep working unmodified -------------


async def test_no_key_repo_falls_back_to_static_token_only():
    require_token = make_require_token(_settings(token="secret"))
    await require_token(authorization="Bearer secret")
    await _expect_401(require_token(authorization="Bearer wrong"))


# --- TTL cache: a repeat within the window costs no second DB hit -----------


async def test_cache_serves_repeat_without_second_db_hit():
    plaintext = "agk_" + "d" * 20
    repo = FakeKeyRepo(by_hash={hash_key(plaintext): _valid_record()})
    clock = [0.0]
    require_token = make_require_token(_settings(token="secret"), key_repo=repo,
                                       cache_ttl_seconds=30, now_fn=lambda: clock[0])

    await require_token(authorization=f"Bearer {plaintext}")
    assert repo.calls == 1
    clock[0] += 5  # well within the 30s TTL
    await require_token(authorization=f"Bearer {plaintext}")
    assert repo.calls == 1  # served from cache, no second DB hit


async def test_revocation_takes_effect_only_after_the_cache_ttl():
    plaintext = "agk_" + "e" * 20
    key_hash = hash_key(plaintext)
    record = _valid_record()
    repo = FakeKeyRepo(by_hash={key_hash: record})
    clock = [0.0]
    require_token = make_require_token(_settings(token="secret"), key_repo=repo,
                                       cache_ttl_seconds=30, now_fn=lambda: clock[0])

    await require_token(authorization=f"Bearer {plaintext}")  # populates the cache as valid
    assert repo.calls == 1

    record.revoked_at = datetime.now(timezone.utc)  # simulate revocation landing in the store

    clock[0] += 10  # still inside the TTL window
    await require_token(authorization=f"Bearer {plaintext}")  # must not raise -- served from cache
    assert repo.calls == 1  # no re-query yet

    clock[0] += 25  # now past the 30s TTL (35s since the first call)
    await _expect_401(require_token(authorization=f"Bearer {plaintext}"))
    assert repo.calls == 2  # the cache expired and re-queried the store, which now says revoked


# --- fail closed: any error verifying a key is a 401, never a pass ----------


async def test_db_error_during_key_verification_is_401_not_a_pass():
    repo = FakeKeyRepo(raise_on_call=True)
    require_token = make_require_token(_settings(token="secret"), key_repo=repo)
    await _expect_401(require_token(authorization="Bearer agk_whatever"))


async def test_db_error_does_not_poison_the_cache_as_a_permanent_pass():
    plaintext = "agk_" + "f" * 20
    repo = FakeKeyRepo(raise_on_call=True)
    require_token = make_require_token(_settings(token="secret"), key_repo=repo)
    await _expect_401(require_token(authorization=f"Bearer {plaintext}"))
    repo.raise_on_call = False
    repo.by_hash[hash_key(plaintext)] = _valid_record()
    await require_token(authorization=f"Bearer {plaintext}")  # recovers once the store is healthy again
