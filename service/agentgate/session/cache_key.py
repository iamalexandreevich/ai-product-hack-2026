"""Cache key for `allow` decisions only. `deny` and `ask` are never cached.

The history digest is part of the key on purpose: without it an `allow`
granted in a benign context would be replayed for the same action after a
hostile tool result entered the dialogue. The price is that the cache only
hits on an exact repeat (a harness retry, parallel calls of one turn) --
it is a deduplicator, not an accelerator.
"""

import hashlib


def allow_cache_key(profile_hash: str, action_hash: str, user_request: str, history_digest: str) -> str:
    payload = f"{profile_hash}\n{action_hash}\n{user_request}\n{history_digest}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
