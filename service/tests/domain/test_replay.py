from agentgate.domain.replay import STATIC_PRINCIPAL, Replay, ReplayKey, principal_of
from tests.factories import decide_request, decision


def record(key: str = "k", key_id: str | None = None):
    return decision(idempotency_key=key, key_id=key_id).to_record()


def test_a_key_id_is_its_own_principal():
    assert principal_of("01HZKEYA") == "01HZKEYA"


def test_the_static_token_is_one_named_principal():
    assert principal_of(None) == STATIC_PRINCIPAL == "token"


def test_the_storage_key_joins_principal_and_key():
    assert ReplayKey.of("01HZKEYA", "abc").storage_key() == "01HZKEYA:abc"


def test_the_storage_key_of_the_static_token_is_namespaced_too():
    assert ReplayKey.of(None, "abc").storage_key() == "token:abc"


def test_a_replay_takes_its_principal_from_the_record():
    assert Replay.of(record(key_id="01HZKEYA")).principal == "01HZKEYA"


def test_a_replay_answers_its_own_principal():
    stored = Replay.of(record(key_id="01HZKEYA"))

    assert stored.answers(decide_request("ls -la"), "01HZKEYA") is True


def test_a_replay_does_not_answer_another_principal():
    stored = Replay.of(record(key_id="01HZKEYA"))

    assert stored.answers(decide_request("ls -la"), "01HZKEYB") is False
