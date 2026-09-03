"""Cache key for `allow` decisions only. `deny` and `ask` are never cached."""

import hashlib


def allow_cache_key(profile_hash: str, action_hash: str, user_request: str) -> str:
    payload = f"{profile_hash}\n{action_hash}\n{user_request}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
