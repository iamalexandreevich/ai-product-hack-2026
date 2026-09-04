from agentgate.session.cache_key import allow_cache_key


def test_cache_key_depends_on_all_parts():
    a = allow_cache_key("ph", "ah", "task", "hd")
    assert a != allow_cache_key("ph2", "ah", "task", "hd")
    assert a != allow_cache_key("ph", "ah2", "task", "hd")
    assert a != allow_cache_key("ph", "ah", "task2", "hd")
    assert a != allow_cache_key("ph", "ah", "task", "hd2")
    assert len(a) == 64


def test_same_action_with_a_different_history_gets_a_different_key():
    assert allow_cache_key("ph", "ah", "task", "history-a") != allow_cache_key("ph", "ah", "task", "history-b")
