"""Cache key builders, kept in one module so a decide/inspect key is never
defined twice.

`allow_cache_key` is for `allow` decisions only -- `deny` and `ask` are
never cached. The history digest is part of the key on purpose: without
it an `allow` granted in a benign context would be replayed for the same
action after a hostile tool result entered the dialogue. The price is
that the cache only hits on an exact repeat (a harness retry, parallel
calls of one turn) -- it is a deduplicator, not an accelerator.

The rules digest is part of the key for the same reason: an `allow`
granted while the caller's client-side `rules` were permissive must not
be replayed once the same session sends stricter (or no) rules for the
same action. `ClientRules.digest()` already ignores pattern order and
duplicates, so resending the same rules in a different order still hits
the cache; the caller passes a fixed sentinel when there are no rules at
all, so "no rules" is its own digest rather than colliding with any
real one.

`inspect_cache_key` is for the inspect route, where `mask` and `drop`
are cached too: a verdict on tool output is a function of the output,
the policy and where the output came from, so those three are the whole
key.
"""

import hashlib

NO_RULES_DIGEST = "no-rules"


def allow_cache_key(
    profile_hash: str, action_hash: str, user_request: str, history_digest: str, rules_digest: str,
) -> str:
    payload = f"{profile_hash}\n{action_hash}\n{user_request}\n{history_digest}\n{rules_digest}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def inspect_cache_key(profile_hash: str, provenance_kind: str, output_digest: str) -> str:
    return f"{profile_hash}:{provenance_kind}:{output_digest}"
