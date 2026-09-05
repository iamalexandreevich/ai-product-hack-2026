from agentgate.session.cache_key import allow_cache_key, inspect_cache_key


def test_cache_key_depends_on_all_parts():
    a = allow_cache_key("ph", "ah", "task", "hd", "rd")
    assert a != allow_cache_key("ph2", "ah", "task", "hd", "rd")
    assert a != allow_cache_key("ph", "ah2", "task", "hd", "rd")
    assert a != allow_cache_key("ph", "ah", "task2", "hd", "rd")
    assert a != allow_cache_key("ph", "ah", "task", "hd2", "rd")
    assert a != allow_cache_key("ph", "ah", "task", "hd", "rd2")
    assert len(a) == 64


def test_same_action_with_a_different_history_gets_a_different_key():
    assert allow_cache_key("ph", "ah", "task", "history-a", "rd") != allow_cache_key("ph", "ah", "task", "history-b", "rd")


def test_same_action_with_a_different_rules_digest_gets_a_different_key():
    # An `allow` granted under permissive client rules must not survive
    # into a call that arrives with stricter (or no) rules for the same
    # action.
    assert allow_cache_key("ph", "ah", "task", "hd", "permissive") != allow_cache_key("ph", "ah", "task", "hd", "strict")


def test_inspect_cache_key_depends_on_task_and_history_too():
    a = inspect_cache_key("ph", "pd", "od", "td", "hd", True)
    assert a != inspect_cache_key("ph", "pd", "od", "td2", "hd", True)
    assert a != inspect_cache_key("ph", "pd", "od", "td", "hd2", True)
    assert a != inspect_cache_key("ph", "pd2", "od", "td", "hd", True)
    assert a != inspect_cache_key("ph2", "pd", "od", "td", "hd", True)
    assert a != inspect_cache_key("ph", "pd", "od2", "td", "hd", True)
    assert len(a) == 64


def test_inspect_cache_key_depends_on_the_entropy_candidate_policy():
    # Two shell commands share a provenance but not the policy that
    # decides whether a neutral-named value is redacted.
    assert inspect_cache_key("ph", "pd", "od", "td", "hd", True) != inspect_cache_key(
        "ph", "pd", "od", "td", "hd", False
    )
