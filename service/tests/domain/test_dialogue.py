import statistics
import time

from agentgate.api.schemas import HISTORY_MAX_BYTES
from agentgate.domain.dialogue import Dialogue
from tests.factories import dialogue, turn


def test_empty_dialogue_is_empty_and_has_a_stable_digest():
    assert Dialogue().is_empty and Dialogue.of([]).is_empty
    assert Dialogue().digest() == Dialogue.of([]).digest()
    assert len(Dialogue().digest()) == 64


def test_digest_is_the_same_for_the_same_turns():
    assert dialogue(turn(), turn(role="assistant", author="agent")).digest() == dialogue(
        turn(), turn(role="assistant", author="agent")
    ).digest()


def test_digest_differs_when_any_field_of_any_turn_differs():
    base = dialogue(turn(content="a"), turn(role="toolresult", author="system", content="b", tool="bash", call_id="c1"))
    variants = [
        dialogue(turn(content="A"), base.turns[1]),
        dialogue(base.turns[0], turn(role="toolcall", author="system", content="b", tool="bash", call_id="c1")),
        dialogue(base.turns[0], turn(role="toolresult", author="agent", content="b", tool="bash", call_id="c1")),
        dialogue(base.turns[0], turn(role="toolresult", author="system", content="b", tool="sh", call_id="c1")),
        dialogue(base.turns[0], turn(role="toolresult", author="system", content="b", tool="bash", call_id="c2")),
        dialogue(base.turns[1], base.turns[0]),
    ]
    assert len({base.digest(), *(v.digest() for v in variants)}) == len(variants) + 1


def test_last_human_request_skips_agent_authored_human_turns():
    d = dialogue(
        turn(content="real request"),
        turn(role="human", author="agent", content="subagent instructions"),
        turn(role="assistant", author="agent", content="ok"),
    )
    assert d.last_human_request() == "real request"


def test_last_human_request_is_none_without_a_human_authored_turn():
    assert dialogue(turn(role="human", author="agent", content="x")).last_human_request() is None
    assert Dialogue().last_human_request() is None


def test_digest_of_a_full_size_history_is_well_under_a_millisecond():
    # The digest is taken on every request before the cache lookup, so it is
    # inside the 1 ms budget stage 1 already lives under.
    big = dialogue(*[turn(role="toolresult", author="system", content="x" * 640) for _ in range(200)])
    assert len(big.digest()) == 64
    samples = []
    for _ in range(50):
        t0 = time.perf_counter()
        big.digest()
        samples.append((time.perf_counter() - t0) * 1000)
    assert statistics.median(samples) <= 1.0
    assert 200 * 640 <= HISTORY_MAX_BYTES  # sanity: this really is a wire-legal history
