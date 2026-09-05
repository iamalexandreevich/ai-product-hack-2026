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
are cached too. A stage-1 verdict is a function of the output, the
policy and where the output came from; a stage-2 verdict also depends on
the task and the dialogue it was judged against, so both digests are in
the key. Hits mostly happen inside one task (a retry, parallel calls of
one turn), where they match anyway. Where the output came from is
digested whole rather than by kind, because the prompt renders every
provenance field -- the url a `web` fetch names, the command a `shell`
ran -- and a verdict about one page must not be replayed for another.
Whether a neutral-named high-entropy value counts as a secret rides the
key as its own component: it also depends on the workspace, which the
provenance does not carry.
"""

import hashlib

NO_RULES_DIGEST = "no-rules"


def allow_cache_key(
    profile_hash: str, action_hash: str, user_request: str, history_digest: str, rules_digest: str,
) -> str:
    payload = f"{profile_hash}\n{action_hash}\n{user_request}\n{history_digest}\n{rules_digest}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def inspect_cache_key(
    profile_hash: str, provenance_digest: str, output_digest: str, task_digest: str, history_digest: str,
    entropy_candidates: bool,
) -> str:
    candidates = "1" if entropy_candidates else "0"
    payload = (
        f"{profile_hash}\n{provenance_digest}\n{output_digest}\n{task_digest}\n{history_digest}\n{candidates}"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
