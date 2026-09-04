from agentgate.session.cache_key import allow_cache_key


def test_cache_key_depends_on_all_parts():
    a = allow_cache_key("ph", "ah", "task")
    assert a != allow_cache_key("ph2", "ah", "task")
    assert a != allow_cache_key("ph", "ah2", "task")
    assert a != allow_cache_key("ph", "ah", "task2")
    assert len(a) == 64
