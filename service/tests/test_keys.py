"""Tests for agentgate.store.keys: generation, hashing, and the api_keys repo.

Generation/hashing tests need no database. The repo round-trip tests do and
follow the project's established DB-test guard (tests/conftest.py).
"""

import base64
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from agentgate.store.keys import KEY_PREFIX, ApiKeyRecord, ApiKeyRepo, generate_key, hash_key
from tests.conftest import requires_db

# --- generation: shape and entropy -------------------------------------------


def test_generate_key_has_agk_prefix():
    key = generate_key()
    assert key.startswith(KEY_PREFIX)


def test_generate_key_decodes_to_32_bytes_of_entropy():
    key = generate_key()
    token = key[len(KEY_PREFIX):]
    # no padding: base64url without '='
    assert "=" not in token
    decoded = base64.urlsafe_b64decode(token + "==")  # re-add padding for decode
    assert len(decoded) == 32


def test_generate_key_is_random_each_call():
    keys = {generate_key() for _ in range(50)}
    assert len(keys) == 50


# --- hashing -------------------------------------------------------------


def test_hash_key_is_sha256_hex_digest():
    key = "agk_abc123"
    assert hash_key(key) == hashlib.sha256(key.encode("utf-8")).hexdigest()


def test_hash_key_is_deterministic_and_distinct_for_distinct_keys():
    k1, k2 = generate_key(), generate_key()
    assert hash_key(k1) == hash_key(k1)
    assert hash_key(k1) != hash_key(k2)


# --- ApiKeyRecord.is_valid: revocation and expiry ----------------------------


def _rec(**over) -> ApiKeyRecord:
    base = dict(id="k1", label="l", created_at=datetime.now(timezone.utc), expires_at=None,
                revoked_at=None, last_used_at=None)
    base.update(over)
    return ApiKeyRecord(**base)


def test_record_valid_when_no_revoke_no_expiry():
    assert _rec().is_valid() is True


def test_record_invalid_when_revoked():
    assert _rec(revoked_at=datetime.now(timezone.utc)).is_valid() is False


def test_record_invalid_when_expired():
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert _rec(expires_at=past).is_valid() is False


def test_record_valid_when_expiry_in_future():
    future = datetime.now(timezone.utc) + timedelta(days=1)
    assert _rec(expires_at=future).is_valid() is True


# --- repo: create/get/list/revoke/touch (needs DB) ---------------------------
#
# Applied per-function, not as a module-level `pytestmark`, because a
# module-level `pytestmark` would also skip the generation/hashing tests
# above that need no database at all.


@requires_db
async def test_create_returns_plaintext_once_and_stores_only_hash(session_factory):
    repo = ApiKeyRepo(session_factory)
    plaintext, record = await repo.create(label="ci")

    assert plaintext.startswith(KEY_PREFIX)
    assert record.label == "ci"
    assert record.revoked_at is None
    assert record.expires_at is None

    # the stored row carries only the hash -- never the plaintext, anywhere.
    from sqlalchemy import select

    from agentgate.store.models import ApiKeyRow
    async with session_factory() as s:
        row = (await s.execute(select(ApiKeyRow).where(ApiKeyRow.id == record.id))).scalar_one()
    assert row.key_hash == hash_key(plaintext)
    assert plaintext not in row.key_hash
    assert not hasattr(row, "key")  # no plaintext column exists at all


@requires_db
async def test_get_by_hash_finds_a_valid_key(session_factory):
    repo = ApiKeyRepo(session_factory)
    plaintext, record = await repo.create(label="ci")
    found = await repo.get_by_hash(hash_key(plaintext))
    assert found is not None
    assert found.id == record.id
    assert found.is_valid() is True


@requires_db
async def test_get_by_hash_returns_none_for_unknown_hash(session_factory):
    repo = ApiKeyRepo(session_factory)
    assert await repo.get_by_hash("0" * 64) is None


@requires_db
async def test_get_by_hash_returns_revoked_key_marked_invalid(session_factory):
    repo = ApiKeyRepo(session_factory)
    plaintext, record = await repo.create(label="ci")
    await repo.revoke(record.id)
    found = await repo.get_by_hash(hash_key(plaintext))
    assert found is not None
    assert found.is_valid() is False
    assert found.revoked_at is not None


@requires_db
async def test_get_by_hash_returns_expired_key_marked_invalid(session_factory):
    repo = ApiKeyRepo(session_factory)
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    plaintext, record = await repo.create(label="ci", expires_at=past)
    found = await repo.get_by_hash(hash_key(plaintext))
    assert found is not None
    assert found.is_valid() is False


@requires_db
async def test_revoke_is_idempotent_and_reports_unknown_id(session_factory):
    repo = ApiKeyRepo(session_factory)
    plaintext, record = await repo.create(label="ci")
    assert await repo.revoke(record.id) is True
    first_revoked_at = (await repo.get_by_hash(hash_key(plaintext))).revoked_at
    assert await repo.revoke(record.id) is True  # idempotent: still True, does not move revoked_at
    assert (await repo.get_by_hash(hash_key(plaintext))).revoked_at == first_revoked_at
    assert await repo.revoke("does-not-exist") is False


@requires_db
async def test_list_never_exposes_key_or_hash(session_factory):
    repo = ApiKeyRepo(session_factory)
    await repo.create(label="a")
    await repo.create(label="b")
    records = await repo.list()
    assert len(records) == 2
    for r in records:
        assert not hasattr(r, "key")
        assert not hasattr(r, "key_hash")
    assert {r.label for r in records} == {"a", "b"}


@requires_db
async def test_touch_last_used_sets_timestamp(session_factory):
    repo = ApiKeyRepo(session_factory)
    plaintext, record = await repo.create(label="ci")
    assert record.last_used_at is None
    await repo.touch_last_used(record.id)
    found = await repo.get_by_hash(hash_key(plaintext))
    assert found.last_used_at is not None


@requires_db
async def test_touch_last_used_on_unknown_id_is_a_no_op(session_factory):
    repo = ApiKeyRepo(session_factory)
    await repo.touch_last_used("does-not-exist")  # must not raise
