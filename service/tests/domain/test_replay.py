from agentgate.domain.replay import NO_SESSION, STATIC_PRINCIPAL, Replay, ReplayKey, principal_of
from tests.factories import decide_request, decision


def record(key: str = "k", key_id: str | None = None):
    return decision(idempotency_key=key, key_id=key_id).to_record()


def test_a_key_id_is_its_own_principal():
    assert principal_of("01HZKEYA") == "01HZKEYA"


def test_the_static_token_is_one_named_principal():
    assert principal_of(None) == STATIC_PRINCIPAL == "token"


def test_the_storage_key_joins_principal_session_and_key():
    assert ReplayKey.of("01HZKEYA", "s1", "abc").storage_key() == ReplayKey("01HZKEYA", "s1", "abc").storage_key()


def test_a_call_without_a_session_gets_its_own_namespace():
    assert ReplayKey.of(None, None, "abc").storage_key() == ReplayKey(STATIC_PRINCIPAL, NO_SESSION, "abc").storage_key()
    assert NO_SESSION == "-"


def test_two_sessions_of_one_principal_do_not_share_a_slot():
    a = ReplayKey.of("01HZKEYA", "s1", "abc").storage_key()
    b = ReplayKey.of("01HZKEYA", "s2", "abc").storage_key()

    assert a != b


def test_a_replay_does_not_answer_a_call_from_another_session():
    # No second field on Replay carries the session: the identity digest is
    # the whole request body, and session_id is part of it. This test is what
    # keeps that argument honest.
    stored = Replay.of(decision(idempotency_key="k", request=decide_request("ls -la", session_id="s1")).to_record())

    assert stored.answers(decide_request("ls -la", session_id="s1"), STATIC_PRINCIPAL) is True
    assert stored.answers(decide_request("ls -la", session_id="s2"), STATIC_PRINCIPAL) is False


def test_a_replay_takes_its_principal_from_the_record():
    assert Replay.of(record(key_id="01HZKEYA")).principal == "01HZKEYA"


def test_a_replay_answers_its_own_principal():
    stored = Replay.of(record(key_id="01HZKEYA"))

    assert stored.answers(decide_request("ls -la"), "01HZKEYA") is True


def test_a_replay_does_not_answer_another_principal():
    stored = Replay.of(record(key_id="01HZKEYA"))

    assert stored.answers(decide_request("ls -la"), "01HZKEYB") is False
